"""Gerçek Textual yerleşimi: sabit editör, bağımsız kaydırma ve async ajan olayları."""

import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Sequence
from pathlib import Path

import pytest
from textual import events
from textual.containers import VerticalScroll
from textual.events import MouseScrollDown
from textual.pilot import Pilot
from textual.widgets import Button, Input, OptionList, Static

from harness.cli.commands import ChatSession
from harness.cli.inspector import write_report
from harness.cli.render import PermissionChoice
from harness.cli.tui import (
    ChatMessage,
    ComposeInput,
    HarnessApp,
    InfoScreen,
    KeyScreen,
    PermissionDialog,
    PickerScreen,
    ThoughtBlock,
    load_keymap,
)
from harness.core.anatomy import get_preset
from harness.core.events import TaskFinished, TaskStarted
from harness.core.session_log import read_session
from harness.environments.local import find_bash
from harness.llm.base import Effort, ModelInfo
from harness.llm.credentials import load_keys
from harness.llm.providers import Provider, get_provider
from harness.llm.scripted import ScriptedLLM
from harness.messages import (
    AssistantMessage,
    Message,
    ReasoningDelta,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolSpec,
    UserMessage,
)

pytestmark = pytest.mark.anyio


def make_app(model: object, tmp_path: Path, anatomy: str = "files") -> HarnessApp:
    # Concrete LLM doubles below satisfy the shared complete/stream protocols.
    assert isinstance(model, (ScriptedLLM, ControlledModel, ThinkingModel))

    async def no_models(provider: Provider) -> list[ModelInfo]:
        raise RuntimeError("testte model listesi yok")

    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset(anatomy),
        provider=get_provider("chatgpt"),
        model="test-model",
        effort=None,
        make_llm=lambda provider, name, effort: model,
        load_models=no_models,
        load_prompt=lambda anatomy: "talimat",
        sessions_dir=tmp_path / "logs",
    )
    return HarnessApp(session)


class ControlledModel:
    def __init__(self, first: str = "İlk parça") -> None:
        self.first = first
        self.entered = asyncio.Event()
        self.pieces: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False
        self.calls = 0

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        raise AssertionError("Streaming capability should be used")

    async def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | AssistantMessage, None]:
        self.calls += 1
        self.entered.set()
        text = self.first
        try:
            yield TextDelta(text=text)
            while True:
                part = await self.pieces.get()
                if part is None:
                    break
                text += part
                yield TextDelta(text=part)
            yield AssistantMessage(text=text, usage=TokenUsage(input_tokens=12, output_tokens=4))
        finally:
            self.closed = True


@pytest.mark.parametrize(
    ("size", "panel"),
    [
        ((120, 40), "akis"),
        ((120, 40), "ozet"),
        ((100, 30), "akis"),
        ((80, 24), "akis"),
        ((60, 20), "ozet"),
        ((80, 14), "akis"),
    ],
)
async def test_fixed_layout_and_visible_scrollbars_at_terminal_sizes(
    tmp_path: Path,
    size: tuple[int, int],
    panel: str,
) -> None:
    app = make_app(ScriptedLLM([]), tmp_path)
    app.session.panel = panel
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        chat = app.query_one("#conversation", VerticalScroll)
        side = app.query_one("#flow" if panel == "akis" else "#summary-scroll", VerticalScroll)
        editor = app.query_one(ComposeInput)
        send = app.query_one("#send", Button)
        assert chat.region.bottom <= editor.region.y
        assert editor.region.bottom <= size[1]
        assert send.region.bottom <= size[1]
        assert send.region.width > 0 and chat.region.height > 0
        assert chat.show_vertical_scrollbar
        if size[0] >= 100:  # panel yalnız geniş terminalde görünür
            assert chat.region.right <= side.region.x
            assert side.region.bottom <= editor.region.y
            assert side.show_vertical_scrollbar
            pane = app.query_one("#flow-pane").region.width
            if panel == "akis" and size[0] == 120:
                assert 0.18 <= pane / size[0] <= 0.23
            if panel == "ozet":
                assert pane == 30
        else:  # dar terminal: sohbet tam genişlik, panel gizli
            assert app.query_one("#flow-pane").region.width == 0
            assert chat.region.width >= size[0] - 2
        assert app.focused is editor


async def test_initial_demo_task_runs_after_screen_mount(tmp_path: Path) -> None:
    model = ScriptedLLM([AssistantMessage(text="Hazır demo yanıtı")])
    app = make_app(model, tmp_path)
    app.initial_task = "Hazır görev"
    async with app.run_test() as pilot:
        await app.wait_turn()
        await pilot.pause()
        assert not app.busy and not app.failed
        assert [c.role for c in app.query(ChatMessage)] == ["Sen", "Ajan"]
        assert len(model.received) == 1 and await app.build_report() is not None


def dialog_open(app: HarnessApp) -> bool:
    return isinstance(app.screen, PermissionDialog)


