"""Tam ekran terminal ön yüzü: sohbet, bağımsız akış ve sabit mesaj editörü."""

import asyncio
from collections.abc import Callable
from contextlib import aclosing, suppress
from datetime import UTC, datetime
from pathlib import Path

import httpx
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Markdown, Static, TextArea
from textual.widgets.markdown import MarkdownStream
from textual.worker import Worker, WorkerCancelled

from harness.cli.commands import ChatSession, is_command
from harness.cli.inspector import code_blocks_summary, seconds, summary_items, write_report
from harness.cli.render import PermissionChoice
from harness.core.agent import Agent
from harness.core.events import (
    CodeBlocksSaved,
    ContextAssembled,
    Event,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    PermissionRequested,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.core.session_log import SessionLogger
from harness.messages import ToolCall


class ComposeInput(TextArea):
    """Enter gönderir; Shift+Enter çok satırlı taslağı korur."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    async def _on_key(self, event: events.Key) -> None:
        if event.key in {"enter", "ctrl+enter"}:
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
        elif event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
        else:
            await super()._on_key(event)


class ChatMessage(Vertical):
    def __init__(self, role: str, source: str = "", *, kind: str = "agent") -> None:
        """kind: user (kullanıcı), agent (model) ya da command (/komut cevabı)."""
        super().__init__(classes=f"message {kind}-message")
        self.role = role
        self.source = source
        self.markdown = Markdown(source, open_links=False)
        self._stream: MarkdownStream | None = None

    def compose(self) -> ComposeResult:
        yield Static(Text(self.role), classes="message-role")
        yield self.markdown

    async def append_delta(self, text: str) -> None:
        self.source += text
        if self._stream is None:
            self._stream = Markdown.get_stream(self.markdown)
        await self._stream.write(text)

    async def finish(self, source: str) -> None:
        await self.close_stream()
        self.source = source
        await self.markdown.update(source)

    async def close_stream(self) -> None:
        if self._stream is not None:
            stream, self._stream = self._stream, None
            await stream.stop()

    async def on_unmount(self) -> None:
        await self.close_stream()


class PermissionDialog(ModalScreen[PermissionChoice]):
    """İzin katmanının "sor" dediği araç çağrısını gösterir; sonuç izin ver/reddet/oturum boyu."""

    def __init__(self, call: ToolCall) -> None:
        super().__init__()
        self._call = call

    def compose(self) -> ComposeResult:
        with Vertical(id="permission-dialog"):
            yield Static(Text(f"İzin isteniyor: {self._call.name}"), id="permission-title")
            yield Static(Text(self._call.arguments), id="permission-args")
            with Horizontal(id="permission-actions"):
                yield Button("Reddet", id="deny")
                yield Button("Oturum boyu izin ver", id="always")
                yield Button("İzin ver", id="allow", variant="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "allow":
                self.dismiss("allow")
            case "always":
                self.dismiss("always")
            case _:
                self.dismiss("deny")


class HarnessApp(App[None]):
    TITLE = "Harness"
    CSS_PATH = "tui.tcss"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Çıkış", priority=True),
        Binding("escape", "stop", "Durdur", priority=True),
        Binding("ctrl+end", "latest", "En alta", priority=True),
        Binding("pageup", "page_up", show=False, priority=True),
        Binding("pagedown", "page_down", show=False, priority=True),
        Binding("ctrl+t", "toggle_theme", "Tema", priority=True),
    ]

    def __init__(self, session: ChatSession, *, initial_task: str | None = None) -> None:
        super().__init__()
        self.session = session
        self.initial_task = initial_task
        self.busy = False
        self.failed = False
        self.report: Path | None = None
        self._turn: Worker[None] | None = None
        self._reply: ChatMessage | None = None
        self._turn_number = 0
        self._active_question: str | None = None
        self._active_events: list[Event] = []
        self.register_theme(
            Theme(
                name="harness-dark",
                # Minimal Clarity OKLCH tokenlarının terminal için sRGB karşılıkları.
                primary="#6895f4",
                secondary="#6895f4",
                accent="#6895f4",
                foreground="#ebeff5",
                background="#0f1216",
                surface="#171b20",
                panel="#171b20",
                variables={"text-muted": "#9199a5", "border": "#2a2e34"},
                dark=True,
            )
        )
        self.register_theme(
            Theme(
                name="harness-light",
                primary="#436ed1",
                secondary="#436ed1",
                accent="#4874d8",
                foreground="#11161f",
                background="#fcfcfc",
                surface="#f8f8fa",
                panel="#f8f8fa",
                variables={"text-muted": "#6b727e", "border": "#e3e5e7"},
                dark=False,
            )
        )
        self.theme = "harness-dark"

    # Model, ajan ve kayıt /komutlarla değişebilir; her seferinde oturumdan okunur.
    @property
    def agent(self) -> Agent:
        return self.session.agent

    @property
    def session_log(self) -> SessionLogger:
        return self.session.log

    @property
    def model_label(self) -> str:
        return self.session.label

    def topbar_text(self) -> Text:
        return Text(
            f" HARNESS  /  {self.model_label}  /  {self.session.root}",
            no_wrap=True,
            overflow="ellipsis",
        )

    def compose(self) -> ComposeResult:
        yield Static(self.topbar_text(), id="topbar")
        with Horizontal(id="body"):
            with Vertical(id="chat-pane"):
                with Horizontal(id="chat-heading"):
                    yield Static("SOHBET", id="chat-title")
                    yield Button("↓ En alta", id="latest", variant="default")
                with VerticalScroll(id="conversation", can_focus=True):
                    yield Static(
                        Text(
                            "Birlikte ne yapalım?\n\n"
                            "Mesajını aşağıya yaz. Yanıtlar burada akar; "
                            "işlem durumu sağda kalır.\n"
                            "Yukarı kaydırdığında okuduğun yer korunur.\n"
                            "/help: model, effort ve oturum komutları."
                        ),
                        id="welcome",
                    )
            with Vertical(id="flow-pane"):
                yield Static("AKIŞ", id="flow-title")
                with VerticalScroll(id="flow", can_focus=True):
                    yield Static(Text("Hazır.\nHenüz görev yok."), id="flow-empty")
        with Vertical(id="composer"):
            yield ComposeInput(
                id="message-input",
                placeholder="Mesajını yaz…",
                soft_wrap=True,
                show_line_numbers=False,
                highlight_cursor_line=False,
            )
            with Horizontal(id="actions"):
                yield Static("Hazır · Enter gönder", id="status")
                yield Button("Rapor", id="inspect", disabled=True)
                yield Button("Durdur", id="stop", disabled=True)
                yield Button("Gönder", id="send", variant="primary")
        yield Static(
            " Shift+Enter: satır · Esc: durdur · Ctrl+End: en alta · Ctrl+T: tema · Ctrl+Q: çık",
            id="keys",
        )

    async def on_mount(self) -> None:
        self.query_one("#conversation", VerticalScroll).anchor()
        self.query_one("#flow", VerticalScroll).anchor()
        self.query_one(ComposeInput).focus()
        if self.initial_task:
            await self.submit(self.initial_task)

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.height < 25, "compact")

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(Text(text))

    async def add_flow(self, text: str) -> None:
        await self.query_one("#flow", VerticalScroll).mount(
            Static(Text(text), classes="flow-entry")
        )

    @on(ComposeInput.Submitted)
    async def input_submitted(self, event: ComposeInput.Submitted) -> None:
        await self.submit(event.text)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "send":
                await self.submit(self.query_one(ComposeInput).text)
            case "stop":
                await self.action_stop()
            case "latest":
                self.action_latest()
            case "inspect":
                self.action_inspect()
            case _:
                pass

    async def submit(self, text: str) -> None:
        question = text.strip()
        if not question:
            return
        editor = self.query_one(ComposeInput)
        if is_command(question):
            await self.run_command(question)
            return
        if self.busy:
            self.set_status("Görev sürüyor; taslağın korunuyor.")
            return
        if self.failed:
            self.set_status("Bu oturum kesildi · /new ya da /resume ile devam et")
            return
        self.busy = True
        self._active_question = question
        self._active_events = []
        self._turn_number += 1
        self.query_one("#send", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        editor.clear()
        editor.focus()
        self.query_one("#welcome").display = False
        self.query_one("#flow-empty").display = False
        # Çizimi beklemeden worker'ı kaydet; durdurma önceki turu hedeflemesin.
        user_mounted = self.query_one("#conversation", VerticalScroll).mount(
            ChatMessage("Sen", question, kind="user")
        )

        async def turn() -> None:
            await user_mounted
            await self.consume(question)

        self._turn = self.run_worker(
            turn,
            name="agent-turn",
            group="agent",
            exit_on_error=False,
        )

    async def run_command(self, text: str) -> None:
        name = text.split()[0].lower()
        if self.busy and name not in {"/help", "/sessions", "/inspect", "/exit"}:
            self.set_status("Görev sürüyor; komut için bitmesini bekle ya da Esc.")
            return
        self.query_one(ComposeInput).clear()
        self.set_status("Komut çalışıyor…")
        reply = await self.session.run(text)
        if reply.exit:
            await self.action_quit()
            return
        if reply.inspect:
            self.action_inspect()
            return
        if reply.reset:
            await self.reset_conversation(reply.transcript)
        if reply.text:
            self.query_one("#welcome").display = False
            await self.query_one("#conversation", VerticalScroll).mount(
                ChatMessage("Harness", reply.text, kind="command")
            )
        self.query_one("#topbar", Static).update(self.topbar_text())
        self.set_status("Görev sürüyor…" if self.busy else "Hazır · Enter gönder")

    async def reset_conversation(self, transcript: tuple[tuple[str, str], ...]) -> None:
        """Yeni ya da devam edilen oturum: ekran o oturumun konuşmasını gösterir."""
        await self.query(ChatMessage).remove()
        await self.query(".flow-entry").remove()
        conversation = self.query_one("#conversation", VerticalScroll)
        for role, text in transcript:
            kind = "user" if role == "Sen" else "agent"
            await conversation.mount(ChatMessage(role, text, kind=kind))
        self.query_one("#welcome").display = not transcript
        self.query_one("#flow-empty").display = True
        self._turn_number = sum(role == "Sen" for role, _ in transcript)
        self.failed = False
        self.report = None
        self.query_one("#inspect", Button).disabled = True
        self.query_one("#send", Button).disabled = False

    async def consume(self, question: str) -> None:
        events_seen = self._active_events
        finished = False
        self._reply = None
        try:
            async with aclosing(self.agent.run(question)) as stream:
                send: bool | None = None
                while True:
                    try:
                        event = await stream.asend(send)
                    except StopAsyncIteration:
                        break
                    send = None
                    self.session_log.write(event)
                    events_seen.append(event)
                    match event:
                        case TaskStarted():
                            await self.add_flow(f"TUR {self._turn_number}")
                        case ModelStarted(step=step):
                            self._reply = None
                            self.set_status("Model bekleniyor…")
                            await self.add_flow(f"● Model · adım {step}")
                        case ModelTextDelta(text=text):
                            if self._reply is None:
                                self._reply = ChatMessage("Ajan")
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    self._reply
                                )
                            self.set_status("Yanıt geliyor…")
                            await self._reply.append_delta(text)
                        case ModelReplied(text=text, duration_seconds=duration):
                            if self._reply is None and text:
                                self._reply = ChatMessage("Ajan")
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    self._reply
                                )
                            if self._reply is not None:
                                await self._reply.finish(text)
                            await self.add_flow(f"✓ Model · {seconds(duration)}")
                        case PermissionRequested(call=call):
                            self.set_status("İzin bekleniyor…")
                            await self.add_flow(f"? izin isteniyor · {call.name}")
                            choice = await self.push_screen_wait(PermissionDialog(call))
                            if choice == "always":
                                self.agent.permissions.grant(call.name)
                            send = choice != "deny"
                        case ToolStarted(call=call):
                            self.set_status("Araç çalışıyor…")
                            await self.add_flow(f"● {call.name}")
                        case ToolFinished(call=call, result=result, duration_seconds=duration):
                            mark = "!" if result.is_error else "✓"
                            await self.add_flow(f"{mark} {call.name}\n{seconds(duration)}")
                        case CodeBlocksSaved():
                            await self.add_flow(f"✓ {code_blocks_summary(event)}")
                        case TaskFinished(reason=reason):
                            finished = True
                            if reason in {"cancelled", "error"}:
                                self.failed = True
                            await self.add_flow(
                                "\n".join(
                                    f"{label}: {value}"
                                    for label, value in summary_items(events_seen)
                                )
                            )
                            self.set_status(
                                {
                                    "done": "Tamamlandı · Enter ile yeni mesaj",
                                    "step_limit": "Adım sınırı · yeni mesaj yazabilirsin",
                                    "cancelled": "Durduruldu · /new ya da /resume ile devam et",
                                    "error": "Hata · ayrıntılar kayıtta",
                                }[reason]
                            )
                        case ContextAssembled():
                            pass  # Bağlam terminale sığmaz; kayıtta ve HTML incelemede durur.
        except asyncio.CancelledError:
            if not finished:
                self._record_cancelled_turn(question, events_seen)
                self.failed = True
                self.set_status("Durduruldu · /new ya da /resume ile devam et")
            raise
        except Exception:
            self.failed = True
            self.set_status("Hata · ayrıntılar kayıtta")
            if not finished:
                await self.add_flow("! Görev tamamlanamadı; kayıt korundu.")
        finally:
            if self._reply is not None:
                await self._reply.close_stream()
                if self.failed:
                    self._reply.query_one(".message-role", Static).update(
                        Text("Ajan · yanıt tamamlanmadı")
                    )
            if events_seen:
                try:
                    self.report = write_report(self.session_log.path)
                    self.query_one("#inspect", Button).disabled = False
                except (OSError, ValueError):
                    await self.add_flow("! Rapor oluşturulamadı; JSONL kaydına bak.")
            self.busy = False
            self.query_one("#send", Button).disabled = self.failed
            self.query_one("#stop", Button).disabled = True
            if self._active_question == question:
                self._active_question = None
                self._active_events = []

    def _record_cancelled_turn(self, question: str, events_seen: list[Event]) -> None:
        if not any(isinstance(event, TaskStarted) for event in events_seen):
            started = TaskStarted(
                task=question,
                model=self.model_label,
                timestamp=datetime.now(UTC),
            )
            self.session_log.write(started)
            events_seen.append(started)
        if any(isinstance(event, TaskFinished) for event in events_seen):
            return
        steps = max(
            (event.step for event in events_seen if isinstance(event, ModelStarted)),
            default=0,
        )
        finished = TaskFinished(
            reason="cancelled",
            steps=steps,
            timestamp=datetime.now(UTC),
        )
        self.session_log.write(finished)
        events_seen.append(finished)

    async def wait_turn(self) -> None:
        if self._turn is not None:
            with suppress(WorkerCancelled):
                await self._turn.wait()

    async def action_stop(self) -> None:
        if self.busy and self._turn is not None:
            self._turn.cancel()
            await self.wait_turn()
            # Başlamadan iptal edilen coroutine consume() finally bloğuna giremez.
            if self.busy:
                if self._active_question is not None:
                    self._record_cancelled_turn(self._active_question, self._active_events)
                    try:
                        self.report = write_report(self.session_log.path)
                        self.query_one("#inspect", Button).disabled = False
                    except (OSError, ValueError):
                        await self.add_flow("! Rapor oluşturulamadı; JSONL kaydına bak.")
                    self._active_question = None
                    self._active_events = []
                self.busy = False
                self.failed = True
                self.set_status("Durduruldu · /new ya da /resume ile devam et")
                self.query_one("#send", Button).disabled = True
                self.query_one("#stop", Button).disabled = True

    async def action_quit(self) -> None:
        await self.action_stop()
        self.exit()

    def action_latest(self) -> None:
        self.query_one("#conversation", VerticalScroll).scroll_end(animate=False, immediate=True)

    def page_target(self) -> VerticalScroll:
        flow = self.query_one("#flow", VerticalScroll)
        return (
            flow
            if self.focused is flow or (self.focused is not None and flow in self.focused.ancestors)
            else self.query_one("#conversation", VerticalScroll)
        )

    def action_page_up(self) -> None:
        self.page_target().scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self.page_target().scroll_page_down(animate=False)

    def action_toggle_theme(self) -> None:
        self.theme = "harness-light" if self.theme == "harness-dark" else "harness-dark"

    def action_inspect(self) -> None:
        if self.report is not None:
            self.open_url(self.report.resolve().as_uri())
        else:
            self.set_status("Henüz inceleme raporu yok.")


def run_tui(make_session: Callable[[httpx.AsyncClient], ChatSession]) -> Path:
    """Tam ekran sohbeti açar; kapanınca son kullanılan kayıt dosyasının yolunu döner."""

    async def run() -> Path:
        async with httpx.AsyncClient(timeout=120) as http:
            session = make_session(http)
            await HarnessApp(session).run_async()
            return session.log.path

    return asyncio.run(run())
