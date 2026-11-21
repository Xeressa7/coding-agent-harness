"""Tam ekran terminal ön yüzü: sohbet, bağımsız akış, sabit editör ve seçim pencereleri."""

import asyncio
import json
from collections.abc import Callable
from contextlib import aclosing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.markdown import MarkdownStream
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerCancelled

from harness.cli.commands import (
    STEP_LIMIT_HINT,
    ChatSession,
    Choice,
    CommandReply,
    KeyRequest,
    Picker,
    is_command,
    matching_commands,
)
from harness.cli.inspector import (
    code_blocks_summary,
    intervention_summary,
    seconds,
    summary_items,
    write_report,
)
from harness.cli.render import PermissionChoice, live_outcome, plan_lines
from harness.cli.themes import available, textual_theme
from harness.core.agent import Agent
from harness.core.events import (
    CodeBlocksSaved,
    ContextAssembled,
    Event,
    HarnessIntervened,
    ModelReasoningDelta,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    PermissionRequested,
    QuestionAsked,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.core.session_log import SessionLogger
from harness.messages import ToolCall
from harness.tools.ask_user import pick_answer

# İş sürerken de çalışabilen, yalnız bilgi veren komutlar.
READ_ONLY_COMMANDS = {
    "/help",
    "/inspect",
    "/exit",
    "/quit",
    "/status",
    "/tools",
    "/usage",
    "/cost",
    "/watch",  # panel kaydı okur; koşu sürerken açmak asıl kullanımıdır
    "/theme",
}


class ComposeInput(TextArea):
    """Enter gönderir; Shift+Enter çok satırlı taslağı korur."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Complete(Message):
        """Tab: yazılmakta olan /komut ilk öneriyle tamamlansın."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "tab" and self.text.startswith("/") and " " not in self.text:
            event.stop()
            event.prevent_default()
            self.post_message(self.Complete())
        elif event.key in {"enter", "ctrl+enter"}:
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


class ThoughtBlock(Collapsible):
    """Sağlayıcının gösterdiği düşünce (özet ya da açık düşünce); cevabın üstünde, soluk.

    Açık başlar ki model düşünürken ne yaptığı görülsün; başlığa tıklayınca katlanır.
    """

    def __init__(self, text: str = "") -> None:
        self.source = text
        self._body = Static(Text(text), classes="thought-text")
        super().__init__(self._body, title="Düşünce", collapsed=False, classes="thought")

    def append(self, text: str) -> None:
        self.source += text
        self._body.update(Text(self.source))


def permission_text(arguments: str) -> str:
    """Argümanı okunur kıl: JSON nesnesiyse "anahtar: değer", metinler gerçek satırlarıyla."""
    try:
        data: object = json.loads(arguments)
    except ValueError:
        return arguments
    if not isinstance(data, dict):
        return arguments
    parts: list[str] = []
    fields = cast(dict[str, object], data)
    for key, value in fields.items():
        shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parts.append(f"{key}:\n{shown}" if "\n" in shown else f"{key}: {shown}")
    return "\n".join(parts)


class PermissionDialog(ModalScreen[PermissionChoice]):
    """İzin katmanının "sor" dediği araç çağrısını gösterir; sonuç izin ver/reddet/oturum boyu.

    Uzun argüman kendi kutusunda kayar, düğmeler hep görünür kalır; klavye: Enter/e izin ver,
    o oturum boyu, Esc/r reddet.
    """

    BINDINGS = [
        Binding("e", "choose('allow')", "İzin ver"),
        Binding("o", "choose('always')", "Oturum boyu"),
        Binding("r", "choose('deny')", "Reddet"),
    ]

    def __init__(self, call: ToolCall, summary: str = "") -> None:
        super().__init__()
        self._call = call
        self._summary = summary

    def compose(self) -> ComposeResult:
        title = f"İzin isteniyor: {self._call.name}" + (
            f" · {self._summary}" if self._summary else ""
        )
        with Vertical(id="permission-dialog"):
            yield Static(Text(title), id="permission-title")
            with Horizontal(id="permission-actions"):
                yield Button("Reddet (r)", id="deny")
                yield Button("Oturum boyu izin ver (o)", id="always")
                yield Button("İzin ver (e)", id="allow", variant="primary")
            yield Static(
                "Enter: odaktaki düğme · ↑↓ / PgUp PgDn: içeriği kaydır · Esc: reddet",
                id="permission-keys",
            )
            with VerticalScroll(id="permission-scroll"):
                yield Static(Text(permission_text(self._call.arguments)), id="permission-args")

    def on_mount(self) -> None:
        self.query_one("#allow", Button).focus()
        self.call_after_refresh(self.fit_content)

    def on_resize(self) -> None:
        self.call_after_refresh(self.fit_content)

    def fit_content(self) -> None:
        """İçerik kutusu, pencere ekranın %90'ını aşmayacak kadar yükseklik alır."""
        title = self.query_one("#permission-title", Static).outer_size.height
        rest = sum(
            self.query_one(f"#{name}").outer_size.height
            for name in ("permission-actions", "permission-keys")
        )
        room = int(self.size.height * 0.9) - 2 - title - rest  # 2: pencere çerçevesi
        self.query_one("#permission-scroll", VerticalScroll).styles.max_height = max(1, room)

    def on_key(self, event: events.Key) -> None:
        # Odak düğmedeyken de oklar içeriği kaydırır (PgUp/PgDn ve Esc uygulama bağlarından gelir).
        scroll = self.query_one("#permission-scroll", VerticalScroll)
        moves = {"up": scroll.scroll_up, "down": scroll.scroll_down}
        if event.key in moves:
            moves[event.key](animate=False)
            event.stop()

    def action_choose(self, choice: PermissionChoice) -> None:
        self.dismiss(choice)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "allow":
                self.dismiss("allow")
            case "always":
                self.dismiss("always")
            case _:
                self.dismiss("deny")


class PickerScreen(ModalScreen[Choice | None]):
    """Aranabilir seçim listesi: yazdıkça süzülür, ↑/↓ gezer, Enter seçer, Esc vazgeçer."""

    def __init__(self, picker: Picker) -> None:
        super().__init__()
        self.picker = picker
        self.shown: list[Choice | None] = []  # listedeki sıraya göre satır; None = grup başlığı

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text(self.picker.title), classes="dialog-title")
            yield Input(placeholder="Ara…", id="picker-search")
            yield OptionList(id="picker-options")
            yield Static("↑↓ gez · Enter seç · Esc vazgeç", classes="dialog-keys")

    def on_mount(self) -> None:
        self.refill("")
        self.query_one(Input).focus()

    def refill(self, query: str) -> None:
        words = query.casefold().split()
        options: list[Option] = []
        self.shown = []
        group = ""
        current: int | None = None
        for choice in self.picker.choices:
            haystack = f"{choice.group} {choice.label} {choice.detail}".casefold()
            if not all(word in haystack for word in words):
                continue
            if choice.group and choice.group != group:
                group = choice.group
                options.append(Option(Text(group, style="bold"), disabled=True))
                self.shown.append(None)
            line = Text(("● " if choice.current else "  ") + choice.label)
            if choice.detail:
                line.append(f"  {choice.detail}", style="dim")
            if choice.current and current is None:
                current = len(options)
            options.append(Option(line, disabled=not choice.enabled))
            self.shown.append(choice)
        listing = self.query_one(OptionList)
        listing.clear_options()
        listing.add_options(options)
        selectable = [i for i, c in enumerate(self.shown) if c is not None and c.enabled]
        if selectable:
            # Arama yokken imleç şu anki seçimde; arama varsa ilk eşleşmede.
            listing.highlighted = current if current in selectable and not words else selectable[0]

    def on_input_changed(self, event: Input.Changed) -> None:
        self.refill(event.value)

    def on_key(self, event: events.Key) -> None:
        # Odak arama kutusundayken de ok tuşları listeyi gezer.
        listing = self.query_one(OptionList)
        if event.key == "down":
            listing.action_cursor_down()
            event.stop()
        elif event.key == "up":
            listing.action_cursor_up()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.select(self.query_one(OptionList).highlighted)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.select(event.option_index)

    def select(self, index: int | None) -> None:
        choice = self.shown[index] if index is not None and index < len(self.shown) else None
        if choice is not None and choice.enabled:
            self.dismiss(choice)