async def test_permission_dialog_session_grant_skips_the_second_question(tmp_path: Path) -> None:
    def write(call_id: str, text: str) -> AssistantMessage:
        arguments = f'{{"path": "not.txt", "content": "{text}"}}'
        return AssistantMessage(
            text="", tool_calls=(ToolCall(id=call_id, name="write_file", arguments=arguments),)
        )

    model = ScriptedLLM([write("c1", "bir"), write("c2", "iki"), AssistantMessage(text="Yazdım.")])
    app = make_app(model, tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await app.submit("yaz")
        for _ in range(50):
            await pilot.pause()
            if dialog_open(app):
                break
        first = app.screen
        assert dialog_open(app)
        # Koordinata tıklamak yük altında, pencere yerleşmeden ıskalayabiliyor; düğmenin
        # kendisine basmak aynı işleyiciyi (on_button_pressed -> dismiss) çalıştırır.
        first.query_one("#always", Button).press()
        # Takılmak yerine başarısız ol: ikinci bir soru açılırsa tur hiç bitmez. İlk pencerenin
        # kapanması da birkaç kare sürer; o hâlâ açıkken "ikinci soru" sayılmaz.
        for _ in range(100):
            await pilot.pause()
            second = dialog_open(app) and app.screen is not first
            if second or (not app.busy and not dialog_open(app)):
                break
        assert not dialog_open(app)
        assert not app.busy and not app.failed
    assert (tmp_path / "not.txt").read_text(encoding="utf-8") == "iki"
    kinds = [e.kind for e in read_session(app.session_log.path)]
    assert kinds.count("permission_requested") == 1


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (60, 16)])
async def test_permission_dialog_long_content_keeps_buttons_and_keys(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    body = "\n".join(f"satır {i}" for i in range(200))
    arguments = json.dumps({"path": "uzun.md", "content": body}, ensure_ascii=False)
    call = ToolCall(id="c1", name="write_file", arguments=arguments)
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test(size=size) as pilot:
        results: list[PermissionChoice | None] = []
        app.push_screen(PermissionDialog(call, "uzun.md"), results.append)
        await pilot.pause()
        await pilot.pause()  # yükseklik ayarı bir kare sonra uygulanır
        dialog = app.screen
        allow = dialog.query_one("#allow", Button)
        deny = dialog.query_one("#deny", Button)
        for button in (allow, deny, dialog.query_one("#always", Button)):
            assert button.region.height > 0 and button.region.bottom <= size[1]
        assert app.focused is allow
        # Metin kaçışsız, gerçek satırlarla görünür.
        shown = str(dialog.query_one("#permission-args", Static).render())
        assert "satır 1\nsatır 2" in shown and "\\n" not in shown
        scroll = dialog.query_one("#permission-scroll", VerticalScroll)
        assert scroll.region.bottom <= size[1]
        await pilot.press("pagedown")
        await pilot.pause()
        assert scroll.scroll_y > 0
        await pilot.press("enter")
        await pilot.pause()
        assert results == ["allow"] and not dialog_open(app)

        app.push_screen(PermissionDialog(call), results.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        app.push_screen(PermissionDialog(call), results.append)
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        assert results == ["allow", "deny", "always"]


async def test_enter_send_shift_enter_multiline_and_history(tmp_path: Path) -> None:
    model = ScriptedLLM(
        [AssistantMessage(text="Birinci yanıt"), AssistantMessage(text="İkinci yanıt")]
    )
    app = make_app(model, tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        editor = app.query_one(ComposeInput)
        editor.load_text("ilk")
        editor.move_cursor((0, 3))
        await pilot.press("shift+enter")
        editor.insert("ikinci satır")
        assert editor.text == "ilk\nikinci satır"
        await pilot.press("enter")
        await app.wait_turn()
        await pilot.pause()
        assert not app.busy
        assert editor.text == ""
        cards = list(app.query(ChatMessage))
        assert [c.role for c in cards] == ["Sen", "Ajan"]
        assert cards[0].source == "ilk\nikinci satır"
        assert cards[1].source == "Birinci yanıt"
        editor.load_text("devam")
        await pilot.click("#send")
        await app.wait_turn()
        await pilot.pause()
        assert [c.role for c in app.query(ChatMessage)] == ["Sen", "Ajan", "Sen", "Ajan"]
        assert len(model.received) == 2
        assert any(
            isinstance(m, AssistantMessage) and m.text == "Birinci yanıt" for m in model.received[1]
        )
        report = await app.build_report()
        assert report is not None and report.is_file()


async def test_second_turn_is_registered_before_slow_message_mount(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_mount(message: ChatMessage) -> None:
        if message.role == "Sen" and message.source == "ikinci":
            entered.set()
            await release.wait()

    monkeypatch.setattr(ChatMessage, "on_mount", slow_mount, raising=False)
    model = ScriptedLLM([AssistantMessage(text="ilk yanıt"), AssistantMessage(text="ikinci yanıt")])
    app = make_app(model, tmp_path)
    async with app.run_test() as pilot:
        await app.submit("ilk")
        await app.wait_turn()
        submission = asyncio.create_task(app.submit("ikinci"))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert submission.done(), "Mesaj çizimi yeni worker kaydını geciktirmemeli"
            await app.action_stop()
            assert not app.busy and app.failed
            assert len(model.received) == 1
        finally:
            release.set()
            await submission
        await pilot.pause()


async def test_stream_visible_before_finish_and_editor_stays_responsive(tmp_path: Path) -> None:
    model = ControlledModel()
    app = make_app(model, tmp_path)
    async with app.run_test(size=(100, 30)) as pilot:
        await app.submit("başla")
        await asyncio.wait_for(model.entered.wait(), timeout=2)
        await pilot.pause()
        assert app.busy
        assert app.query_one("#send", Button).disabled
        assert not app.query_one("#stop", Button).disabled
        assert list(app.query(ChatMessage))[-1].source == "İlk parça"
        assert "İlk" in app.export_screenshot()
        editor = app.query_one(ComposeInput)
        editor.load_text("taslak")
        await pilot.press("enter")
        await pilot.pause()
        assert editor.text == "taslak" and model.calls == 1
        model.pieces.put_nowait(" tamamlandı")
        model.pieces.put_nowait(None)
        await app.wait_turn()
        await pilot.pause()
        assert list(app.query(ChatMessage))[-1].source == "İlk parça tamamlandı"
        assert len(app.query(".agent-message")) == 1
        assert not app.busy and not app.failed
        assert editor.text == "taslak"
        assert model.closed


async def test_escape_cancels_without_allowing_broken_history_to_continue(tmp_path: Path) -> None:
    model = ControlledModel()
    app = make_app(model, tmp_path)
    async with app.run_test() as pilot:
        await app.submit("bekle")
        await asyncio.wait_for(model.entered.wait(), timeout=2)
        await pilot.pause()
        await pilot.press("escape")
        await app.wait_turn()
        await pilot.pause()
        assert model.closed and app.failed and not app.busy
        assert app.query_one("#send", Button).disabled
        assert list(app.query(ChatMessage))[-1].source == "İlk parça"
        await app.submit("devam etme")
        assert model.calls == 1
        events = read_session(app.session_log.path)
        assert isinstance(events[-1], TaskFinished) and events[-1].reason == "cancelled"
        assert await app.build_report() is not None


async def test_new_command_recovers_a_cancelled_session(tmp_path: Path) -> None:
    model = ControlledModel()
    app = make_app(model, tmp_path)
    async with app.run_test() as pilot:
        await app.submit("bekle")
        await asyncio.wait_for(model.entered.wait(), timeout=2)
        await pilot.press("escape")
        await app.wait_turn()
        old_log = app.session_log.path
        assert app.failed

        await app.submit("/help")
        await pilot.pause()
        assert isinstance(app.screen, InfoScreen)  # yardım pencerede, sohbete yazılmaz
        assert "/resume" in app.screen.text and not app.query(".command-message")
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, InfoScreen)  # Esc pencereyi kapattı
        assert app.failed  # yardım oturumu kurtarmaz

        await app.submit("/new")
        await pilot.pause()
        assert not app.failed and not app.query_one("#send", Button).disabled
        assert list(app.query(ChatMessage)) == []  # "Yeni oturum" bildirimi sohbete düşmez
        assert app.session_log.path != old_log
        assert len(app.agent.history) == 1


async def test_tool_details_only_in_report_not_chat_or_flow(tmp_path: Path) -> None:
    (tmp_path / "PRIVATE_PATH.txt").write_text("PRIVATE_TOOL_RESULT", encoding="utf-8")
    call = ToolCall(id="c1", name="read_file", arguments='{"path":"PRIVATE_PATH.txt"}')
    model = ScriptedLLM(
        [
            AssistantMessage(text="Dosyaya bakıyorum.", tool_calls=(call,)),
            AssistantMessage(text="İnceleme tamamlandı."),
        ]
    )
    app = make_app(model, tmp_path)
    async with app.run_test() as pilot:
        await app.submit("incele")
        await app.wait_turn()
        await pilot.pause()
        shown = "\n".join(c.source for c in app.query(ChatMessage))
        shown += "\n".join(str(s.render()) for s in app.query("#flow Static"))
        assert "PRIVATE_PATH" not in shown and "PRIVATE_TOOL_RESULT" not in shown
        # Yol yok ama ne olduğu var: araç ve sonucun özeti.
        assert "✓ read_file · 1 satır" in shown
        report = await app.build_report()
        assert report is not None
        html = report.read_text(encoding="utf-8")
        assert "PRIVATE_PATH" in html and "PRIVATE_TOOL_RESULT" in html


class ThinkingModel:
    """Önce düşünce özetini, sonra cevabı akıtan model."""

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        raise AssertionError("Streaming capability should be used")

    async def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | ReasoningDelta | AssistantMessage, None]:
        yield ReasoningDelta(text="Önce testlere ")
        yield ReasoningDelta(text="bakmalıyım.")
        yield TextDelta(text="Testler geçiyor.")
        yield AssistantMessage(text="Testler geçiyor.", reasoning="Önce testlere bakmalıyım.")


async def test_thought_streams_above_the_answer_folded_by_default(tmp_path: Path) -> None:
    app = make_app(ThinkingModel(), tmp_path)
    async with app.run_test() as pilot:
        await app.submit("kontrol et")
        await app.wait_turn()
        await pilot.pause()
        thoughts = list(app.query(ThoughtBlock))
        assert [t.source for t in thoughts] == ["Önce testlere bakmalıyım."]
        # Varsayılan görünüm katlı: tek satır başlık, kaç kelime olduğu görünür.
        assert thoughts[0].collapsed and thoughts[0].title == "Düşünce · 3 kelime"
        # Düşünce cevabın üstünde, cevaptan ayrı durur; cevap metnine karışmaz.
        conversation = list(app.query_one("#conversation").children)
        reply = [m for m in app.query(ChatMessage) if m.role == "Ajan"][-1]
        assert conversation.index(thoughts[0]) < conversation.index(reply)
        assert reply.source == "Testler geçiyor."


async def test_thought_of_a_non_streaming_model_is_shown_with_the_reply(tmp_path: Path) -> None:
    model = ScriptedLLM([AssistantMessage(text="Bitti.", reasoning="Kısa bir plan yaptım.")])
    app = make_app(model, tmp_path)
    async with app.run_test() as pilot:
        await app.submit("yap")
        await app.wait_turn()
        await pilot.pause()
        assert [t.source for t in app.query(ThoughtBlock)] == ["Kısa bir plan yaptım."]


async def test_plan_progress_shows_in_the_flow(tmp_path: Path) -> None:
    steps = [
        {"step": "Oku", "status": "completed"},
        {"step": "Düzelt", "status": "in_progress"},
        {"step": "Test et", "status": "pending"},
    ]
    plan = ToolCall(id="p1", name="update_plan", arguments=json.dumps({"steps": steps}))
    model = ScriptedLLM([AssistantMessage(tool_calls=(plan,)), AssistantMessage(text="Tamam.")])
    app = make_app(model, tmp_path)
    async with app.run_test() as pilot:
        await app.submit("planla")
        await app.wait_turn()
        await pilot.pause()
        flow = "\n".join(str(s.render()) for s in app.query("#flow Static"))
    assert "update_plan · 1/3 tamam · şimdi: Düzelt" in flow
    assert "PLAN" in flow and "▸ Düzelt" in flow and "✓ Oku" in flow


async def test_scrolling_is_independent_editor_fixed_and_new_text_does_not_steal_position(
    tmp_path: Path,
) -> None:
    long_text = "\n\n".join(f"Paragraf {n}: test içeriği." for n in range(50))
    model = ControlledModel(long_text)
    app = make_app(model, tmp_path)
    app.session.panel = "akis"  # bağımsız kaydırma akış günlüğüyle sınanır
    async with app.run_test(size=(100, 30)) as pilot:
        await app.submit("uzun yanıt")
        await asyncio.wait_for(model.entered.wait(), timeout=2)
        for n in range(40):
            await app.add_flow(f"Adım {n}")
        await pilot.pause()
        chat = app.query_one("#conversation", VerticalScroll)
        flow = app.query_one("#flow", VerticalScroll)
        editor = app.query_one(ComposeInput)
        editor_region = editor.region
        assert chat.max_scroll_y > 0 and flow.max_scroll_y > 0
        chat.scroll_to(y=0, animate=False, immediate=True)
        await pilot.pause()
        before_flow = flow.scroll_y
        chat.focus()
        await pilot.press("pagedown")
        await pilot.pause()
        assert chat.scroll_y > 0 and flow.scroll_y == before_flow
        chat.scroll_to(y=0, animate=False, immediate=True)
        await pilot.pause()
        scrollbar = chat.vertical_scrollbar
        await pilot.click(scrollbar, offset=(0, scrollbar.size.height - 1))
        await pilot.wait_for_scheduled_animations()
        assert chat.scroll_y > 0
        chat.scroll_to(y=0, animate=False, immediate=True)
        chat.post_message(MouseScrollDown(chat, 1, 1, 0, 1, 0, False, False, False))
        await pilot.pause()
        assert chat.scroll_y > 0
        chat.scroll_to(y=0, animate=False, immediate=True)
        model.pieces.put_nowait("\n\nYeni gelen metin.")
        await pilot.pause()
        assert chat.scroll_y == 0
        assert editor.region == editor_region
        await pilot.click("#latest")
        await pilot.pause()
        assert chat.is_vertical_scroll_end
        model.pieces.put_nowait("\n\nEn alta dönüldükten sonra gelen metin.")
        await pilot.pause()
        assert chat.is_vertical_scroll_end
        flow.focus()
        chat_y = chat.scroll_y
        await pilot.press("pageup")
        await pilot.pause()
        assert flow.scroll_y < before_flow and chat.scroll_y == chat_y
        model.pieces.put_nowait(None)
        await app.wait_turn()


async def test_stop_before_worker_starts_does_not_leave_interface_busy(tmp_path: Path) -> None:
    app = make_app(ControlledModel(), tmp_path)
    async with app.run_test() as pilot:
        await app.submit("hemen dur")
        await app.action_stop()
        await pilot.pause()
        assert not app.busy and app.failed
        assert app.query_one("#send", Button).disabled
        assert not app.query_one(ComposeInput).disabled
    assert app.session_log.path.exists()
    events = read_session(app.session_log.path)
    assert isinstance(events[0], TaskStarted) and events[0].task == "hemen dur"
    assert isinstance(events[-1], TaskFinished) and events[-1].reason == "cancelled"


async def test_cancel_during_delta_render_records_cancelled_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    model = ControlledModel()
    app = make_app(model, tmp_path)

    async def blocked_append(message: ChatMessage, text: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ChatMessage, "append_delta", blocked_append)
    async with app.run_test():
        await app.submit("render beklerken iptal")
        await asyncio.wait_for(entered.wait(), timeout=2)
        await app.action_stop()

    events = read_session(app.session_log.path)
    assert isinstance(events[-1], TaskFinished)
    assert events[-1].reason == "cancelled"
    assert "Durduruldu" in write_report(app.session_log.path).read_text(encoding="utf-8")


async def test_quit_waits_for_cancellation_and_preserves_log(tmp_path: Path) -> None:
    model = ControlledModel()
    app = make_app(model, tmp_path)
    async with app.run_test() as pilot:
        await app.submit("bekle")
        await asyncio.wait_for(model.entered.wait(), timeout=2)
        await pilot.pause()
        await pilot.press("ctrl+q")
    assert model.closed
    events = read_session(app.session_log.path)
    assert isinstance(events[-1], TaskFinished) and events[-1].reason == "cancelled"


async def test_model_failure_is_visible_and_does_not_leave_send_enabled(tmp_path: Path) -> None:
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test() as pilot:
        await app.submit("hata")
        await app.wait_turn()
        await pilot.pause()
        assert app.failed and not app.busy
        assert app.query_one("#send", Button).disabled
        assert await app.build_report() is not None
        events = read_session(app.session_log.path)
        assert isinstance(events[-1], TaskFinished) and events[-1].reason == "error"
        assert events[-1].error is not None
        flow_texts = [str(node.render()) for node in app.query(".flow-entry")]
        assert any(events[-1].error in text for text in flow_texts)
        await app.submit("tekrar")
        assert len([e for e in read_session(app.session_log.path) if e.kind == "task_started"]) == 1


async def test_resize_and_theme_preserve_draft_and_messages(tmp_path: Path) -> None:
    app = make_app(ScriptedLLM([AssistantMessage(text="Yanıt")]), tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.submit("ilk mesaj")
        await app.wait_turn()
        editor = app.query_one(ComposeInput)
        editor.load_text("korunacak taslak")
        await pilot.resize_terminal(60, 20)
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.theme == "harness-açık"  # koyu → açık; seçim kalıcı
        assert editor.text == "korunacak taslak"
        assert len(app.query(ChatMessage)) == 2
        assert editor.region.bottom <= 20


def picker_app(tmp_path: Path) -> HarnessApp:
    """ChatGPT'de iki model; diğer sağlayıcılar kapalı ya da bağlı değil."""

    async def load_models(provider: Provider) -> list[ModelInfo]:
        if provider.name != "chatgpt":
            raise RuntimeError("kapalı")
        return [
            ModelInfo(slug="luna", display_name="Luna", efforts=[Effort(name="low")]),
            ModelInfo(
                slug="sol",
                display_name="Sol",
                efforts=[Effort(name="medium"), Effort(name="high")],
            ),
        ]

    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        provider=get_provider("chatgpt"),
        model="luna",
        effort=None,
        make_llm=lambda provider, name, effort: ScriptedLLM([]),
        load_models=load_models,
        load_prompt=lambda anatomy: "talimat",
        sessions_dir=tmp_path / "logs",
    )
    return HarnessApp(session)


async def test_model_window_filters_as_you_type_and_then_asks_the_effort(tmp_path: Path) -> None:
    app = picker_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.submit("/model")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PickerScreen) and screen.picker.kind == "model"
        listing = screen.query_one(OptionList)
        # İmleç şu anki modelde başlar (0: grup başlığı).
        assert listing.highlighted is not None
        assert screen.shown[listing.highlighted].value == "chatgpt/luna"  # type: ignore[union-attr]

        await pilot.press(*"sol")
        await pilot.pause()
        assert [c.value for c in screen.shown if c] == ["chatgpt/sol"]
        await pilot.press("enter")
        await pilot.pause()

        assert app.session.model == "sol"
        effort = app.screen
        assert isinstance(effort, PickerScreen) and effort.picker.kind == "effort"
        await pilot.press("down", "down", "enter")  # varsayılan → medium → high
        await pilot.pause()
        assert not isinstance(app.screen, PickerScreen)
        assert app.session.label == "sol · effort: high · chatgpt"
        assert "sol · effort: high" in str(app.query_one("#topbar", Static).render())


async def test_escape_closes_the_window_without_changing_anything(tmp_path: Path) -> None:
    app = picker_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.submit("/anatomy")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PickerScreen)
        # İmleç ilk satırda değil, şu anki anatomide (raw, files, ...) başlar.
        highlighted = screen.query_one(OptionList).highlighted
        assert highlighted is not None
        assert screen.shown[highlighted].value == "files"  # type: ignore[union-attr]
        await pilot.press("down", "escape")
        await pilot.pause()
        assert not isinstance(app.screen, PickerScreen)
        assert app.session.anatomy.name == "files"


