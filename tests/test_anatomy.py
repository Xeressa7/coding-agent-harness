"""Anatomy: hangi katmanın açık olduğu tek yerde durur ve her koşunun kaydına yazılır."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli.app import app
from harness.core.agent import Agent
from harness.core.anatomy import PRESETS, Anatomy, get_preset
from harness.core.events import Event, PermissionRequested, TaskStarted, ToolFinished
from harness.core.permissions import PermissionManager
from harness.core.session_log import SessionLogger, read_session
from harness.environments.local import find_bash
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall

pytestmark = pytest.mark.anyio


async def collect_approving(agent: Agent, task: str) -> list[Event]:
    """`async for` gibi tüm olayları toplar, ama her PermissionRequested'ı onaylar."""
    events: list[Event] = []
    stream = agent.run(task)
    send: bool | None = None
    try:
        while True:
            event = await stream.asend(send)
            events.append(event)
            send = True if isinstance(event, PermissionRequested) else None
    except StopAsyncIteration:
        pass
    return events


async def offered_tool_names(anatomy: Anatomy, root: Path) -> list[str]:
    model = ScriptedLLM([AssistantMessage(text="tamam")])
    tools = anatomy.tool_registry(root)
    agent = Agent(
        model, tools, "talimat", permissions=PermissionManager(root, tools), anatomy=anatomy
    )
    _ = await collect_approving(agent, "görev")
    return [spec.name for spec in model.offered_tools[0]]


async def test_raw_preset_offers_the_model_no_tools(tmp_path: Path) -> None:
    assert await offered_tool_names(get_preset("raw"), tmp_path) == []


async def test_files_preset_offers_the_file_tools(tmp_path: Path) -> None:
    names = await offered_tool_names(get_preset("files"), tmp_path)
    assert names == ["read_file", "write_file", "edit_file", "search"]


async def test_bash_preset_adds_only_bash_on_top_of_files(tmp_path: Path) -> None:
    files = await offered_tool_names(get_preset("files"), tmp_path)
    assert await offered_tool_names(get_preset("bash"), tmp_path) == [*files, "bash"]


async def test_every_run_records_its_anatomy(tmp_path: Path) -> None:
    anatomy = get_preset("raw")
    model = ScriptedLLM([AssistantMessage(text="tamam")])
    tools = anatomy.tool_registry(tmp_path)
    agent = Agent(
        model, tools, "talimat", permissions=PermissionManager(tmp_path, tools), anatomy=anatomy
    )
    log = SessionLogger(tmp_path / "kayit.jsonl")
    for event in await collect_approving(agent, "görev"):
        log.write(event)

    started = read_session(log.path)[0]
    assert isinstance(started, TaskStarted) and started.anatomy == anatomy


def test_old_logs_without_anatomy_still_read(tmp_path: Path) -> None:
    path = tmp_path / "eski.jsonl"
    path.write_text('{"kind": "task_started", "task": "eski görev"}\n', encoding="utf-8")

    (started,) = read_session(path)
    assert isinstance(started, TaskStarted) and started.anatomy is None


def test_preset_names_match_their_keys() -> None:
    assert all(anatomy.name == name for name, anatomy in PRESETS.items())


def test_unknown_anatomy_is_rejected_with_the_known_names() -> None:
    with pytest.raises(ValueError, match="raw, files, bash"):
        get_preset("yok")

    # Geniş terminal: hata kutusu mesajı satırlara bölmesin.
    result = CliRunner().invoke(app, ["ask", "selam", "--anatomy", "yok"], env={"COLUMNS": "200"})
    assert result.exit_code == 2
    assert "raw, files, bash" in result.output


def step(call_id: str, name: str, **arguments: object) -> AssistantMessage:
    call = ToolCall(id=call_id, name=name, arguments=json.dumps(arguments, ensure_ascii=False))
    return AssistantMessage(tool_calls=(call,))


@pytest.mark.skipif(find_bash() is None, reason="bash yok")
async def test_bash_level_lets_the_model_see_its_error_and_fix_it(tmp_path: Path) -> None:
    """Seviye 2'nin özü: yaz, çalıştır, hatayı gör, düzelt, yeniden çalıştır."""
    model = ScriptedLLM(
        [
            step("c1", "write_file", path="gemi.py", content="print(hiz * 2)\n"),
            step("c2", "bash", command="python gemi.py"),
            step("c3", "edit_file", path="gemi.py", old_text="print(", new_text="hiz = 21\nprint("),
            step("c4", "bash", command="python gemi.py"),
            AssistantMessage(text="Düzelttim."),
        ]
    )
    anatomy = get_preset("bash")
    tools = anatomy.tool_registry(tmp_path)
    agent = Agent(
        model, tools, "talimat", permissions=PermissionManager(tmp_path, tools), anatomy=anatomy
    )

    events = await collect_approving(agent, "gemiyi çalıştır")

    outputs = [e.result.content for e in events if isinstance(e, ToolFinished)]
    assert "NameError" in outputs[1] and outputs[1].startswith("çıkış kodu: 1")
    assert outputs[3] == "çıkış kodu: 0\n--- stdout ---\n42"
