"""Döngünün verdiği sözler: araç ister → çalıştırılır → sonuç modele döner → model bitirir."""

from pathlib import Path

import pytest

from harness.core.agent import Agent
from harness.core.events import (
    Event,
    ModelReplied,
    ModelStarted,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall, ToolResult, UserMessage
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile

pytestmark = pytest.mark.anyio


def read_call(call_id: str, path: str) -> AssistantMessage:
    arguments = f'{{"path": "{path}"}}'
    call = ToolCall(id=call_id, name="read_file", arguments=arguments)
    return AssistantMessage(tool_calls=(call,))


async def collect(agent: Agent, task: str) -> list[Event]:
    return [event async for event in agent.run(task)]


def make_agent(model: ScriptedLLM, root: Path, max_steps: int = 20) -> Agent:
    tools = ToolRegistry([ReadFile(root=root)])
    return Agent(model, tools, system_prompt="talimat", max_steps=max_steps)


async def test_answer_without_tools_finishes_in_one_step(tmp_path: Path) -> None:
    agent = make_agent(ScriptedLLM([AssistantMessage(text="merhaba")]), tmp_path)

    events = await collect(agent, "selam")

    assert [type(e) for e in events] == [
        TaskStarted,
        ModelStarted,
        ModelReplied,
        TaskFinished,
    ]
    assert isinstance(events[0], TaskStarted) and events[0].task == "selam"
    assert isinstance(events[2], ModelReplied) and events[2].text == "merhaba"
    assert isinstance(events[-1], TaskFinished)
    assert events[-1].reason == "done" and events[-1].steps == 1
    assert all(e.timestamp is not None for e in events)
    assert events[-1].duration_seconds is not None


async def test_tool_result_reaches_the_model_on_the_next_step(tmp_path: Path) -> None:
    (tmp_path / "not.txt").write_text("gizli bilgi", encoding="utf-8")
    model = ScriptedLLM([read_call("c1", "not.txt"), AssistantMessage(text="okudum")])

    events = await collect(make_agent(model, tmp_path), "notu oku")

    assert [type(e) for e in events] == [
        TaskStarted,
        ModelStarted,
        ModelReplied,
        ToolStarted,
        ToolFinished,
        ModelStarted,
        ModelReplied,
        TaskFinished,
    ]
    # İkinci çağrıda model, kendi isteğini ve aracın sonucunu geçmişin sonunda görür.
    seen_last = model.received[1][-1]
    assert isinstance(seen_last, ToolResult)
    assert seen_last.tool_call_id == "c1"
    assert "gizli bilgi" in seen_last.content


async def test_tool_error_goes_back_to_the_model_and_loop_continues(tmp_path: Path) -> None:
    model = ScriptedLLM([read_call("c1", "yok.txt"), AssistantMessage(text="dosya yokmuş")])

    events = await collect(make_agent(model, tmp_path), "oku")

    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert finished[0].result.is_error
    assert isinstance(events[-1], TaskFinished)
    assert events[-1].reason == "done" and events[-1].steps == 2


async def test_stops_at_step_limit_even_if_model_keeps_asking(tmp_path: Path) -> None:
    model = ScriptedLLM([read_call(f"c{i}", "yok.txt") for i in range(10)])

    events = await collect(make_agent(model, tmp_path, max_steps=3), "oku")

    assert isinstance(events[-1], TaskFinished)
    assert events[-1].reason == "step_limit" and events[-1].steps == 3
    assert len(model.received) == 3


async def test_model_is_offered_the_tools_on_every_step(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    model = ScriptedLLM([read_call("c1", "a.txt"), AssistantMessage(text="tamam")])

    await collect(make_agent(model, tmp_path), "oku")

    assert [[spec.name for spec in tools] for tools in model.offered_tools] == [
        ["read_file"],
        ["read_file"],
    ]


async def test_history_starts_with_system_prompt_and_task(tmp_path: Path) -> None:
    model = ScriptedLLM([AssistantMessage(text="tamam")])

    await collect(make_agent(model, tmp_path), "görev")

    first_seen = model.received[0]
    assert first_seen[0].role == "system"
    assert first_seen[1] == UserMessage(text="görev")