async def test_connect_asks_for_a_hidden_key_and_saves_it(tmp_path: Path) -> None:
    app = picker_app(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await app.submit("/connect deepseek")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, KeyScreen)
        field = screen.query_one(Input)
        assert field.password  # yazılan ekranda görünmez
        await pilot.press(*"ds-gizli", "enter")
        await pilot.pause()
        assert load_keys() == {"DEEPSEEK_API_KEY": "ds-gizli"}
        assert all("ds-gizli" not in m.source for m in app.query(ChatMessage))
        # Bağlanınca o sağlayıcının modelleri açılır (burada sunucu "kapalı").
        assert isinstance(app.screen, PickerScreen)
        await pilot.press("escape")

        await app.submit("/connect openai")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert "OPENAI_API_KEY" not in load_keys()
        assert "Vazgeçildi" in str(app.query_one("#status", Static).render())


async def test_slash_opens_palette_suggests_and_tab_completes(tmp_path: Path) -> None:
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test() as pilot:
        editor = app.query_one(ComposeInput)
        editor.text = "/se"
        await pilot.pause()
        assert "/sessions" in str(app.query_one("#status", Static).render())
        await pilot.press("tab")
        await pilot.pause()
        assert editor.text == "/sessions "

        editor.text = "/"
        await pilot.press("enter")
        await pilot.pause()
        palette = app.screen
        assert isinstance(palette, PickerScreen) and palette.picker.kind == "command"
        await pilot.press("escape")
        await pilot.pause()

        await app.submit("/status")
        await pilot.pause()
        assert isinstance(app.screen, InfoScreen) and app.screen.title_text == "Durum"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, InfoScreen)
        await app.submit("/theme açık")
        await pilot.pause()
        assert not app.query(".command-message")  # onay sohbete değil bildirime düştü
        assert any("açık" in n.message for n in app._notifications)  # type: ignore[attr-defined]


