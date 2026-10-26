"""Döngünün verdiği sözler: araç ister → çalıştırılır → sonuç modele döner → model bitirir."""

from pathlib import Path

import pytest

from harness.core.agent import Agent
from harness.core.events import (
    ContextAssembled,
    Event,
    ModelReplied,
    ModelStarted,
    PermissionRequested,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.core.permissions import PermissionManager
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall, ToolResult, UserMessage
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile
from harness.tools.write_file import WriteFile

pytestmark = pytest.mark.anyio


def read_call(call_id: str, path: str) -> AssistantMessage:
    arguments = f'{{"path": "{path}"}}'
    call = ToolCall(id=call_id, name="read_file", arguments=arguments)
    return AssistantMessage(tool_calls=(call,))


async def collect(agent: Agent, task: str) -> list[Event]:
    return [event async for event in agent.run(task)]


async def collect_answering(agent: Agent, task: str, answer: bool) -> list[Event]:
    """`async for` gibi tüketir ama her PermissionRequested'ı sabit bir cevapla yanıtlar."""
    events: list[Event] = []
    stream = agent.run(task)
    send: bool | None = None
    try:
        while True:
            event = await stream.asend(send)
            events.append(event)
            send = answer if isinstance(event, PermissionRequested) else None
    except StopAsyncIteration:
        pass
    return events


def make_agent(model: ScriptedLLM, root: Path, max_steps: int = 20) -> Agent:
    tools = ToolRegistry([ReadFile(root=root)])
    permissions = PermissionManager(root, tools)
    return Agent(
        model, tools, system_prompt="talimat", permissions=permissions, max_steps=max_steps
    )


def write_call(call_id: str, path: str, content: str) -> AssistantMessage:
    arguments = f'{{"path": "{path}", "content": "{content}"}}'
    call = ToolCall(id=call_id, name="write_file", arguments=arguments)
    return AssistantMessage(tool_calls=(call,))


def make_agent_with_write_file(model: ScriptedLLM, root: Path) -> Agent:
    tools = ToolRegistry([WriteFile(root=root)])
    return Agent(model, tools, system_prompt="talimat", permissions=PermissionManager(root, tools))


async def test_answer_without_tools_finishes_in_one_step(tmp_path: Path) -> None:
    agent = make_agent(ScriptedLLM([AssistantMessage(text="merhaba")]), tmp_path)

    events = await collect(agent, "selam")

    assert [type(e) for e in events] == [
        TaskStarted,
        ContextAssembled,
        ModelStarted,
        ModelReplied,
        TaskFinished,
    ]
    assert isinstance(events[0], TaskStarted) and events[0].task == "selam"
    assert isinstance(events[3], ModelReplied) and events[3].text == "merhaba"
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
        ContextAssembled,
        ModelStarted,
        ModelReplied,
        ToolStarted,
        ToolFinished,
        ContextAssembled,
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


async def test_recorded_context_is_exactly_what_the_model_received(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    model = ScriptedLLM([read_call("c1", "a.txt"), AssistantMessage(text="tamam")])

    events = await collect(make_agent(model, tmp_path), "oku")

    contexts = [e for e in events if isinstance(e, ContextAssembled)]
    assert [c.step for c in contexts] == [1, 2]
    assert [c.messages for c in contexts] == model.received
    assert [c.tools for c in contexts] == model.offered_tools


async def test_unanswered_permission_request_defaults_to_deny(tmp_path: Path) -> None:
    model = ScriptedLLM([write_call("c1", "a.txt", "x"), AssistantMessage(text="tamam")])
    agent = make_agent_with_write_file(model, tmp_path)

    # async for == her yield'e asend(None): cevapsız kalan "sor" reddedilmiş sayılır.
    events = await collect(agent, "yaz")

    assert not (tmp_path / "a.txt").exists()
    assert any(isinstance(e, PermissionRequested) for e in events)
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert finished[0].result.is_error
    assert "verilmedi" in finished[0].result.content


async def test_approved_permission_request_runs_the_tool(tmp_path: Path) -> None:
    model = ScriptedLLM([write_call("c1", "a.txt", "merhaba"), AssistantMessage(text="tamam")])
    agent = make_agent_with_write_file(model, tmp_path)

    events = await collect_answering(agent, "yaz", answer=True)

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "merhaba"
    assert any(isinstance(e, PermissionRequested) for e in events)
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert not finished[0].result.is_error


async def test_denied_permission_request_skips_the_tool(tmp_path: Path) -> None:
    model = ScriptedLLM([write_call("c1", "a.txt", "x"), AssistantMessage(text="tamam")])
    agent = make_agent_with_write_file(model, tmp_path)

    events = await collect_answering(agent, "yaz", answer=False)

    assert not (tmp_path / "a.txt").exists()
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert finished[0].result.is_error
    assert finished[0].duration_seconds == 0.0


async def test_granting_a_tool_skips_asking_again_in_the_same_run(tmp_path: Path) -> None:
    model = ScriptedLLM(
        [
            write_call("c1", "a.txt", "bir"),
            write_call("c2", "b.txt", "iki"),
            AssistantMessage(text="tamam"),
        ]
    )
    agent = make_agent_with_write_file(model, tmp_path)

    # İlk PermissionRequested'ı "oturum boyu izin ver" gibi grant ederek onaylar;
    # ikinci write_file çağrısı artık hiç sorulmadan (ASK olmadan) çalışmalı.
    stream = agent.run("yaz")
    events: list[Event] = []
    send: bool | None = None
    try:
        while True:
            event = await stream.asend(send)
            events.append(event)
            send = None
            if isinstance(event, PermissionRequested):
                agent.permissions.grant(event.call.name)
                send = True
    except StopAsyncIteration:
        pass

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "bir"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "iki"
    assert sum(isinstance(e, PermissionRequested) for e in events) == 1
