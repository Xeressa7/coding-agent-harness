"""Özet paneli: olaylardan "şu an ne durumda?" cevabı; hiçbir şey yürütmez."""

from datetime import UTC, datetime

from harness.cli.summary import WorkSummary, elapsed, fit_path
from harness.core.events import ModelReplied, TaskFinished, TaskStarted, ToolFinished
from harness.messages import TokenUsage, ToolCall, ToolResult

NOW = datetime(2026, 11, 23, tzinfo=UTC)


def finished(name: str, path: str, *, error: bool = False) -> ToolFinished:
    call = ToolCall(id=f"{name}-{path}", name=name, arguments=f'{{"path": "{path}"}}')
    return ToolFinished(
        call=call,
        result=ToolResult(tool_call_id=call.id, content="", is_error=error),
        timestamp=NOW,
    )


def test_files_keep_what_was_done_and_changed_ones_come_first() -> None:
    summary = WorkSummary()
    summary.update(TaskStarted(task="t", model="m", timestamp=NOW))
    for event in (
        finished("read_file", "okunan.py"),
        finished("read_file", "degisen.py"),
        finished("edit_file", "degisen.py"),
        finished("write_file", "hatali.py", error=True),
    ):
        summary.update(event)
    assert summary.files == {"okunan.py": "O", "degisen.py": "OD"}
    assert summary.tool_calls == 4 and summary.tool_errors == 1
    shown = summary.render(30).plain
    assert shown.index("degisen.py") < shown.index("okunan.py")
    assert "O oku · D düzen" in shown and "hatali.py" not in shown


def test_usage_and_turn_result() -> None:
    summary = WorkSummary()
    assert "Henüz görev yok" in summary.render().plain
    summary.update(TaskStarted(task="t", model="m", timestamp=NOW))
    usage = TokenUsage(input_tokens=12_300, output_tokens=800)
    summary.update(ModelReplied(step=1, text="", usage=usage, timestamp=NOW))
    summary.update(TaskFinished(reason="done", steps=1, timestamp=NOW))
    shown = summary.render().plain
    assert "TUR 1 · bitti" in shown
    assert "↑12.3k ↓800" in shown and "bağlam 12.3k" in shown
    summary.reset()
    assert summary.turn == 0 and not summary.files


def test_fit_path_keeps_the_file_name_and_elapsed_is_short() -> None:
    assert fit_path("src/harness/cli/summary.py", 12) == "…/summary.py"
    assert fit_path("kisa.py", 12) == "kisa.py"
    assert elapsed(4.24) == "4.2 sn" and elapsed(37.4) == "37 sn" and elapsed(125) == "2 dk 05 sn"
