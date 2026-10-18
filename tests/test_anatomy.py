"""Anatomy: hangi katmanın açık olduğu tek yerde durur ve her koşunun kaydına yazılır."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli.app import app
from harness.core.agent import Agent
from harness.core.anatomy import PRESETS, Anatomy, get_preset
from harness.core.events import TaskStarted
from harness.core.session_log import SessionLogger, read_session
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage

pytestmark = pytest.mark.anyio


async def offered_tool_names(anatomy: Anatomy, root: Path) -> list[str]:
    model = ScriptedLLM([AssistantMessage(text="tamam")])
    agent = Agent(model, anatomy.tool_registry(root), "talimat", anatomy=anatomy)
    _ = [event async for event in agent.run("görev")]
    return [spec.name for spec in model.offered_tools[0]]


async def test_raw_preset_offers_the_model_no_tools(tmp_path: Path) -> None:
    assert await offered_tool_names(get_preset("raw"), tmp_path) == []


async def test_files_preset_offers_the_file_tools(tmp_path: Path) -> None:
    assert await offered_tool_names(get_preset("files"), tmp_path) == ["read_file"]


async def test_every_run_records_its_anatomy(tmp_path: Path) -> None:
    anatomy = get_preset("raw")
    model = ScriptedLLM([AssistantMessage(text="tamam")])
    agent = Agent(model, anatomy.tool_registry(tmp_path), "talimat", anatomy=anatomy)
    log = SessionLogger(tmp_path / "kayit.jsonl")
    async for event in agent.run("görev"):
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
    with pytest.raises(ValueError, match="raw, files"):
        get_preset("yok")

    # Geniş terminal: hata kutusu mesajı satırlara bölmesin.
    result = CliRunner().invoke(app, ["ask", "selam", "--anatomy", "yok"], env={"COLUMNS": "200"})
    assert result.exit_code == 2
    assert "raw, files" in result.output
