"""Bitirmeden önce kontrol: model çağırmadan eksikleri bir kez söyler; ikinci "bitti" kabul."""

import json
from pathlib import Path

import pytest

from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import Event, HarnessIntervened, TaskFinished
from harness.core.finish_check import FinishCheck, note
from harness.core.permissions import PermissionManager
from harness.core.session_log import history_from_events
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall, ToolResult, UserMessage

pytestmark = pytest.mark.anyio


def call(name: str, call_id: str = "c", **arguments: object) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


def ok(call_id: str = "c") -> ToolResult:
    return ToolResult(tool_call_id=call_id, content="")


def test_edit_without_a_later_command_is_reported_once() -> None:
    check = FinishCheck(can_run_commands=True)
    check.saw(call("bash"), ok(), {})
    check.saw(call("edit_file"), ok(), {})

    missing = check.check("bitti")

    assert missing is not None and "hiç komut çalıştırmadın" in missing
    assert check.check("bitti") is None  # yalnız bir kez


def test_a_command_after_the_edit_or_a_failed_edit_is_fine() -> None:
    check = FinishCheck(can_run_commands=True)
    check.saw(call("edit_file"), ok(), {})
    check.saw(call("bash"), ok(), {})
    check.saw(call("edit_file"), ToolResult(tool_call_id="c", content="", is_error=True), {})
    assert check.check("bitti") is None
    # Komut aracı olmayan anatomide (files) komut istenmez.
    files = FinishCheck(can_run_commands=False)
    files.saw(call("write_file"), ok(), {})
    assert files.check("bitti") is None


def test_open_plan_steps_and_an_unanswered_test_guard_warning() -> None:
    check = FinishCheck(can_run_commands=False)
    steps = [
        {"step": "oku", "status": "completed"},
        {"step": "düzelt", "status": "in_progress"},
        {"step": "test", "status": "pending"},
    ]
    check.saw(call("update_plan"), ok(), {"steps": steps})
    check.guard_warned()

    missing = check.check("Düzelttim.")

    assert missing is not None
    assert 'planda bitmemiş adım var: "düzelt", "test"' in missing
    assert '"Değişen sözleşme: …" satırı yok' in missing
    text = note(missing)
    assert text.startswith("[harness] Bitirmeden önce kontrol:\n- planda")
    assert text.count("\n- ") == 2 and text.endswith("yazıp bitir.")
    answered = FinishCheck(can_run_commands=False)
    answered.guard_warned()
    assert answered.check("Değişen sözleşme: sayaç artık gizli.") is None


async def run(tmp_path: Path, anatomy_name: str, replies: list[AssistantMessage]) -> list[Event]:
    anatomy = get_preset(anatomy_name)
    tools = anatomy.tool_registry(tmp_path)
    agent = Agent(
        ScriptedLLM(replies),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=anatomy,
        root=tmp_path,
    )
    agent.permissions.grant("write_file")
    agent.permissions.grant("bash")
    return [event async for event in agent.run("dosyayı yaz")]


async def test_agent_reminds_once_and_the_resumed_history_keeps_the_note(tmp_path: Path) -> None:
    replies = [
        AssistantMessage(tool_calls=(call("write_file", "c1", path="a.py", content="x = 1\n"),)),
        AssistantMessage(text="bitti"),
        AssistantMessage(text="Tek satırlık sabit; çalıştırılacak bir şey yok."),
    ]
    events = await run(tmp_path, "bash+finish_check", replies)

    [warned] = [e for e in events if isinstance(e, HarnessIntervened)]
    assert (warned.layer, warned.action, warned.step) == ("finish_check", "warn", 2)
    finished = events[-1]
    assert isinstance(finished, TaskFinished) and finished.reason == "done" and finished.steps == 3
    history = history_from_events(events)
    assert [type(m).__name__ for m in history] == [
        "UserMessage",
        "AssistantMessage",
        "ToolResult",
        "AssistantMessage",
        "UserMessage",
        "AssistantMessage",
    ]
    reminder = history[4]
    assert isinstance(reminder, UserMessage) and reminder.text == note(warned.summary)


async def test_layer_off_finishes_at_the_first_done(tmp_path: Path) -> None:
    replies = [
        AssistantMessage(tool_calls=(call("write_file", "c1", path="a.py", content="x = 1\n"),)),
        AssistantMessage(text="bitti"),
    ]
    events = await run(tmp_path, "bash", replies)

    assert not [e for e in events if isinstance(e, HarnessIntervened)]
    assert isinstance(events[-1], TaskFinished) and events[-1].steps == 2
