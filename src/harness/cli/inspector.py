"""JSONL kaydından çevrimdışı, çalıştırılabilir içerik barındırmayan inceleme raporu."""

import json
from collections.abc import Sequence
from html import escape
from pathlib import Path

from harness.core.events import (
    CodeBlocksSaved,
    ContextAssembled,
    Event,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.core.session_log import read_session
from harness.messages import AssistantMessage, Message, ToolResult

_STATUS = {
    "done": "Tamamlandı",
    "step_limit": "Adım sınırı",
    "error": "Hata",
    "cancelled": "Durduruldu",
}


def code_blocks_summary(event: CodeBlocksSaved) -> str:
    """Seviye 0'da harness'ın cevaptan çıkarıp yazdığı dosyalar; bütün ön yüzler bunu yazar."""
    parts = [
        f"{len(event.saved)} dosya kaydedildi"
        + (f": {', '.join(event.saved)}" if event.saved else "")
    ]
    if event.rejected:
        parts.append(f"reddedilen: {', '.join(event.rejected)}")
    if event.unnamed:
        parts.append(f"yolsuz blok: {event.unnamed}")
    return " · ".join(parts)


def summary_items(events: Sequence[Event]) -> list[tuple[str, str]]:
    """Aynı ölçüm tanımları terminal ve HTML'de kullanılır; kısmi toplam uydurulmaz."""
    replies = [e for e in events if isinstance(e, ModelReplied)]
    tools = [e for e in events if isinstance(e, ToolFinished)]
    finishes = [e for e in events if isinstance(e, TaskFinished)]
    finish = finishes[-1] if finishes else None
    complete = finish is not None and finish.reason in {"done", "step_limit"}
    model_seconds = (
        sum(e.duration_seconds for e in replies if e.duration_seconds is not None)
        if complete and replies and all(e.duration_seconds is not None for e in replies)
        else None
    )
    inputs = [e.usage.input_tokens if e.usage else None for e in replies]
    outputs = [e.usage.output_tokens if e.usage else None for e in replies]
    input_total = (
        sum(n for n in inputs if n is not None)
        if complete and inputs and all(n is not None for n in inputs)
        else None
    )
    output_total = (
        sum(n for n in outputs if n is not None)
        if complete and outputs and all(n is not None for n in outputs)
        else None
    )
    return [
        ("Durum", _STATUS[finish.reason] if finish else "Yarım kaldı"),
        ("Tur süresi", seconds(finish.duration_seconds if finish else None)),
        ("Model süresi", seconds(model_seconds)),
        ("Model yanıtı", str(len(replies))),
        ("Araç sonucu / hata", f"{len(tools)} / {sum(e.result.is_error for e in tools)}"),
        ("Girdi tokenı", str(input_total) if input_total is not None else "—"),
        ("Çıktı tokenı", str(output_total) if output_total is not None else "—"),
        (
            "Ortalama token/sn",
            f"{output_total / model_seconds:.2f}"
            if output_total is not None and model_seconds and model_seconds > 0
            else "—",
        ),
    ]


def seconds(value: float | None) -> str:
    return f"{value:.3f} sn" if value is not None else "—"


def _pre(text: str) -> str:
    return f"<pre>{escape(text)}</pre>"


def _message_text(message: Message) -> str:
    """Mesajın modele giden metni; araç istekleri ad ve ham argümanla yazılır."""
    match message:
        case AssistantMessage(text=text, tool_calls=calls):
            return "\n".join([text, *(f"{c.name}({c.arguments})" for c in calls)]).strip()
        case ToolResult(content=content):
            return content
        case _:
            return message.text


def _event_html(event: Event) -> str:
    detail = ""
    match event:
        case TaskStarted():
            title = "Görev başladı"
        case ContextAssembled(step=step, messages=messages, tools=tools):
            title = f"Modele giden bağlam · adım {step} · {len(messages)} mesaj · {len(tools)} araç"
            for message in messages:
                text = _message_text(message)
                detail += f"<h4>{escape(message.role)} · {len(text)} karakter</h4>" + _pre(text)
            for spec in tools:
                schema = f"{spec.description}\n{json.dumps(spec.parameters, ensure_ascii=False)}"
                detail += f"<h4>Araç şeması: {escape(spec.name)}</h4>" + _pre(schema)
        case ModelStarted(step=step):
            title = f"Model çağrısı başladı · adım {step}"
        case ModelTextDelta(step=step, text=text):
            title = f"Kısmi model metni · adım {step} (tamamlanmadı)"
            detail = _pre(text)
        case ModelReplied(step=step):
            title = f"Model yanıtı · adım {step} · {seconds(event.duration_seconds)}"
            detail = _pre(event.text) if event.text else "<p>Metin yok; araç isteği olabilir.</p>"
            detail += f"<p>İlk metne kadar: {seconds(event.first_text_seconds)}</p>"
            if event.usage:
                usage = event.usage
                fields = [
                    ("Girdi", usage.input_tokens),
                    ("Çıktı", usage.output_tokens),
                    ("Cache (girdinin alt kümesi)", usage.cached_tokens),
                    ("Reasoning (çıktının alt kümesi)", usage.reasoning_tokens),
                ]
                detail += (
                    "<dl class='metrics'>"
                    + "".join(
                        f"<div><dt>{escape(label)}</dt><dd>{n if n is not None else '—'}</dd></div>"
                        for label, n in fields
                    )
                    + "</dl>"
                )
            else:
                detail += "<p>Token kullanımı: — (bildirilmedi)</p>"
            for call in event.tool_calls:
                detail += f"<h4>Araç isteği: {escape(call.name)} · {escape(call.id)}</h4>"
                detail += _pre(call.arguments)
        case ToolStarted(call=call):
            title = f"Araç başladı · {call.name} · {call.id}"
            detail = _pre(call.arguments)
        case ToolFinished(call=call, result=result):
            state = "Hata" if result.is_error else "Sonuç alındı"
            title = f"{state} · {call.name} · {call.id} · {seconds(event.duration_seconds)}"
            detail = "<h4>Argümanlar</h4>" + _pre(call.arguments)
            detail += "<h4>Gerçek araç sonucu</h4>" + _pre(result.content)
        case CodeBlocksSaved(step=step):
            title = f"Harness cevaptaki kod bloklarını yazdı · adım {step}"
            detail = f"<p>{escape(code_blocks_summary(event))}</p>"
        case TaskFinished():
            title = f"{_STATUS[event.reason]} · {event.steps} adım"
            detail = _pre(event.error) if event.error else ""
    stamp = event.timestamp.isoformat() if event.timestamp else "Zaman: —"
    if not detail:
        return f"<li><p>{escape(title)}</p><small>{escape(stamp)}</small></li>"
    return (
        f"<li><details><summary>{escape(title)}</summary>"
        f"<small>{escape(stamp)}</small>{detail}</details></li>"
    )


def render_report(events: Sequence[Event], source: str) -> str:
    turns: list[list[Event]] = []
    for event in events:
        if isinstance(event, TaskStarted) or not turns:
            turns.append([])
        turns[-1].append(event)
    sections: list[str] = []
    for number, turn in enumerate(turns, start=1):
        start = next((e for e in turn if isinstance(e, TaskStarted)), None)
        prompt = start.task if start else "Görev başlangıcı kayıtta yok"
        model = start.model if start and start.model else "—"
        effort = start.effort if start and start.effort else "—"
        metrics = "".join(
            f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>"
            for label, value in summary_items(turn)
        )
        completed_steps = {e.step for e in turn if isinstance(e, ModelReplied)}
        visible = [
            e for e in turn if not isinstance(e, ModelTextDelta) or e.step not in completed_steps
        ]
        sections.append(
            f"<section aria-labelledby='turn-{number}'>"
            f"<h2 id='turn-{number}'>Tur {number}</h2>"
            f"<p class='prompt'>{escape(prompt)}</p>"
            f"<p>Model: <strong>{escape(model)}</strong> · Effort: {escape(effort)}</p>"
            f"<dl class='metrics'>{metrics}</dl>"
            "<h3>İşlem kaydı</h3><ol>"
            + "".join(_event_html(e) for e in visible)
            + "</ol></section>"
        )
    content = "".join(sections) if sections else "<p>Bu kayıtta henüz olay yok.</p>"
    css = Path(__file__).with_name("inspector.css").read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; '
        'base-uri &#39;none&#39;; form-action &#39;none&#39;">'
        f"<title>Harness · Oturum incelemesi</title><style>{css}</style></head>"
        '<body><a class="skip" href="#main">Kayda geç</a><main id="main">'
        '<header><p class="eyebrow">HARNESS / GÖREV SONRASI</p>'
        "<h1>Oturum incelemesi</h1>"
        f'<p class="source">{escape(source)}</p>'
        "<p>Yerel kayıt; istemler ve dosya içerikleri hassas bilgi içerebilir. "
        "Paylaşmadan önce kontrol edin.</p></header>"
        f"{content}<footer><h2>Ölçümler nasıl okunur?</h2>"
        "<p>—: ölçülmedi veya tam toplam için veri eksik. Ortalama token/sn = çıktı "
        "tokenı / toplam model çağrısı süresi; saf üretim hızı değildir. "
        "Model süresi ağ ve sağlayıcı beklemesini de içerir. "
        "İlk metin süresi ilk boş olmayan metin parçasına kadardır; araç-only yanıtta yoktur.</p>"
        "<p>Cache ve reasoning tokenları alt kümedir, toplam tokenlara tekrar eklenmez. "
        "Reasoning sayımı iç düşünce metni değildir. Bu ekran yalnız gözlemlenebilir "
        "açıklama, çağrı ve sonuçları gösterir. Tamamlandı, model döngüsünün sona "
        "erdiğini belirtir; testlerin geçtiğini garanti etmez.</p>"
        "</footer></main></body></html>"
    )


def write_report(log_path: Path, output: Path | None = None) -> Path:
    """Kullanıcı dosyalarını ezmez; otomatik hedef doluysa yeni bir ad seçer."""
    if output is not None and output.resolve() == log_path.resolve():
        raise ValueError("Rapor hedefi oturum kaydı olamaz.")
    html = render_report(read_session(log_path), str(log_path))
    target = output or log_path.with_suffix(".html")
    index = 1
    while True:
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(html)
            return target
        except FileExistsError:
            if output is not None:
                raise
            index += 1
            target = log_path.with_name(f"{log_path.stem}-{index}.html")
