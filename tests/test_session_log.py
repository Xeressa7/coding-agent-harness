"""Oturum kaydı ve demo komutu: yazılan olay aynen geri okunur, demo uçtan uca çalışır."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli.app import app
from harness.core.events import (
    ContextAssembled,
    Event,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    TaskFinished,
    TaskStarted,
    ToolFinished,
)
from harness.core.session_log import SessionLogger, read_session
from harness.messages import SystemMessage, ToolCall, ToolResult, ToolSpec, UserMessage


def test_events_survive_a_round_trip_through_the_log(tmp_path: Path) -> None:
    call = ToolCall(id="c1", name="read_file", arguments='{"path": "a.txt"}')
    events: list[Event] = [
        TaskStarted(task="görev"),
        ContextAssembled(
            step=1,
            messages=(SystemMessage(text="talimat"), UserMessage(text="görev")),
            tools=(ToolSpec(name="read_file", description="okur", parameters={}),),
        ),
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

    (log_path,) = (tmp_path / "harness-sessions").rglob("*.jsonl")
    replayed = runner.invoke(app, ["replay", str(log_path)])
    assert replayed.exit_code == 0
    assert "Görev bitti" in replayed.output


def test_replay_shows_unfinished_stream_text_without_duplicating_completed_text(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    partial_log = SessionLogger(tmp_path / "partial.jsonl")
    for event in (
        TaskStarted(task="kesilen görev"),
        ModelStarted(step=1),
        ModelTextDelta(step=1, text="kısmi yanıt"),
        TaskFinished(reason="cancelled", steps=1),
    ):
        partial_log.write(event)

    partial = runner.invoke(app, ["replay", str(partial_log.path)])
    assert partial.exit_code == 0, partial.output
    assert "kısmi yanıt" in partial.output
    assert "Yanıt tamamlanmadı." in partial.output

    complete_log = SessionLogger(tmp_path / "complete.jsonl")
    for event in (
        TaskStarted(task="tam görev"),
        ModelStarted(step=1),
        ModelTextDelta(step=1, text="tam yanıt"),
        ModelReplied(step=1, text="tam yanıt"),
        TaskFinished(reason="done", steps=1),
    ):
        complete_log.write(event)

    complete = runner.invoke(app, ["replay", str(complete_log.path)])
    assert complete.exit_code == 0, complete.output
    assert complete.output.count("tam yanıt") == 1
    assert "Yanıt tamamlanmadı." not in complete.output


def growing_contexts() -> list[ContextAssembled]:
    """Konuşma adım adım uzar, sonra baştan bir mesaj değişir (budama gibi), araçlar sabit."""
    tools = (ToolSpec(name="read_file", description="okur", parameters={}),)
    system = SystemMessage(text="talimat")
    history = [UserMessage(text="görev"), UserMessage(text="ek 1"), UserMessage(text="ek 2")]
    pruned = [UserMessage(text="görev (budandı)"), *history[1:], UserMessage(text="ek 3")]
    return [
        ContextAssembled(step=1, messages=(system, *history[:1]), tools=tools),
        ContextAssembled(step=2, messages=(system, *history[:2]), tools=tools),
        ContextAssembled(step=3, messages=(system, *history), tools=tools),
        ContextAssembled(step=4, messages=(system, *pruned), tools=()),
        ContextAssembled(step=5, messages=(system, *pruned), tools=tools),
    ]


def test_context_is_stored_as_difference_and_read_back_exactly(tmp_path: Path) -> None:
    events: list[Event] = [TaskStarted(task="görev"), *growing_contexts()]
    log = SessionLogger(tmp_path / "oturum.jsonl")
    for event in events:
        log.write(event)

    stored = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    contexts = [r for r in stored if r["kind"] == "context_assembled"]
    assert [(len(r["messages"]), r["reused"], r["tools_reused"]) for r in contexts] == [
        (2, 0, False),  # ilk bağlam tam
        (1, 2, True),  # yalnız yeni mesaj
        (1, 3, True),
        (4, 1, False),  # budanan mesajdan sonrası yeniden yazılır; araç yok
        (0, 5, False),  # araçlar boştan geri geldi: tam yazılır
    ]
    assert read_session(log.path) == events


def test_full_context_lines_from_old_logs_still_read(tmp_path: Path) -> None:
    contexts = growing_contexts()
    path = tmp_path / "eski.jsonl"
    old = [c.model_dump_json(exclude={"reused", "tools_reused"}) for c in contexts]
    path.write_text("\n".join(old) + "\n", encoding="utf-8")
    assert read_session(path) == contexts
    # Devam eden oturum aynı dosyaya yeni kaydediciyle ekler: ilk bağlamı tam yazılır.
    log = SessionLogger(path)
    log.write(contexts[-1])
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["reused"] == 0
    assert read_session(path) == [*contexts, contexts[-1]]


def test_context_that_points_to_a_missing_previous_one_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "bozuk.jsonl"
    line = ContextAssembled(step=2, messages=(), reused=3).model_dump_json()
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="önceki bağlam"):
        read_session(path)
