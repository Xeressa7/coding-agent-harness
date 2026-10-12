"""Oturum kaydı ve demo komutu: yazılan olay aynen geri okunur, demo uçtan uca çalışır."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli.app import app
from harness.core.events import Event, ModelReplied, TaskFinished, TaskStarted, ToolFinished
from harness.core.session_log import SessionLogger, read_session
from harness.messages import ToolCall, ToolResult


def test_events_survive_a_round_trip_through_the_log(tmp_path: Path) -> None:
    call = ToolCall(id="c1", name="read_file", arguments='{"path": "a.txt"}')
    events: list[Event] = [
        TaskStarted(task="görev"),
        ModelReplied(step=1, text="okuyorum", tool_calls=(call,)),
        ToolFinished(call=call, result=ToolResult(tool_call_id="c1", content="içerik")),
        TaskFinished(reason="done", steps=2),
    ]
    log = SessionLogger(tmp_path / "kayit" / "oturum.jsonl")

    for event in events:
        log.write(event)

    assert read_session(log.path) == events


def test_demo_runs_end_to_end_and_can_be_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Demo, çalıştığı klasördeki pyproject.toml'u okur ve kaydı oraya yazar.
    project_file = Path(__file__).parent.parent / "pyproject.toml"
    (tmp_path / "pyproject.toml").write_text(project_file.read_text(encoding="utf-8"), "utf-8")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["demo", "--delay", "0"])

    assert result.exit_code == 0, result.output
    assert "Görev bitti" in result.output
    assert "böyle bir dosya yok" in result.output

    (log_path,) = (tmp_path / "sessions").glob("*.jsonl")
    replayed = runner.invoke(app, ["replay", str(log_path)])
    assert replayed.exit_code == 0
    assert "Görev bitti" in replayed.output
