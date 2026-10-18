"""Görev sonrası inceleme: gerçek kayıt, eksik ölçümler, güvenli HTML ve eski biçim."""

from pathlib import Path

import pytest

from harness.cli.inspector import render_report, summary_items, write_report
from harness.core.events import (
    ContextAssembled,
    Event,
    ModelReplied,
    TaskFinished,
    TaskStarted,
    ToolFinished,
)
from harness.core.session_log import SessionLogger
from harness.messages import SystemMessage, TokenUsage, ToolCall, ToolResult, ToolSpec, UserMessage


def turn(usage: TokenUsage | None = None) -> list[Event]:
    return [
        TaskStarted(task="Dosyayı incele", model="test-model"),
        ModelReplied(step=1, text="Sonuç", usage=usage, duration_seconds=2),
        TaskFinished(reason="done", steps=1, duration_seconds=3),
    ]


def test_unknown_usage_is_not_zero_and_zero_is_not_missing() -> None:
    missing = dict(summary_items(turn()))
    assert missing["Girdi tokenı"] == "—"
    assert missing["Çıktı tokenı"] == "—"
    assert missing["Ortalama token/sn"] == "—"
    zero = dict(summary_items(turn(TokenUsage(input_tokens=0, output_tokens=0))))
    assert zero["Girdi tokenı"] == "0" and zero["Çıktı tokenı"] == "0"
    assert zero["Ortalama token/sn"] == "0.00"


def test_totals_and_rate_do_not_add_subsets_and_reject_partial_counts() -> None:
    events = turn(
        TokenUsage(input_tokens=100, output_tokens=20, cached_tokens=80, reasoning_tokens=5)
    )
    totals = dict(summary_items(events))
    assert totals["Girdi tokenı"] == "100"
    assert totals["Çıktı tokenı"] == "20"
    assert totals["Ortalama token/sn"] == "10.00"
    events.insert(2, ModelReplied(step=2, text="Kullanım yok", duration_seconds=1))
    assert dict(summary_items(events))["Çıktı tokenı"] == "—"
    assert dict(summary_items(events))["Ortalama token/sn"] == "—"


def test_missing_finish_is_not_success_or_a_complete_total() -> None:
    events = turn(TokenUsage(input_tokens=5, output_tokens=2))[:-1]
    totals = dict(summary_items(events))
    assert totals["Durum"] == "Yarım kaldı"
    assert totals["Çıktı tokenı"] == "—"
    assert "Yarım kaldı" in render_report(events, "old.jsonl")


@pytest.mark.parametrize(
    "reason,label", [("error", "Hata"), ("cancelled", "Durduruldu"), ("step_limit", "Adım sınırı")]
)
def test_non_success_states_are_distinct(reason: str, label: str) -> None:
    finish = TaskFinished.model_validate({"reason": reason, "steps": 1, "error": "Örnek hata"})
    assert dict(summary_items([finish]))["Durum"] == label
    assert "Örnek hata" in render_report([finish], "x")


def test_untrusted_text_and_tool_data_never_become_html() -> None:
    payload = '</pre><script>alert("x")</script><img src=x onerror=alert(1)>'
    call = ToolCall(id=payload, name=payload, arguments=payload)
    events: list[Event] = [
        TaskStarted(task=payload, model=payload, effort=payload),
        ContextAssembled(
            step=1,
            messages=(SystemMessage(text=payload), UserMessage(text=payload)),
            tools=(ToolSpec(name=payload, description=payload, parameters={payload: payload}),),
        ),
        ModelReplied(step=1, text=payload, tool_calls=(call,)),
        ToolFinished(
            call=call, result=ToolResult(tool_call_id=payload, content=payload, is_error=True)
        ),
    ]
    html = render_report(events, payload)
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert "Content-Security-Policy" in html
    assert "<details>" in html and "<summary>" in html
    assert "hassas bilgi" in html


def test_turns_are_separate_even_when_step_and_call_ids_repeat() -> None:
    html = render_report([*turn(), *turn()], "two.jsonl")
    assert "Tur 1" in html and "Tur 2" in html
    assert html.count("Dosyayı incele") == 2
    assert "Bu kayıtta henüz olay yok" in render_report([], "empty.jsonl")


def test_old_log_and_exclusive_output_preserve_source_and_user_file(tmp_path: Path) -> None:
    source = tmp_path / "old.jsonl"
    old = '{"kind":"task_started","task":"eski"}\n'
    old += '{"kind":"model_replied","step":1,"text":"cevap"}\n'
    old += '{"kind":"task_finished","reason":"done","steps":1}\n'
    source.write_text(old, encoding="utf-8")
    user_file = source.with_suffix(".html")
    user_file.write_text("Kullanıcının dosyası", encoding="utf-8")
    report = write_report(source)
    assert report != user_file
    assert "eski" in report.read_text(encoding="utf-8")
    assert user_file.read_text(encoding="utf-8") == "Kullanıcının dosyası"
    assert source.read_text(encoding="utf-8") == old
    with pytest.raises(ValueError, match="oturum kaydı"):
        write_report(source, source)
    with pytest.raises(FileExistsError):
        write_report(source, user_file)
    if hasattr(Path, "hardlink_to"):
        alias = tmp_path / "alias.html"
        alias.hardlink_to(source)
        with pytest.raises(FileExistsError):
            write_report(source, alias)
        assert source.read_text(encoding="utf-8") == old


def test_jsonl_report_keeps_full_real_tool_result(tmp_path: Path) -> None:
    call = ToolCall(id="1", name="read_file", arguments='{"path":"a.txt"}')
    result = "\n".join(f"satır {n}" for n in range(100))
    log = SessionLogger(tmp_path / "events.jsonl")
    log.write(TaskStarted(task="oku"))
    log.write(ToolFinished(call=call, result=ToolResult(tool_call_id="1", content=result)))
    html = write_report(log.path).read_text(encoding="utf-8")
    assert "satır 99" in html and "a.txt" in html


def test_context_shows_what_was_sent_and_its_size_per_role() -> None:
    context = ContextAssembled(
        step=1,
        messages=(SystemMessage(text="talimat"), UserMessage(text="görevim")),
        tools=(ToolSpec(name="read_file", description="okur", parameters={}),),
    )
    html = render_report([TaskStarted(task="t"), context], "c.jsonl")
    assert "Modele giden bağlam · adım 1 · 2 mesaj · 1 araç" in html
    assert "system" in html and "talimat" in html and "görevim" in html
    assert "7 karakter" in html  # "talimat" ve "görevim" ayrı ayrı 7 karakter