async def test_ask_user_question_joins_the_chat_and_is_answered_from_the_editor(
    tmp_path: Path,
) -> None:
    arguments = json.dumps({"question": "Sayaç gizlensin mi?", "options": ["evet", "hayır"]})
    model = ScriptedLLM(
        [
            AssistantMessage(tool_calls=(ToolCall(id="q1", name="ask_user", arguments=arguments),)),
            AssistantMessage(text="Gizledim."),
        ]
    )
    app = make_app(model, tmp_path, "files+ask_user")
    async with app.run_test(size=(100, 30)) as pilot:
        await app.submit("sayacı düzelt")
        status = app.query_one("#status", Static)
        for _ in range(50):
            await pilot.pause()
            if "Cevabın bekleniyor" in str(status.render()):
                break
        assert "Cevabın bekleniyor" in str(status.render())
        # Soru sohbetin bir parçası olarak düştü; ayrı pencere açılmadı.
        [question] = [m for m in app.query(ChatMessage) if "Sayaç gizlensin mi?" in m.source]
        editor = app.query_one(ComposeInput)
        editor.text = "1"
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause()
            if not app.busy:
                break
        assert not app.busy and not app.failed
        assert "**Cevap:** evet" in question.source  # cevap sorunun altına işlendi
    finished = [e for e in read_session(app.session_log.path) if e.kind == "tool_finished"]
    assert finished[0].result.content == "Kullanıcının cevabı: evet"  # pyright: ignore[reportAttributeAccessIssue]


