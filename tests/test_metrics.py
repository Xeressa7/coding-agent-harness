"""Okuma verimi: tekrar okunan satırlar; yeni ve eski kayıt biçimi aynı tanımla ölçülür."""

import json

from harness.cli.inspector import reread_text, summary_items
from harness.core.evaluation import TaskResult, summarize
from harness.core.events import Event, TaskFinished, TaskStarted, ToolFinished
from harness.core.metrics import read_stats
from harness.messages import ToolCall, ToolResult


def read(
    call_id: str, path: str, details: dict[str, object], *, error: bool = False, **args: object
) -> ToolFinished:
    call = ToolCall(id=call_id, name="read_file", arguments=json.dumps({"path": path, **args}))
    result = ToolResult(tool_call_id=call_id, content="", is_error=error)
    return ToolFinished(call=call, result=result, details=details)


def test_overlapping_reads_of_the_same_file_count_as_rereads() -> None:
    events: list[Event] = [
        read("1", "src/a.py", {"path": "src/a.py", "start": 1, "end": 100}),
        read("2", "./src/A.py", {"path": "./src/A.py", "start": 51, "end": 150}),  # aynı dosya
        # Eski kayıt: aralık argümandan ve satır sayısından çıkarılır.
        read("3", "src\\a.py", {"path": "src\\a.py", "lines": 20}, start_line=141),
        read("4", "b.py", {"path": "b.py", "start": 1, "end": 10}),
        read("5", "yok.py", {}, error=True),
    ]
    stats = read_stats(events)
    assert (stats.lines_read, stats.reread_lines, stats.files) == (230, 60, 2)
    assert stats.reread_ratio is not None and round(stats.reread_ratio, 3) == 0.261
    assert reread_text(stats) == "%26 (60 / 230)"
    assert read_stats([]).reread_ratio is None and reread_text(read_stats([])) == "—"


def test_the_inspector_and_the_experiment_report_the_same_measure() -> None:
    events: list[Event] = [
        TaskStarted(task="t"),
        read("1", "a.py", {"path": "a.py", "start": 1, "end": 10}),
        read("2", "a.py", {"path": "a.py", "start": 1, "end": 10}),
        TaskFinished(reason="done", steps=1),
    ]
    assert dict(summary_items(events))["Tekrar okunan satır"] == "%50 (10 / 20)"

    def run(read_lines: int, again: int) -> TaskResult:
        return TaskResult(
            "t",
            True,
            "done",
            1,
            1.0,
            None,
            None,
            "",
            lines_read=read_lines,
            reread_lines=again,
            variant="v",
        )

    (summary,) = summarize([run(100, 30), run(300, 10), run(0, 0)])
    assert summary.reread_ratio == 0.1  # 40 / 400: satır ağırlıklı, koşu ortalaması değil
