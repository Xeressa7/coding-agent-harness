"""Gerçek Textual yerleşimi: sabit editör, bağımsız kaydırma ve async ajan olayları."""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.events import MouseScrollDown
from textual.widgets import Button

from harness.cli.tui import ChatMessage, ComposeInput, HarnessApp
from harness.core.agent import Agent
from harness.core.events import TaskFinished, TaskStarted
from harness.core.session_log import SessionLogger, read_session
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, Message, TextDelta, TokenUsage, ToolCall, ToolSpec
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile

pytestmark = pytest.mark.anyio


def make_app(model: object, tmp_path: Path) -> HarnessApp:
    # Concrete LLM doubles below satisfy the shared complete/stream protocols.
    assert isinstance(model, (ScriptedLLM, ControlledModel))
    agent = Agent(model, ToolRegistry([ReadFile(root=tmp_path)]), "talimat")
    return HarnessApp(
        agent, SessionLogger(tmp_path / "tui.jsonl"), model_label="test-model", root=tmp_path
    )


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


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (60, 20), (80, 14)])
async def test_fixed_layout_and_visible_scrollbars_at_terminal_sizes(
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    app = make_app(ScriptedLLM([]), tmp_path)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        chat = app.query_one("#conversation", VerticalScroll)
        flow = app.query_one("#flow", VerticalScroll)
        editor = app.query_one(ComposeInput)
        send = app.query_one("#send", Button)
        assert chat.region.right <= flow.region.x
        assert chat.region.bottom <= editor.region.y
        assert flow.region.bottom <= editor.region.y
        assert editor.region.bottom <= size[1]
        assert send.region.bottom <= size[1]
        assert send.region.width > 0 and chat.region.height > 0
        assert chat.show_vertical_scrollbar and flow.show_vertical_scrollbar
        if size[0] == 120:
            assert 0.18 <= app.query_one("#flow-pane").region.width / size[0] <= 0.23
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
        assert len(model.received) == 1 and app.report is not None


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
        assert app.report is not None and app.report.is_file()


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
        assert app.report is not None


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
        assert "read_file" in shown
        assert app.report is not None
        html = app.report.read_text(encoding="utf-8")
        assert "PRIVATE_PATH" in html and "PRIVATE_TOOL_RESULT" in html


async def test_scrolling_is_independent_editor_fixed_and_new_text_does_not_steal_position(
    tmp_path: Path,
) -> None:
    long_text = "\n\n".join(f"Paragraf {n}: test içeriği." for n in range(50))
    model = ControlledModel(long_text)
    app = make_app(model, tmp_path)
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
    assert app.report is not None
    assert "Durduruldu" in app.report.read_text(encoding="utf-8")


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
        assert app.report is not None
        events = read_session(app.session_log.path)
        assert isinstance(events[-1], TaskFinished) and events[-1].reason == "error"
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
        assert app.theme == "harness-light"
        assert editor.text == "korunacak taslak"
        assert len(app.query(ChatMessage)) == 2
        assert editor.region.bottom <= 20