async def test_blank_enter_answers_the_ask_user_question_with_no_answer(tmp_path: Path) -> None:
    arguments = json.dumps({"question": "Hangi biçim?", "options": ["csv", "json"]})
    model = ScriptedLLM(
        [
            AssistantMessage(tool_calls=(ToolCall(id="q1", name="ask_user", arguments=arguments),)),
            AssistantMessage(text="Varsayım: csv."),
        ]
    )
    app = make_app(model, tmp_path, "files+ask_user")
    async with app.run_test(size=(100, 30)) as pilot:
        await app.submit("veriyi yaz")
        status = app.query_one("#status", Static)
        for _ in range(50):
            await pilot.pause()
            if "Cevabın bekleniyor" in str(status.render()):
                break
        app.query_one(ComposeInput).text = ""
        await pilot.press("enter")  # boş: cevap yok, ajan varsayımla sürer
        for _ in range(100):
            await pilot.pause()
            if not app.busy:
                break
        assert not app.busy and not app.failed
        [question] = [m for m in app.query(ChatMessage) if "Hangi biçim?" in m.source]
        assert "Cevap yok" in question.source
    finished = [e for e in read_session(app.session_log.path) if e.kind == "tool_finished"]
    assert finished[0].result.content.startswith(  # pyright: ignore[reportAttributeAccessIssue]
        "Kullanıcıya şu an soru sorulamıyor"
    )


