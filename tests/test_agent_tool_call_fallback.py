"""Ajan: metne yazılmış araç çağrısı katman açıkken çalışır, kapalıyken eski davranış."""

from pathlib import Path

import pytest

from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import Event, HarnessIntervened, TaskFinished, ToolFinished
from harness.core.permissions import PermissionManager
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage

pytestmark = pytest.mark.anyio

_PLAIN_TEXT_CALL = '{"name": "read_file", "arguments": {"path": "a.py"}}'


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
    return [event async for event in agent.run("a.py'yi oku")]


async def test_plain_text_tool_call_is_recovered_and_executed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    replies = [AssistantMessage(text=_PLAIN_TEXT_CALL), AssistantMessage(text="bitti")]
    events = await run(tmp_path, "bash", replies)  # preset varsayılanı: tool_call_fallback=True

    [intervened] = [e for e in events if isinstance(e, HarnessIntervened)]
    assert intervened.layer == "tool_call_fallback" and "read_file" in intervened.summary
    [tool_finished] = [e for e in events if isinstance(e, ToolFinished)]
    assert tool_finished.call.name == "read_file" and not tool_finished.result.is_error
    finished = events[-1]
    assert isinstance(finished, TaskFinished) and finished.reason == "done" and finished.steps == 2


async def test_layer_off_treats_the_same_reply_as_already_done(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    replies = [AssistantMessage(text=_PLAIN_TEXT_CALL)]
    events = await run(tmp_path, "bash-tool_call_fallback", replies)

    assert not [e for e in events if isinstance(e, HarnessIntervened)]
    assert not [e for e in events if isinstance(e, ToolFinished)]
    finished = events[-1]
    assert isinstance(finished, TaskFinished) and finished.reason == "done" and finished.steps == 1


async def test_text_that_is_not_a_known_tool_call_still_finishes_normally(tmp_path: Path) -> None:
    replies = [AssistantMessage(text="Her şey yolunda, yapacak bir şey yok.")]
    events = await run(tmp_path, "bash+tool_call_fallback", replies)

    assert not [e for e in events if isinstance(e, HarnessIntervened)]
    finished = events[-1]
    assert isinstance(finished, TaskFinished) and finished.reason == "done" and finished.steps == 1
