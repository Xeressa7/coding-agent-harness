"""Zaman çizelgesi: olaylardan turlar, adımlar, araç/izin aralıkları ve okunur sayfa."""

from datetime import UTC, datetime, timedelta

from harness.cli.inspector import render_report
from harness.cli.timeline import build_turns, render_turn
from harness.core.events import (
    Event,
    ModelReplied,
    ModelStarted,
    PermissionRequested,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.messages import TokenUsage, ToolCall, ToolResult

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def tool(
    call_id: str,
    name: str,
    content: str,
    start: float,
    end: float,
    *,
    category: str | None = "read",
    summary: str = "",
    outcome: str = "",
    error: bool = False,
    details: dict[str, object] | None = None,
) -> list[Event]:
    call = ToolCall(id=call_id, name=name, arguments='{"path": "a.py"}')
    return [
        ToolStarted(call=call, category=category, summary=summary, timestamp=at(start)),
        ToolFinished(
            call=call,
            result=ToolResult(tool_call_id=call_id, content=content, is_error=error),
            duration_seconds=end - start,
            category=category,
            summary=summary,
            outcome=outcome,
            details=details or {},
            timestamp=at(end),
        ),
    ]


def run_events() -> list[Event]:
    """İki adımlı bir koşu: okuma + izinli yazma, sonra cevap."""
    read = ToolCall(id="r", name="read_file", arguments="{}")
    write = ToolCall(id="w", name="write_file", arguments='{"path": "b.py"}')
    plan_steps = [{"step": "Oku", "status": "completed"}, {"step": "Yaz", "status": "in_progress"}]
    return [
        TaskStarted(task="düzelt", provider="anthropic", model="m", effort="high", timestamp=at(0)),
        ModelStarted(step=1, timestamp=at(0)),
        ModelReplied(
            step=1,
            text="",
            reasoning="Önce dosyayı okumalıyım.",
            tool_calls=(read, write),
            duration_seconds=4,
            first_text_seconds=None,
            usage=TokenUsage(input_tokens=1000, output_tokens=50, cached_tokens=800),
            timestamp=at(4),
        ),
        *tool(
            "r",
            "read_file",
            "x" * 300,
            4,
            4.5,
            summary="a.py",
            outcome="10 satır",
            details={"path": "a.py"},
        ),
        PermissionRequested(call=write, summary="b.py", timestamp=at(4.5)),
        *tool(
            "w",
            "write_file",
            "b.py: oluşturuldu",
            7.5,
            7.6,
            category="write",
            summary="b.py",
            outcome="oluşturuldu · 3 satır",
            details={"path": "b.py"},
        ),
        *tool(
            "p",
            "update_plan",
            "Plan",
            7.6,
            7.7,
            category="plan",
            outcome="1/2 tamam",
            details={"steps": plan_steps, "completed": 1, "total": 2},
        ),
        ModelStarted(step=2, timestamp=at(8)),
        ModelReplied(
            step=2,
            text="Bitti <b>kalın değil</b>",
            duration_seconds=12,
            first_text_seconds=9,
            usage=TokenUsage(input_tokens=1400, output_tokens=200),
            timestamp=at(20),
        ),
        TaskFinished(reason="done", steps=2, duration_seconds=20, timestamp=at(20)),
    ]


def test_turn_steps_tools_and_permission_wait_are_rebuilt_from_events() -> None:
    (turn,) = build_turns(run_events())

    assert (turn.provider, turn.model, turn.effort, turn.duration) == ("anthropic", "m", "high", 20)
    first, second = turn.steps
    assert (first.start, first.end, first.reasoning) == (0, 4, "Önce dosyayı okumalıyım.")
    assert [t.call.name for t in first.tools] == ["read_file", "write_file", "update_plan"]
    (wait,) = first.waits
    assert (wait.name, wait.start, wait.end, wait.allowed) == ("write_file", 4.5, 7.5, True)
    assert second.final and second.first_text == 9
    # Sonraki adımın girdi artışı (400) araç sonuçlarına karakter payıyla dağılır.
    assert first.added_next == 400
    estimates = [round(t.tokens or 0) for t in first.tools]
    assert sum(estimates) == 400 and estimates[0] > estimates[1]


def test_page_shows_lanes_findings_reasoning_plan_and_files() -> None:
    (turn,) = build_turns(run_events())
    html = render_turn(turn)

    for lane in (">Model<", ">Okuma<", ">Yazma<", ">Plan<", ">İzin<"):
        assert lane in html
    assert "Sürenin <b>%80</b>’i modeli beklemekle geçti" in html
    assert "1 kez izin istendi, cevap beklerken 3,0 sn geçti" in html
    assert "En uzun adım 2: <b>12 sn</b>; ilk kelimeden önce 9,0 sn düşündü" in html
    assert "Düşünce özeti</span>Önce dosyayı okumalıyım." in html
    # 2. adım önbellek sayısı vermedi: toplam uydurulmaz, "en az" denir.
    assert "toplamda <b>2,4K</b>" in html and "önbellekten en az 800" in html
    assert "Değişen dosyalar: <code>b.py</code>" in html
    assert "▸ Yaz" in html and "1 kez güncellendi" in html
    assert "write_file için izin beklendi · 3,0 sn · izin verildi" in html
    assert "oluşturuldu · 3 satır" in html
    # Model metni HTML olarak yorumlanmaz.
    assert "<b>kalın değil</b>" not in html and "&lt;b&gt;kalın değil&lt;/b&gt;" in html


def test_denied_tool_and_error_are_marked_not_hidden() -> None:
    write = ToolCall(id="w", name="write_file", arguments="{}")
    events: list[Event] = [
        TaskStarted(task="x", timestamp=at(0)),
        ModelStarted(step=1, timestamp=at(0)),
        ModelReplied(step=1, text="", tool_calls=(write,), duration_seconds=1, timestamp=at(1)),
        PermissionRequested(call=write, timestamp=at(1)),
        ToolFinished(
            call=write,
            result=ToolResult(tool_call_id="w", content="İzin verilmedi.", is_error=True),
            duration_seconds=0,
            category="write",
            outcome="izin verilmedi",
            timestamp=at(3),
        ),
        *tool("e", "read_file", "a.py: böyle bir dosya yok.", 3, 3.1, error=True),
        TaskFinished(reason="done", steps=1, duration_seconds=4, timestamp=at(4)),
    ]
    (turn,) = build_turns(events)
    html = render_turn(turn)

    assert turn.waits[0].allowed is False
    assert "1 istek reddedildi" in html
    assert "1 araç çağrısı hata döndü (read_file)" in html
    assert "chip cat-write err" in html


def test_old_logs_without_timestamps_or_categories_still_render() -> None:
    call = ToolCall(id="c", name="bash", arguments='{"command": "pytest -q"}')
    events: list[Event] = [
        TaskStarted(task="eski"),
        ModelReplied(step=1, text="", tool_calls=(call,)),
        ToolFinished(call=call, result=ToolResult(tool_call_id="c", content="çıkış kodu: 0")),
        ModelReplied(step=2, text="tamam"),
        TaskFinished(reason="done", steps=2),
    ]
    (turn,) = build_turns(events)

    assert turn.tools[0].category == "exec" and turn.tools[0].summary == "$ pytest -q"
    html = render_turn(turn)
    assert "zaman bilgisi yok" in html and "$ pytest -q" in html


def test_report_puts_raw_events_in_a_closed_section_per_turn() -> None:
    html = render_report(run_events() + run_events(), "x.jsonl")

    assert html.count("<details class='raw'>") == 2
    assert "Tur 1" in html and "Tur 2" in html
    assert "<script" not in html


def test_cache_claim_needs_a_reported_zero_not_a_missing_count() -> None:
    def two_steps(cached: int | None) -> list[Event]:
        return [
            TaskStarted(task="x", timestamp=at(0)),
            ModelReplied(
                step=1,
                text="",
                tool_calls=(ToolCall(id="a", name="read_file", arguments="{}"),),
                duration_seconds=1,
                usage=TokenUsage(input_tokens=100, output_tokens=1, cached_tokens=cached),
                timestamp=at(1),
            ),
            ModelReplied(
                step=2,
                text="ok",
                duration_seconds=1,
                usage=TokenUsage(input_tokens=200, output_tokens=1, cached_tokens=cached),
                timestamp=at(2),
            ),
            TaskFinished(reason="done", steps=2, duration_seconds=2, timestamp=at(2)),
        ]

    zero = render_turn(build_turns(two_steps(0))[0])
    unknown = render_turn(build_turns(two_steps(None))[0])
    assert "önbellek kullanılmadı" in zero
    assert "önbellek kullanılmadı" not in unknown and "baştan yollanır." in unknown