@pytest.mark.skipif(find_bash() is None, reason="bash yok")
async def test_topbar_follows_the_folder_bash_moved_into(tmp_path: Path) -> None:
    """Üst bar komutların başladığı klasörü gösterir: `cd alt` sonrası yol .../alt olur."""

    def cd(call_id: str, where: str) -> AssistantMessage:
        return AssistantMessage(
            tool_calls=(
                ToolCall(id=call_id, name="bash", arguments=json.dumps({"command": f"cd {where}"})),
            )
        )

    (tmp_path / "alt").mkdir()
    model = ScriptedLLM(
        [
            cd("c1", "alt"),
            AssistantMessage(text="Alt klasördeyim."),
            cd("c2", ".."),
            AssistantMessage(text="Köke döndüm."),
        ]
    )
    app = make_app(model, tmp_path, "bash")
    app.session.permissions.grant("bash")
    async with app.run_test(size=(140, 40)) as pilot:
        topbar = app.query_one("#topbar", Static)
        assert str(topbar.render()).rstrip().endswith(tmp_path.name)
        await app.submit("alt klasöre geç")
        await app.wait_turn()
        await pilot.pause()
        assert str(topbar.render()).rstrip().endswith("alt")
        await app.submit("geri dön")
        await app.wait_turn()
        await pilot.pause()
        assert str(topbar.render()).rstrip().endswith(tmp_path.name)


