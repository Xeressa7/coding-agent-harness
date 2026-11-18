"""Okuma verimi ve çalışma biçimi: kayıttan hakemsiz ölçümler; yeni ve eski kayıt aynı tanımla."""

import json

from harness.cli.inspector import reread_text, summary_items
from harness.core.evaluation import TaskResult, summarize
from harness.core.events import Event, ModelReplied, TaskFinished, TaskStarted, ToolFinished
from harness.core.metrics import read_stats, work_stats
from harness.messages import TokenUsage, ToolCall, ToolResult


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


def _replied(step: int, tokens: int | None) -> ModelReplied:
    usage = TokenUsage(input_tokens=tokens) if tokens is not None else None
    return ModelReplied(step=step, text="", usage=usage)


def _tool(name: str, error: bool = False) -> ToolFinished:
    call = ToolCall(id="c", name=name, arguments="{}")
    return ToolFinished(call=call, result=ToolResult(tool_call_id="c", content="", is_error=error))


def test_work_stats_finds_first_edit_tail_and_peak() -> None:
    events = [
        _replied(1, 1_000),
        _tool("read_file"),
        _replied(2, 5_000),
        _tool("edit_file", error=True),  # hatalı düzenleme sayılmaz
        _replied(3, 9_000),
        _tool("edit_file"),
        _replied(4, 8_000),
        _tool("bash"),
        _replied(5, None),
        _tool("write_file"),
        _replied(6, 12_000),
        _tool("bash"),
        _replied(7, 11_000),
    ]
    work = work_stats(events)
    assert (work.steps, work.edits, work.first_edit_step) == (7, 2, 3)
    assert (work.after_last_edit, work.peak_context) == (2, 12_000)
    assert work.first_edit_share == 3 / 7


def test_work_stats_without_edits() -> None:
    work = work_stats([_replied(1, None), _tool("read_file"), _replied(2, None)])
    assert (work.first_edit_step, work.after_last_edit, work.peak_context) == (None, None, None)
    assert work.first_edit_share is None