class InfoScreen(ModalScreen[None]):
    """Bilgi komutlarının (/help, /status, /usage, /diff…) penceresi: sohbete yazılmaz."""

    BINDINGS = [Binding("escape", "dismiss", "Kapat"), Binding("q", "dismiss", show=False)]

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.title_text = title
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog info-dialog"):
            yield Static(Text(self.title_text), classes="dialog-title")
            with VerticalScroll(id="info-body"):
                yield Markdown(self.text, open_links=False)
            yield Static("Esc: kapat · ↑↓ / PgUp PgDn: kaydır", classes="dialog-keys")

    def on_mount(self) -> None:
        self.query_one("#info-body", VerticalScroll).focus()

    async def action_dismiss(self, result: None = None) -> None:
        self.dismiss(result)


def note_text(markdown: str) -> str:
    """Bildirim için düz metin: Markdown işaretleri atılır (bildirim Markdown çizmez)."""
    return markdown.replace("**", "").replace("`", "")


class KeyScreen(ModalScreen[str | None]):
    """API anahtarı için gizli alan; yazılan ekranda görünmez."""

    def __init__(self, request: KeyRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text(f"{self.request.provider} bağla"), classes="dialog-title")
            yield Static(Text(self.request.prompt), classes="dialog-text")
            yield Input(placeholder="API anahtarı", password=True, id="key-input")
            yield Static("Enter kaydet · Esc vazgeç", classes="dialog-keys")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip() or None)


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

    def __init__(
        self,
        session: ChatSession,
        *,
        initial_task: str | None = None,
        initial_reply: CommandReply | None = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.initial_task = initial_task
        self.initial_reply = initial_reply
        self.busy = False
        self.failed = False
        self.report: Path | None = None
        self._turn: Worker[None] | None = None
        self._reply: ChatMessage | None = None
        self._thought: ThoughtBlock | None = None
        self._turn_number = 0
        self._active_question: str | None = None
        self._active_events: list[Event] = []
        self._suggesting = False  # durum satırı şu an komut önerisi gösteriyor
        self._ask_options: tuple[str, ...] = ()  # bekleyen ask_user sorusunun seçenekleri
        self._ask_answer: asyncio.Future[str | None] | None = None
        for palette in available().values():
            self.register_theme(textual_theme(palette))
        self.theme = f"harness-{session.theme}"

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
                            "/ yazıp Enter: komut listesi · /sessions: geçmiş oturumlar · "
                            "/model: model seç"
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
        if self.initial_reply is not None:  # harness -c / -r: önceki oturum ya da seçim listesi
            await self.handle_reply(self.initial_reply)
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
                await self.action_inspect()
            case _:
                pass

    def answer_question(self, typed: str) -> None:
        """Bekleyen ask_user sorusunu çözer: boş cevap yok, seçenek numarası o seçenek."""
        future = self._ask_answer
        if future is None or future.done():
            return
        self.query_one(ComposeInput).clear()
        future.set_result(pick_answer(typed, self._ask_options))

    async def ask_inline(self, question: str, options: tuple[str, ...]) -> str | None:
        """Soru konuşmaya mesaj olarak düşer; cevap alt editörden gelir (ayrı pencere yok).

        Soru beklenirken Enter/Gönder cevabı yollar, bilgi komutları çalışır, Esc turu
        durdurur. Cevap soru mesajının altına işlenir; sohbet bütünsel kalır.
        """
        hint = (
            "Numara ya da kendi cevabın · boş Enter: cevap yok"
            if options
            else "Cevabın · boş Enter: cevap yok"
        )
        body = question
        if options:
            body += "\n\n" + "\n".join(f"{i}. {option}" for i, option in enumerate(options, 1))
        body += f"\n\n*{hint}*"
        message = ChatMessage("Ajan soruyor", body, kind="agent")
        await self.query_one("#conversation", VerticalScroll).mount(message)
        answer_future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._ask_options = options
        self._ask_answer = answer_future
        self.query_one("#send", Button).disabled = False  # Gönder düğmesi de cevabı yollar
        self.query_one(ComposeInput).focus()
        # Durum ancak cevap yolu hazırken "bekleniyor" der; önce yazılırsa erken Enter boşa gider.
        self.set_status("Cevabın bekleniyor… · boş Enter: cevap yok")
        try:
            answer = await answer_future
        finally:
            self._ask_answer = None
            self._ask_options = ()
        suffix = f"\n\n**Cevap:** {answer}" if answer is not None else "\n\n*Cevap yok*"
        await message.finish(body + suffix)
        self.query_one("#send", Button).disabled = True  # iş bitene dek yine kapalı
        return answer

    async def submit(self, text: str) -> None:
        if self._ask_answer is not None:
            typed = text.strip()
            if is_command(typed):
                # Soru beklerken de bilgi komutları çalışır; öbürleri busy korumasına takılır.
                await self.run_command(typed)
                return
            self.answer_question(text)
            return
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
        if self.busy and name not in READ_ONLY_COMMANDS:
            self.set_status("Görev sürüyor; komut için bitmesini bekle ya da Esc.")
            return
        self.query_one(ComposeInput).clear()
        self.set_status("Komut çalışıyor…")
        reply = await self.session.run(text)
        if reply.exit:
            await self.action_quit()
            return
        if reply.inspect:
            await self.action_inspect()
            return
        await self.handle_reply(reply)

    async def handle_reply(self, reply: CommandReply) -> None:
        """Cevabı gösterir; sıradaki adım varsa (liste, anahtar, giriş) onu açar."""
        if reply.theme is not None:
            self.theme = f"harness-{reply.theme}"
        await self.show_reply(reply)
        if reply.picker is not None:
            picker = reply.picker

            async def picked(choice: Choice | None) -> None:
                if choice is not None:
                    kind = choice.kind or picker.kind
                    await self.handle_reply(await self.session.choose(kind, choice.value))

            await self.push_screen(PickerScreen(picker), picked)
        elif reply.key is not None:
            request = reply.key

            async def entered(key: str | None) -> None:
                if key is None:
                    self.set_status("Vazgeçildi · anahtar kaydedilmedi")
                else:
                    await self.handle_reply(await self.session.save_key(request.provider, key))

            await self.push_screen(KeyScreen(request), entered)
        elif reply.login:
            self.set_status("Tarayıcıda ChatGPT girişi bekleniyor…")
            self.run_worker(self.login(), group="login", exclusive=True, exit_on_error=False)

    async def login(self) -> None:
        def show_url(url: str) -> None:
            text = f"Tarayıcı açılamadı; bu adresi tarayıcıya yapıştır:\n\n{url}"
            message = ChatMessage("Harness", text, kind="command")
            try:  # giriş ayrı iş parçacığında bekler
                self.call_from_thread(self.query_one("#conversation").mount, message)
            except RuntimeError:
                self.query_one("#conversation").mount(message)

        await self.handle_reply(await self.session.login(show_url))

    async def show_reply(self, reply: CommandReply) -> None:
        """Komut cevabı sohbeti kirletmez: bilgi pencerede, onay kısa bildirimde çıkar."""
        if reply.reset:
            await self.reset_conversation(reply.transcript)
        if reply.clipboard is not None:
            self.copy_to_clipboard(reply.clipboard)
        if reply.text:
            if reply.view == "chat":
                self.query_one("#welcome").display = False
                await self.query_one("#conversation", VerticalScroll).mount(
                    ChatMessage("Harness", reply.text, kind="command")
                )
            elif reply.view == "panel":
                await self.push_screen(InfoScreen(reply.title or "Harness", reply.text))
            else:
                self.notify(note_text(reply.text), title="Harness", timeout=8, markup=False)
        self.query_one("#topbar", Static).update(self.topbar_text())
        self.set_status("Görev sürüyor…" if self.busy else "Hazır · Enter gönder")

    @on(TextArea.Changed, "#message-input")
    def suggest_commands(self, event: TextArea.Changed) -> None:
        """`/` ile başlayan taslakta eşleşen komutlar durum satırında; Tab ilkini tamamlar."""
        text = event.text_area.text
        if not text.startswith("/") or " " in text or "\n" in text:
            if self._suggesting:
                self._suggesting = False
                self.set_status("Görev sürüyor…" if self.busy else "Hazır · Enter gönder")
            return
        found = matching_commands(text)
        self._suggesting = True
        if text == "/":
            self.set_status("Enter: komut listesi · ya da yazmaya devam et")
        elif found:
            shown = " · ".join(f"{c.name} {c.summary.split(';')[0]}" for c in found[:3])
            self.set_status(f"Tab ↹ {shown}")
        else:
            self.set_status(f"{text}: böyle bir komut yok · / Enter: liste")

    @on(ComposeInput.Complete)
    def complete_command(self) -> None:
        editor = self.query_one(ComposeInput)
        found = matching_commands(editor.text)
        if found:
            editor.text = found[0].name + " "
            editor.move_cursor(editor.document.end)

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
                send: bool | str | None = None
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
                            self._thought = None
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
                        case ModelReasoningDelta(text=text):
                            if self._thought is None:
                                self._thought = ThoughtBlock()
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    self._thought, before=self._reply
                                )
                            self.set_status("Düşünüyor…")
                            self._thought.append(text)
                        case ModelReplied(
                            text=text, duration_seconds=duration, reasoning=reasoning
                        ):
                            if self._thought is None and reasoning:
                                # Akış vermeyen adaptör: özet tamamlanmış yanıtla gelir.
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    ThoughtBlock(reasoning), before=self._reply
                                )
                            self._thought = None
                            if self._reply is None and text:
                                self._reply = ChatMessage("Ajan")
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    self._reply
                                )
                            if self._reply is not None:
                                await self._reply.finish(text)
                            await self.add_flow(f"✓ Model · {seconds(duration)}")
                        case PermissionRequested(call=call, summary=summary):
                            self.set_status("İzin bekleniyor…")
                            await self.add_flow(f"? izin isteniyor · {call.name}")
                            # Karar argümana göre verilir; ayrıntı yalnız pencerede görünür.
                            choice = await self.push_screen_wait(PermissionDialog(call, summary))
                            if choice == "always":
                                self.agent.permissions.grant(call.name)
                            send = choice != "deny"
                        case QuestionAsked(question=question, options=options):
                            await self.add_flow(f"? ajan soruyor · {question}")
                            send = await self.ask_inline(question, options)
                        case ToolStarted(call=call):
                            self.set_status(f"{call.name} çalışıyor…")
                            await self.add_flow(f"● {call.name}")
                        case ToolFinished(
                            call=call,
                            result=result,
                            duration_seconds=duration,
                            details=details,
                        ):
                            # Yol, komut ve çıktı akışta görünmez (rapordadır); ne olduğu görünür.
                            await self.add_flow(f"{live_outcome(event)}\n  {seconds(duration)}")
                            if call.name == "update_plan" and not result.is_error:
                                await self.add_flow("PLAN\n" + "\n".join(plan_lines(details)))
                        case CodeBlocksSaved():
                            await self.add_flow(f"✓ {code_blocks_summary(event)}")
                        case HarnessIntervened():
                            await self.add_flow(f"⚑ harness · {intervention_summary(event)}")
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
                                    "step_limit": STEP_LIMIT_HINT,
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
                # Rapor burada üretilmez: büyük kayıtta saniyeler sürer; "İncele" deyince yazılır.
                self.query_one("#inspect", Button).disabled = False
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
        if isinstance(self.screen, PermissionDialog):
            self.screen.dismiss("deny")  # Esc izin penceresinde "reddet" demektir
            return
        if isinstance(self.screen, (PickerScreen, KeyScreen, InfoScreen)):
            self.screen.dismiss(None)
            return
        if self.busy and self._turn is not None:
            self._turn.cancel()
            await self.wait_turn()
            # Başlamadan iptal edilen coroutine consume() finally bloğuna giremez.
            if self.busy:
                if self._active_question is not None:
                    self._record_cancelled_turn(self._active_question, self._active_events)
                    self.query_one("#inspect", Button).disabled = False
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
        if isinstance(self.screen, PermissionDialog):
            return self.screen.query_one("#permission-scroll", VerticalScroll)
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
        self.theme = f"harness-{self.session.cycle_theme()}"

    async def build_report(self) -> Path | None:
        """Kayıttan HTML incelemeyi o an üretir (arayüzü dondurmamak için ayrı iş parçacığında)."""
        path = self.session_log.path
        if not path.is_file() or path.stat().st_size == 0:
            self.set_status("Henüz inceleme raporu yok.")
            return None
        self.set_status("İnceleme hazırlanıyor…")
        try:
            self.report = await asyncio.to_thread(write_report, path)
        except (OSError, ValueError):
            await self.add_flow("! Rapor oluşturulamadı; JSONL kaydına bak.")
            return None
        self.set_status(f"İnceleme: {self.report}")
        return self.report

    async def action_inspect(self) -> None:
        report = await self.build_report()
        if report is not None:
            self.open_url(report.resolve().as_uri())


def run_tui(
    make_session: Callable[[httpx.AsyncClient], ChatSession],
    *,
    resume: bool = False,
    pick: bool = False,
) -> Path:
    """Tam ekran sohbeti açar; kapanınca son kullanılan kayıt dosyasının yolunu döner."""

    async def run() -> Path:
        async with httpx.AsyncClient(timeout=120) as http:
            session = make_session(http)
            reply = (
                session.resume_latest()
                if resume
                else await session.run("/resume")
                if pick
                else None
            )
            await HarnessApp(session, initial_reply=reply).run_async()
            return session.log.path

    return asyncio.run(run())