async def test_thinking_view_open_and_hidden_apply_to_past_thoughts(tmp_path: Path) -> None:
    app = make_app(ThinkingModel(), tmp_path)
    async with app.run_test() as pilot:
        await app.submit("kontrol et")
        await app.wait_turn()
        await app.run_command("/thinking açık")
        await pilot.pause()
        thought = app.query_one(ThoughtBlock)
        assert app.session.thinking == "acik" and not thought.collapsed and thought.display
        await app.run_command("/thinking gizli")
        await pilot.pause()
        assert thought.region.height == 0  # çizilmez; kaynak metin kayıtta kalır


async def test_tool_lines_show_in_chat_unless_flow_panel_is_visible(tmp_path: Path) -> None:
    call = ToolCall(id="c1", name="list_dir", arguments='{"path": "."}')
    model = ScriptedLLM([AssistantMessage(tool_calls=(call,)), AssistantMessage(text="Tamam.")])
    app = make_app(model, tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.submit("listele")
        await app.wait_turn()
        await pilot.pause()
        # Başlangıç ve bitiş aynı satırdır: "● list_dir …" yerini sonuca bırakır.
        lines = [str(line.render()) for line in app.query(".tool-line")]
        assert len(lines) == 1 and lines[0].startswith("✓ list_dir")
        line = app.query_one(".tool-line")
        assert app.session.panel == "ozet" and line.region.height == 1
        assert app.query_one("#summary-scroll").region.height > 0
        await pilot.press("ctrl+b")  # özet -> akış: araç satırı akışta, sohbette değil
        await pilot.pause()
        assert app.session.panel == "akis" and line.region.height == 0
        flow = [str(e.render()) for e in app.query(".flow-entry")]
        assert sum(e.startswith("✓ list_dir") for e in flow) == 1
        assert not any(e.startswith("● list_dir") for e in flow)
        await pilot.press("ctrl+b")  # akış -> kapalı: panel yok, sohbet tam genişlik
        await pilot.pause()
        assert app.session.panel == "kapali" and app.query_one("#flow-pane").region.width == 0
        assert line.region.height == 1


async def test_narrow_terminal_hides_the_panel_but_keeps_the_choice(tmp_path: Path) -> None:
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        assert app.query_one("#flow-pane").region.width == 0
        assert app.session.panel == "ozet"


async def test_summary_panel_tracks_plan_files_and_turn(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    steps = [{"step": "Oku", "status": "completed"}, {"step": "Düzelt", "status": "in_progress"}]
    calls = (
        ToolCall(id="p1", name="update_plan", arguments=json.dumps({"steps": steps})),
        ToolCall(id="r1", name="read_file", arguments='{"path": "a.py"}'),
    )
    model = ScriptedLLM([AssistantMessage(tool_calls=calls), AssistantMessage(text="Tamam.")])
    app = make_app(model, tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        await app.submit("planla")
        await app.wait_turn()
        await pilot.pause()
        shown = str(app.query_one("#summary", Static).render())
    assert "TUR 1 · bitti" in shown
    assert "✓ Oku" in shown and "▸ Düzelt" in shown
    assert "O  a.py" in shown and "araç   2" in shown


async def test_newline_keys_work_where_shift_enter_is_plain_enter(tmp_path: Path) -> None:
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("b", "i", "r", "ctrl+j", "i", "k", "i", "backslash", "enter", "u", "c")
        assert app.query_one(ComposeInput).text == "bir\niki\nuc"
        assert not app.busy  # hiçbiri göndermedi


async def test_ctrl_c_clears_draft_then_needs_two_presses_to_quit(tmp_path: Path) -> None:
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("t", "a", "s", "l", "a", "k")
        await pilot.press("ctrl+c")
        assert app.query_one(ComposeInput).text == "" and app.is_running
        await pilot.press("ctrl+c")
        assert app.is_running  # tek basış çıkmaz, uyarır
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert app.return_code == 0


async def test_ctrl_c_stops_a_running_turn(tmp_path: Path) -> None:
    model = ControlledModel()
    app = make_app(model, tmp_path)
    async with app.run_test() as pilot:
        await app.submit("uzun iş")
        await asyncio.wait_for(model.entered.wait(), timeout=2)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not app.busy and app.failed and app.is_running


async def test_copy_also_uses_the_system_clipboard_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied: list[str] = []

    def copy(text: str) -> bool:
        copied.append(text)
        return True

    monkeypatch.setattr("harness.cli.tui.copy_text", copy)
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test() as pilot:
        app.copy_to_clipboard("merhaba")
        for _ in range(20):
            await pilot.pause(0.02)
            if copied:
                break
    assert copied == ["merhaba"] and app.clipboard == "merhaba"


async def test_ctrl_v_pastes_the_system_clipboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def paste() -> str | None:
        return "panodan gelen"

    monkeypatch.setattr("harness.cli.tui.paste_text", paste)
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+v")
        for _ in range(20):
            await pilot.pause(0.02)
            if app.query_one(ComposeInput).text:
                break
        assert app.query_one(ComposeInput).text == "panodan gelen"


async def test_keymap_file_rebinds_and_updates_the_hint(tmp_path: Path) -> None:
    (tmp_path / "keys.json").write_text('{"panel": "f2", "bozuk": 3}', encoding="utf-8")
    keymap = load_keymap(tmp_path / "keys.json")
    assert keymap == {"panel": "f2"}
    assert load_keymap(tmp_path / "yok.json") == {}
    app = make_app(ScriptedLLM([]), tmp_path)
    app._keymap = keymap  # pyright: ignore[reportPrivateUsage]
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("f2")
        await pilot.pause()
        assert app.session.panel == "akis"
        assert "F2 panel" in str(app.query_one("#keys", Static).render())


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def attachments_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "ekler"
    monkeypatch.setattr("harness.cli.attachments.ATTACHMENTS_HOME", folder)
    return folder


async def settle(pilot: Pilot[None], done: Callable[[], object]) -> None:
    """Ek, iş parçacığında okunur: koşul sağlanana dek birkaç kare bekle."""
    for _ in range(30):
        await pilot.pause(0.02)
        if done():
            return


async def test_dropped_image_becomes_a_label_and_goes_with_the_message(
    tmp_path: Path, attachments_home: Path
) -> None:
    picture = tmp_path / "ekran.png"
    picture.write_bytes(PNG_BYTES)
    model = ScriptedLLM([AssistantMessage(text="Bir ekran görüntüsü.")])
    app = make_app(model, tmp_path)
    async with app.run_test(size=(120, 36)) as pilot:
        editor = app.query_one(ComposeInput)
        await pilot.press(*"bu ne")
        editor.post_message(events.Paste(f"'{picture}'"))  # terminal sürükleneni böyle yollar
        await settle(pilot, lambda: editor.attachments())
        assert editor.text == "bu ne [Görsel 1] "
        bar = app.query_one("#attachments", Static)
        assert bar.display and "[Görsel 1] ekran.png" in str(bar.render())
        await pilot.press("enter")
        await app.wait_turn()
        await pilot.pause()
        sent = model.received[0][-1]
        assert isinstance(sent, UserMessage) and sent.text == "bu ne [Görsel 1]"
        assert [Path(i.path).parent for i in sent.images] == [attachments_home]
        user = next(m for m in app.query(ChatMessage) if m.role == "Sen")
        assert "- [Görsel 1] ekran.png · 1 KB" in user.source
        assert not bar.display and not editor.attachments()  # gönderince ekler temizlenir


async def test_deleting_the_label_drops_the_attachment(
    tmp_path: Path, attachments_home: Path
) -> None:
    picture = tmp_path / "a.png"
    picture.write_bytes(PNG_BYTES)
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test() as pilot:
        editor = app.query_one(ComposeInput)
        editor.post_message(events.Paste(str(picture)))
        await settle(pilot, lambda: editor.attachments())
        assert editor.attachments()
        editor.text = "etiketsiz metin"
        await pilot.pause()
        assert editor.attachments() == []
        assert not app.query_one("#attachments", Static).display


async def test_model_without_image_input_keeps_the_draft(
    tmp_path: Path, attachments_home: Path
) -> None:
    picture = tmp_path / "a.png"
    picture.write_bytes(PNG_BYTES)
    model = ScriptedLLM([AssistantMessage(text="gitmemeli")])
    app = make_app(model, tmp_path)

    async def text_only() -> bool | None:
        return False

    app.session.image_support = text_only  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        editor = app.query_one(ComposeInput)
        await pilot.press(*"bak")
        editor.post_message(events.Paste(str(picture)))
        await settle(pilot, lambda: editor.attachments())
        await pilot.press("enter")
        await pilot.pause()
        assert not app.busy and model.received == []
        assert editor.text == "bak [Görsel 1] " and editor.attachments()
        assert "görsel kabul etmiyor" in str(app.query_one("#status", Static).render())


async def test_ctrl_v_attaches_a_clipboard_image_when_there_is_no_text(
    tmp_path: Path, attachments_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_text() -> str | None:
        return None

    def screenshot() -> bytes | None:
        return PNG_BYTES

    monkeypatch.setattr("harness.cli.tui.paste_text", no_text)
    monkeypatch.setattr("harness.cli.tui.paste_image", screenshot)
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test() as pilot:
        editor = app.query_one(ComposeInput)
        await pilot.press("ctrl+v")
        await settle(pilot, lambda: editor.attachments())
        [(label, image)] = editor.attachments()
        assert label == "[Görsel 1]" and image.name == "pano-1"
        assert image.media_type == "image/png" and Path(image.path).parent == attachments_home
