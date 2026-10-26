"""Ölçüm sözleşmesi: sağlayıcı sayımları, saatler, hata ve kesinti."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.cli.app import run_turn
from harness.core import agent as agent_module
from harness.core.agent import Agent
from harness.core.events import Event, ModelReplied, TaskFinished, ToolFinished
from harness.core.permissions import PermissionManager
from harness.core.session_log import SessionLogger, read_session
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, Message, TokenUsage, ToolCall, ToolSpec
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile


def test_usage_rejects_negative_numbers() -> None:
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1)


@pytest.mark.anyio
async def test_timing_is_separate_from_consumer_pauses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = iter([0.0, 10.0, 12.0, 20.0, 23.0, 30.0, 34.0, 40.0])
    monkeypatch.setattr(agent_module, "perf_counter", lambda: next(clock))
    call = ToolCall(id="1", name="read_file", arguments='{"path":"missing"}')
    tools = ToolRegistry([ReadFile(root=tmp_path)])
    agent = Agent(
        ScriptedLLM([AssistantMessage(tool_calls=(call,)), AssistantMessage(text="ok")]),
        tools,
        "kural",
        permissions=PermissionManager(tmp_path, tools),
    )
    events = [e async for e in agent.run("oku")]
    assert [e.duration_seconds for e in events if isinstance(e, ModelReplied)] == [2, 4]
    assert [e.duration_seconds for e in events if isinstance(e, ToolFinished)] == [3]
    assert isinstance(events[-1], TaskFinished) and events[-1].duration_seconds == 40


class BlockingModel:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.anyio
async def test_real_cancellation_preserves_a_cancelled_log_and_html(tmp_path: Path) -> None:
    model = BlockingModel()
    tools = ToolRegistry([])
    agent = Agent(model, tools, "kural", permissions=PermissionManager(tmp_path, tools))
    log = SessionLogger(tmp_path / "cancelled.jsonl")
    task = asyncio.create_task(run_turn(agent, "bekle", log))
    await asyncio.wait_for(model.entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    events = read_session(log.path)
    assert isinstance(events[-1], TaskFinished) and events[-1].reason == "cancelled"
    (report,) = tmp_path.glob("*.html")
    assert "Durduruldu" in report.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_unexpected_tool_exception_is_not_task_success(tmp_path: Path) -> None:
    class BrokenRegistry(ToolRegistry):
        async def call(self, call: ToolCall):
            raise RuntimeError("tool crashed")

    call = ToolCall(id="1", name="broken", arguments="{}")
    tools = BrokenRegistry([])
    agent = Agent(
        ScriptedLLM([AssistantMessage(tool_calls=(call,))]),
        tools,
        "kural",
        permissions=PermissionManager(tmp_path, tools),
    )
    events: list[Event] = []
    with pytest.raises(RuntimeError, match="tool crashed"):
        async for event in agent.run("x"):
            events.append(event)
    assert isinstance(events[-1], TaskFinished)
    assert events[-1].reason == "error" and events[-1].error == "tool crashed"
