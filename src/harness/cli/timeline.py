"""Zaman çizelgesi: bir oturum kaydını okunur bir koşu incelemesine çevirir.

İki saf aşama: `build_turns(events)` olaylardan turları, adımları, araç ve izin
aralıklarını çıkarır; `render_turn(turn)` onları HTML ve satır içi SVG'ye çizer.
Script yoktur: üzerine gelince açılan bilgi SVG <title>, ayrıntı <details>.
Kayıttaki her metin HTML olarak kaçışlanır.

Ölçüm ve tahmin ayrı tutulur: adım başına token sağlayıcının sayımıdır; dosya
başına token ise ölçülen artışın karakter payına göre bölüştürülmesidir ve
sayfada "≈" ile yazılır.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any

from harness.core.events import (
    CodeBlocksSaved,
    ContextAssembled,
    Event,
    HarnessIntervened,
    ModelReasoningDelta,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    PermissionRequested,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.messages import TokenUsage, ToolCall

CATEGORY_ORDER = ("read", "search", "write", "exec", "network", "plan", "other")
CATEGORY_LABEL = {
    "read": "Okuma",
    "search": "Arama",
    "write": "Yazma",
    "exec": "Komut",
    "network": "Ağ",
    "plan": "Plan",
    "other": "Diğer",
}
# Kategori alanı olmayan eski kayıtlar için araç adından tahmin.
_LEGACY_CATEGORY = {
    "read_file": "read",
    "list_dir": "read",
    "search": "search",
    "write_file": "write",
    "edit_file": "write",
    "delete_file": "write",
    "bash": "exec",
    "fetch_url": "network",
    "update_plan": "plan",
}
_DENIED = "izin verilmedi"


# ---------------------------------------------------------------- veri


@dataclass
class ToolSpan:
    call: ToolCall
    category: str
    summary: str
    outcome: str
    start: float | None
    end: float | None
    error: bool
    denied: bool
    chars: int
    details: dict[str, Any]
    tokens: float | None = None  # tahmin: sonraki adıma eklediği girdi tokenı


@dataclass
class Wait:
    name: str
    start: float | None
    end: float | None
    allowed: bool | None


@dataclass
class Step:
    number: int
    start: float | None = None
    end: float | None = None
    duration: float | None = None
    first_text: float | None = None
    usage: TokenUsage | None = None
    text: str = ""
    reasoning: str = ""
    replied: bool = False
    final: bool = False
    context_messages: int | None = None
    partial_text: str = ""
    tools: list[ToolSpan] = field(default_factory=lambda: [])
    waits: list[Wait] = field(default_factory=lambda: [])
    added_next: int | None = None

    @property
    def input_tokens(self) -> int | None:
        return self.usage.input_tokens if self.usage else None


@dataclass
class Turn:
    number: int
    task: str | None
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    anatomy: str | None = None
    started: datetime | None = None
    finish: TaskFinished | None = None
    steps: list[Step] = field(default_factory=lambda: [])
    code_blocks: list[CodeBlocksSaved] = field(default_factory=lambda: [])
    interventions: list[HarnessIntervened] = field(default_factory=lambda: [])

    @property
    def tools(self) -> list[ToolSpan]:
        return [t for s in self.steps for t in s.tools]

    @property
    def waits(self) -> list[Wait]:
        return [w for s in self.steps for w in s.waits]

    @property
    def duration(self) -> float | None:
        if self.finish and self.finish.duration_seconds is not None:
            return self.finish.duration_seconds
        ends = [s.end for s in self.steps if s.end is not None]
        ends += [t.end for t in self.tools if t.end is not None]
        return max(ends) if ends else None


def build_turns(events: list[Event]) -> list[Turn]:
    turns: list[Turn] = []
    origin: datetime | None = None
    started_at: dict[str, float | None] = {}
    waiting: dict[str, Wait] = {}

    def at(event: Event) -> float | None:
        if origin is None or event.timestamp is None:
            return None
        return (event.timestamp - origin).total_seconds()

    def current_turn() -> Turn:
        if not turns:
            turns.append(Turn(number=1, task=None))
        return turns[-1]

    def current_step(number: int | None = None) -> Step:
        turn = current_turn()
        if not turn.steps or (number is not None and turn.steps[-1].number != number):
            turn.steps.append(Step(number=number or len(turn.steps) + 1))
        return turn.steps[-1]

    pending_context: dict[int, int] = {}
    for event in events:
        match event:
            case TaskStarted():
                origin = event.timestamp
                turns.append(
                    Turn(
                        number=len(turns) + 1,
                        task=event.task,
                        provider=event.provider,
                        model=event.model,
                        effort=event.effort,
                        anatomy=event.anatomy.name if event.anatomy else None,
                        started=event.timestamp,
                    )
                )
                pending_context.clear()
            case ContextAssembled(step=step, messages=messages):
                pending_context[step] = len(messages)
            case ModelStarted(step=step):
                current_turn().steps.append(Step(number=step, start=at(event)))
                current_turn().steps[-1].context_messages = pending_context.get(step)
            case ModelTextDelta(step=step, text=text):
                current_step(step).partial_text += text
            case ModelReasoningDelta():
                pass  # tamamı ModelReplied.reasoning ile gelir; yarım kalanı ham kayıttadır
            case ModelReplied():
                step = current_step(event.step)
                step.replied = True
                step.end = at(event)
                step.duration = event.duration_seconds
                if step.start is None and step.end is not None and step.duration is not None:
                    step.start = max(step.end - step.duration, 0.0)
                step.first_text = event.first_text_seconds
                step.usage = event.usage
                step.text = event.text
                step.reasoning = event.reasoning
                step.final = not event.tool_calls
            case PermissionRequested(call=call):
                wait = Wait(name=call.name, start=at(event), end=None, allowed=None)
                waiting[call.id] = wait
                current_step().waits.append(wait)
            case ToolStarted(call=call):
                started_at[call.id] = at(event)
                if call.id in waiting:
                    wait = waiting.pop(call.id)
                    wait.end, wait.allowed = at(event), True
            case ToolFinished():
                current_step().tools.append(_span(event, started_at.pop(event.call.id, None), at))
                if event.call.id in waiting:
                    wait = waiting.pop(event.call.id)
                    wait.end, wait.allowed = at(event), False
            case CodeBlocksSaved():
                current_turn().code_blocks.append(event)
            case HarnessIntervened():
                current_turn().interventions.append(event)
            case TaskFinished():
                current_turn().finish = event
    for turn in turns:
        _estimate_tool_tokens(turn.steps)
    return turns


def _span(event: ToolFinished, start: float | None, at: Any) -> ToolSpan:
    end: float | None = at(event)
    denied = event.outcome == _DENIED or event.result.content.startswith("İzin verilmedi")
    if start is None and end is not None and event.duration_seconds is not None:
        start = max(end - event.duration_seconds, 0.0)
    outcome = event.outcome or (
        event.result.content.splitlines()[0][:120] if event.result.is_error else ""
    )
    return ToolSpan(
        call=event.call,
        category=event.category or _LEGACY_CATEGORY.get(event.call.name, "other"),
        summary=event.summary or _legacy_summary(event.call),
        outcome=_DENIED if denied else outcome,
        start=start,
        end=end,
        error=event.result.is_error,
        denied=denied,
        chars=len(event.result.content),
        details=event.details,
    )


def _legacy_summary(call: ToolCall) -> str:
    """Özet alanı olmayan eski kayıtta argümandan kısa anlatım."""
    try:
        args: Any = json.loads(call.arguments)
    except json.JSONDecodeError:
        return ""
    if not isinstance(args, dict):
        return ""
    values: dict[str, Any] = args  # pyright: ignore[reportUnknownVariableType]
    for key in ("path", "command", "url", "pattern"):
        if isinstance(values.get(key), str):
            text = str(values[key])
            return f"$ {text}" if key == "command" else text
    return ""


def _estimate_tool_tokens(steps: list[Step]) -> None:
    """Bir adımın araç sonuçları sonraki adımın girdisine eklenir. Ölçülen artışı
    sonuçların karakter payına göre bölüştürür: tahmin, ölçüm değil."""
    for current, following in zip(steps, steps[1:], strict=False):
        if current.input_tokens is None or following.input_tokens is None:
            continue
        delta = following.input_tokens - current.input_tokens
        current.added_next = delta
        counted = [t for t in current.tools if t.chars]
        total = sum(t.chars for t in counted)
        for tool in counted:
            tool.tokens = max(delta, 0) * tool.chars / total if total else None


# ---------------------------------------------------------------- biçim


def fmt_int(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def fmt_k(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}K".replace(".", ",")
    return str(round(value))


def fmt_s(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 10:
        return f"{value:.1f} sn".replace(".", ",")
    return f"{value:.0f} sn"


def _usage_sum(steps: list[Step], name: str) -> int | None:
    values = [getattr(s.usage, name) if s.usage else None for s in steps if s.replied]
    if not values or any(v is None for v in values):
        return None
    return sum(v for v in values if v is not None)


def _cached_text(steps: list[Step]) -> str:
    """Önbellekten okunan; bazı adımlar sayı vermediyse "en az" der, uydurmaz."""
    values = [s.usage.cached_tokens if s.usage else None for s in steps if s.replied]
    known = [v for v in values if v is not None]
    if not known or not sum(known):
        return ""
    prefix = "" if len(known) == len(values) else "en az "
    return prefix + fmt_k(sum(known))


def _no_cache(steps: list[Step]) -> str:
    values = [s.usage.cached_tokens if s.usage else None for s in steps if s.replied]
    if values and all(v == 0 for v in values):
        return "; önbellek kullanılmadı."
    return "."


# ---------------------------------------------------------------- çizim


def render_turn(turn: Turn) -> str:
    total = turn.duration
    meta = " · ".join(
        part
        for part in (
            turn.provider,
            turn.model,
            f"effort {turn.effort}" if turn.effort else None,
            f"anatomi: {turn.anatomy}" if turn.anatomy else None,
        )
        if part
    )
    parts = [
        f"<p class='prompt'>{escape(turn.task or 'Görev başlangıcı kayıtta yok')}</p>",
        f"<p class='meta'>{escape(meta or 'Model bilgisi kayıtta yok')}</p>",
        _tiles(turn, total),
        _findings(turn, total),
        _timeline_svg(turn, total),
        _tokens_svg(turn),
        _steps_html(turn, total),
        _plan_html(turn),
        _files_html(turn),
    ]
    return "".join(p for p in parts if p)


def _tiles(turn: Turn, total: float | None) -> str:
    replied = [s for s in turn.steps if s.replied]
    model_seconds = sum(s.duration or 0 for s in replied)
    tools = turn.tools
    by_cat: dict[str, int] = {}
    for tool in tools:
        by_cat[tool.category] = by_cat.get(tool.category, 0) + 1
    breakdown = ", ".join(
        f"{by_cat[c]} {CATEGORY_LABEL[c].lower()}" for c in CATEGORY_ORDER if c in by_cat
    )
    tin, tout = _usage_sum(turn.steps, "input_tokens"), _usage_sum(turn.steps, "output_tokens")
    cached = _cached_text(turn.steps)
    last_input = replied[-1].input_tokens if replied else None
    token_note = " · ".join(
        part
        for part in (
            f"son bağlam {fmt_k(last_input)}"
            if last_input is not None and len(replied) > 1
            else "",
            f"önbellek {cached}" if cached else "",
            f"çıktı {fmt_k(tout)}" if tout is not None else "",
        )
        if part
    )
    share = f"%{100 * model_seconds / total:.0f}" if total else "—"
    tool_seconds = sum((t.end or 0) - (t.start or 0) for t in tools if t.start is not None)
    tiles = [
        ("Toplam süre", fmt_s(total), f"{len(replied)} model çağrısı"),
        ("Model bekleme", share, f"araçlar {fmt_s(tool_seconds)}" if tools else "araç yok"),
        ("Araç çağrısı", str(len(tools)), breakdown or "—"),
        ("Modele giden token", fmt_k(tin) if tin is not None else "—", token_note or "sayım yok"),
    ]
    return (
        "<div class='tiles'>"
        + "".join(
            f"<div class='tile'><div class='tl'>{escape(a)}</div><div class='tv'>{escape(b)}</div>"
            f"<div class='ts'>{escape(c)}</div></div>"
            for a, b, c in tiles
        )
        + "</div>"
    )


def _findings(turn: Turn, total: float | None) -> str:
    """Kayıttan otomatik çıkan gözlemler; veri yoksa o gözlem yazılmaz."""
    replied = [s for s in turn.steps if s.replied]
    items: list[str] = []
    model_seconds = sum(s.duration or 0 for s in replied)
    tools = turn.tools
    if total and replied:
        tool_seconds = sum((t.end or 0) - (t.start or 0) for t in tools if t.start is not None)
        items.append(
            f"Sürenin <b>%{100 * model_seconds / total:.0f}</b>’i modeli beklemekle geçti"
            + (f"; {len(tools)} araç çağrısı toplam {fmt_s(tool_seconds)} sürdü." if tools else ".")
        )
    waits = [w for w in turn.waits if w.start is not None and w.end is not None]
    if waits:
        waited = sum((w.end or 0) - (w.start or 0) for w in waits)
        refused = sum(w.allowed is False for w in waits)
        items.append(
            f"{len(waits)} kez izin istendi, cevap beklerken {fmt_s(waited)} geçti"
            + (f"; {refused} istek reddedildi." if refused else ".")
        )
    longest = max(replied, key=lambda s: s.duration or 0, default=None)
    if longest and longest.duration and len(replied) > 1:
        thought = (
            f"; ilk kelimeden önce {fmt_s(longest.first_text)} düşündü"
            if longest.first_text
            else ""
        )
        items.append(f"En uzun adım {longest.number}: <b>{fmt_s(longest.duration)}</b>{thought}.")
    silent = [s for s in replied if not s.final and not s.text.strip() and not s.reasoning]
    if silent:
        items.append(
            f"{len(silent)} adımda model araç çağırdı ama ne yaptığını yazmadı "
            "(metin de düşünce özeti de yok)."
        )
    if any(s.reasoning for s in replied):
        items.append("Sağlayıcı düşünce özeti verdi; her adımın “neden”i adım kartlarında.")
    tin = _usage_sum(turn.steps, "input_tokens")
    if tin is not None and len(replied) > 1 and replied[-1].input_tokens:
        cached = _cached_text(turn.steps)
        items.append(
            f"Son bağlam {fmt_k(replied[-1].input_tokens)} token ama toplamda <b>{fmt_k(tin)}</b> "
            "gönderildi: geçmiş her adımda baştan yollanır"
            + (f"; önbellekten {cached}." if cached else _no_cache(turn.steps))
        )
    errors = [t for t in tools if t.error and not t.denied]
    if errors:
        names = ", ".join(sorted({t.call.name for t in errors}))
        items.append(
            f"{len(errors)} araç çağrısı hata döndü ({escape(names)}); model hatayı okudu."
        )
    written = sorted(
        {str(t.details.get("path")) for t in tools if t.category == "write" and not t.error}
        - {"None"}
    )
    if written:
        shown = ", ".join(f"<code>{escape(p)}</code>" for p in written[:6])
        more = f" ve {len(written) - 6} dosya daha" if len(written) > 6 else ""
        items.append(f"Değişen dosyalar: {shown}{more}.")
    if not items:
        return ""
    return (
        "<section class='card'><h3>Bu turda ne oldu?</h3><ul class='findings'>"
        + "".join(f"<li>{item}</li>" for item in items)
        + "</ul></section>"
    )


def _tick_step(total: float) -> float:
    for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200):
        if total / step <= 10:
            return step
    return 3600


@dataclass
class _Block:
    category: str
    start: float
    end: float
    tools: list[ToolSpan]


def _blocks(turn: Turn) -> list[_Block]:
    """Aynı adımda art arda gelen aynı kategorideki araçlar tek blok olur (×sayı)."""
    blocks: list[_Block] = []
    for step in turn.steps:
        for tool in step.tools:
            if tool.start is None or tool.end is None:
                continue
            last = blocks[-1] if blocks else None
            same_step = last is not None and any(t is last.tools[-1] for t in step.tools)
            if last is not None and same_step and last.category == tool.category:
                last.end = max(last.end, tool.end)
                last.tools.append(tool)
            else:
                blocks.append(_Block(tool.category, tool.start, tool.end, [tool]))
    return blocks


def _timeline_svg(turn: Turn, total: float | None) -> str:
    timed = [s for s in turn.steps if s.start is not None and s.end is not None]
    if not total or not timed:
        return (
            "<section class='card'><h3>Zaman çizelgesi</h3>"
            "<p class='sub'>Bu kayıtta zaman bilgisi yok; çizelge çizilemedi.</p></section>"
        )
    blocks = _blocks(turn)
    lanes = ["model"] + [c for c in CATEGORY_ORDER if any(b.category == c for b in blocks)]
    if any(w.start is not None for w in turn.waits):
        lanes.append("wait")
    width, left, right, top, lane_h, gap = 960, 96, 24, 12, 26, 12
    plot = width - left - right
    height = top + len(lanes) * (lane_h + gap) + 26

    def x(seconds: float) -> float:
        return left + plot * min(max(seconds, 0.0), total) / total

    def y(lane: str) -> float:
        return top + lanes.index(lane) * (lane_h + gap)

    out = [
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-labelledby='tl-{turn.number}' "
        "class='chart'>",
        f"<title id='tl-{turn.number}'>Tur {turn.number} zaman çizelgesi</title>",
    ]
    tick = _tick_step(total)
    value = 0.0
    while value <= total + 1e-9:
        px = x(value)
        out.append(
            f"<line class='grid' x1='{px:.1f}' x2='{px:.1f}' y1='{top}' y2='{height - 24}'/>"
        )
        label = f"{value:g} sn".replace(".", ",")
        out.append(
            f"<text class='axis' x='{px:.1f}' y='{height - 8}' text-anchor='middle'>{label}</text>"
        )
        value += tick
    names = {"model": "Model", "wait": "İzin", **CATEGORY_LABEL}
    for lane in lanes:
        out.append(
            f"<text class='lane' x='0' y='{y(lane) + lane_h / 2 + 4:.1f}'>{names[lane]}</text>"
        )
    for step in timed:
        out.append(_model_bar(step, x, y("model"), lane_h))
    for block in blocks:
        out.append(_tool_block(block, x, y(block.category), lane_h))
    for wait in turn.waits:
        if wait.start is None:
            continue
        end = wait.end if wait.end is not None else wait.start
        x0, x1 = x(wait.start), x(end)
        answer = {True: "izin verildi", False: "reddedildi", None: "cevapsız"}[wait.allowed]
        tip = f"{wait.name} için izin beklendi · {fmt_s(end - wait.start)} · {answer}"
        out.append(
            f"<g class='hit'><title>{escape(tip)}</title>"
            f"<rect class='wait' x='{x0:.1f}' y='{y('wait') + 4}' width='{max(x1 - x0, 3):.1f}' "
            f"height='{lane_h - 8}' rx='3'/></g>"
        )
    out.append("</svg>")
    legend = "".join(
        f"<span><i class='sw sw-{lane}'></i>{escape(names[lane])}</span>" for lane in lanes
    )
    return (
        "<section class='card'><h3>Zaman çizelgesi</h3>"
        "<p class='sub'>Soldan sağa geçen süre. Bir bloğun üzerine gelince ne yapıldığı çıkar; "
        "×sayı aynı adımda art arda yapılan çağrılardır.</p>"
        + "".join(out)
        + "<p class='legend'><span><i class='sw sw-think'></i>Model ilk kelimeden önce</span>"
        + legend
        + "</p></section>"
    )


def _model_bar(step: Step, x: Any, top: float, height: float) -> str:
    assert step.start is not None and step.end is not None
    x0, x1 = x(step.start), x(step.end)
    u = step.usage
    tip = [f"Adım {step.number} · model {fmt_s(step.end - step.start)}"]
    if u:
        tip.append(
            f"girdi {fmt_int(u.input_tokens or 0)} · çıktı {fmt_int(u.output_tokens or 0)}"
            + (f" (düşünme {fmt_int(u.reasoning_tokens)})" if u.reasoning_tokens else "")
        )
    pieces: list[str] = []
    split = step.first_text is not None and step.start + step.first_text < step.end
    if split and step.first_text is not None:
        xf = x(step.start + step.first_text)
        tip.append(f"ilk kelimeye kadar {fmt_s(step.first_text)}")
        pieces.append(
            f"<rect class='think' x='{x0 + 1:.1f}' y='{top}' width='{max(xf - x0 - 2, 1):.1f}' "
            f"height='{height}' rx='4'/>"
        )
        pieces.append(
            f"<rect class='model' x='{xf + 1:.1f}' y='{top}' width='{max(x1 - xf - 2, 1):.1f}' "
            f"height='{height}' rx='4'/>"
        )
    else:
        pieces.append(
            f"<rect class='model' x='{x0 + 1:.1f}' y='{top}' width='{max(x1 - x0 - 2, 2):.1f}' "
            f"height='{height}' rx='4'/>"
        )
    label = str(step.number)
    if x1 - x0 > len(label) * 8 + 8:
        pieces.append(
            f"<text class='inbar' x='{(x0 + x1) / 2:.1f}' y='{top + height / 2 + 4:.1f}' "
            f"text-anchor='middle'>{label}</text>"
        )
    return f"<g class='hit'><title>{escape(chr(10).join(tip))}</title>{''.join(pieces)}</g>"


def _tool_block(block: _Block, x: Any, top: float, height: float) -> str:
    x0, x1 = x(block.start), x(block.end)
    lines = [f"{CATEGORY_LABEL[block.category]} · {len(block.tools)} çağrı"]
    for tool in block.tools:
        mark = "!" if tool.error else "·"
        detail = " · ".join(p for p in (tool.summary, tool.outcome) if p)
        lines.append(f"{mark} {tool.call.name}" + (f": {detail}" if detail else ""))
    errors = any(t.error for t in block.tools)
    width = max(x1 - x0, 4)
    label = (f"×{len(block.tools)}" if len(block.tools) > 1 else "") + (" !" if errors else "")
    text = (
        f"<text class='axis' x='{x0 + width + 4:.1f}' y='{top + height / 2 + 4:.1f}'>"
        f"{escape(label.strip())}</text>"
        if label.strip()
        else ""
    )
    css = "err" if errors else f"cat-{block.category}"
    return (
        f"<g class='hit'><title>{escape(chr(10).join(lines))}</title>"
        f"<rect class='{css}' x='{x0:.1f}' y='{top + 3}' width='{width:.1f}' "
        f"height='{height - 6}' rx='3'/>{text}</g>"
    )


def _tokens_svg(turn: Turn) -> str:
    steps = [s for s in turn.steps if s.replied and s.input_tokens is not None]
    if len(steps) < 2:
        return ""
    width, height, left, bottom, top = 960, 240, 56, 34, 22
    plot_h = height - bottom - top
    peak = max(s.input_tokens or 0 for s in steps)
    scale = _nice_ceiling(peak)
    band = (width - left - 10) / len(steps)
    bar = min(40.0, band * 0.55)

    def y(value: float) -> float:
        return top + plot_h * (1 - value / scale)

    out = [
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-labelledby='tk-{turn.number}' "
        "class='chart'>",
        f"<title id='tk-{turn.number}'>Adım başına modele giden girdi tokenı</title>",
    ]
    for tick in range(0, scale + 1, scale // 4):
        gy = y(tick)
        out.append(
            f"<line class='grid' x1='{left}' x2='{width - 10}' y1='{gy:.1f}' y2='{gy:.1f}'/>"
        )
        out.append(
            f"<text class='axis' x='{left - 8}' y='{y(tick) + 4:.1f}' text-anchor='end'>"
            f"{fmt_k(tick) if tick else 0}</text>"
        )
    previous = 0
    for i, step in enumerate(steps):
        value = step.input_tokens or 0
        carried, new = min(previous, value), max(value - previous, 0)
        center = left + band * i + band / 2
        x0 = center - bar / 2
        tip = (
            f"Adım {step.number} · modele giden {fmt_int(value)} token\n"
            f"önceki adımlardan tekrar: {fmt_int(carried)}\nbu adımda yeni: {fmt_int(new)}"
        )
        if step.usage and step.usage.cached_tokens:
            tip += f"\nönbellekten: {fmt_int(step.usage.cached_tokens)}"
        rects = ""
        if carried:
            rects += (
                f"<rect class='carried' x='{x0:.1f}' y='{y(carried):.1f}' width='{bar:.1f}' "
                f"height='{y(0) - y(carried):.1f}'/>"
            )
        if new:
            gap = 2 if carried else 0
            shape = _round_top(x0, y(value), bar, max(y(carried) - y(value) - gap, 1))
            rects += f"<path class='fresh' d='{shape}'/>"
        out.append(
            f"<g class='hit'><title>{escape(tip)}</title>{rects}"
            f"<rect x='{x0 - 6:.1f}' y='{top}' width='{bar + 12:.1f}' height='{plot_h}' "
            "fill='transparent'/></g>"
        )
        out.append(
            f"<text class='axis' x='{center:.1f}' y='{height - 12}' text-anchor='middle'>"
            f"Adım {step.number}</text>"
        )
        if i in (0, len(steps) - 1):
            out.append(
                f"<text class='val' x='{center:.1f}' y='{y(value) - 7:.1f}' text-anchor='middle'>"
                f"{fmt_k(value)}</text>"
            )
        previous = value
    out.append("</svg>")
    return (
        "<section class='card'><h3>Token nereye gitti?</h3>"
        "<p class='sub'>Her adımda geçmişin tamamı modele yeniden gönderilir. Gri: önceki "
        "adımlardan tekrar; mavi: o adımda yeni eklenen (çoğu araç sonucu).</p>"
        + "".join(out)
        + "<p class='legend'><span><i class='sw sw-carried'></i>Önceki adımlardan tekrar</span>"
        "<span><i class='sw sw-fresh'></i>Bu adımda yeni</span></p></section>"
    )


def _nice_ceiling(value: float) -> int:
    for nice in (1_000, 2_000, 4_000, 8_000, 10_000, 20_000, 40_000, 80_000, 100_000):
        if value <= nice:
            return nice
    return int((value // 100_000 + 1) * 100_000)


def _round_top(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    r = min(r, h, w / 2)
    return (
        f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z"
    )


def _steps_html(turn: Turn, total: float | None) -> str:
    if not turn.steps:
        return ""
    rows: list[str] = []
    for step in turn.steps:
        rows.append(_step_html(step, total))
    return (
        "<section class='card'><h3>Adım adım</h3><p class='sub'>Her adımda model bir karar "
        "verir, harness istenen araçları çalıştırır. ≈ sayı, o aracın sonucunun bir sonraki "
        "adımda bağlama eklediği tahmini tokendır.</p><ol class='steps'>"
        + "".join(rows)
        + "</ol></section>"
    )


def _step_html(step: Step, total: float | None) -> str:
    share = 100 * (step.duration or 0) / total if total else 0
    body: list[str] = []
    if step.reasoning:
        short_text = step.reasoning if len(step.reasoning) <= 400 else step.reasoning[:399] + "…"
        body.append(f"<p class='why'><span>Düşünce özeti</span>{escape(short_text)}</p>")
        if len(step.reasoning) > 400:
            body.append(
                f"<details><summary>Düşünce özetinin tamamı</summary>"
                f"<pre>{escape(step.reasoning)}</pre></details>"
            )
    if step.text and not step.final:
        body.append(f"<p class='say'>“{escape(step.text[:300])}”</p>")
    if step.tools:
        counts: dict[str, int] = {}
        for tool in step.tools:
            counts[tool.category] = counts.get(tool.category, 0) + 1
        what = " · ".join(
            f"{counts[c]} {CATEGORY_LABEL[c].lower()}" for c in CATEGORY_ORDER if c in counts
        )
        body.append(f"<div class='what'>{escape(what)}</div><div class='chips'>")
        for tool in step.tools:
            body.append(_chip(tool))
        body.append("</div>")
    for wait in step.waits:
        if wait.start is not None and wait.end is not None:
            answer = "verildi" if wait.allowed else "reddedildi"
            body.append(
                f"<p class='wait-note'>İzin: {escape(wait.name)} · {fmt_s(wait.end - wait.start)} "
                f"bekledi · {answer}</p>"
            )
    if step.final and step.text:
        body.append(
            f"<div class='what'>Cevabı yazdı · {fmt_int(len(step.text))} karakter</div>"
            f"<details><summary>Cevabı göster</summary><pre>{escape(step.text)}</pre></details>"
        )
    if not step.replied:
        body.append("<div class='what warn'>Yanıt tamamlanmadı.</div>")
        if step.partial_text:
            body.append(f"<pre>{escape(step.partial_text)}</pre>")
    u = step.usage
    stats = (
        (
            f"<div><b>{fmt_int(u.input_tokens)}</b> girdi</div>"
            if u and u.input_tokens is not None
            else ""
        )
        + (
            f"<div><b>{fmt_int(u.output_tokens)}</b> çıktı</div>"
            if u and u.output_tokens is not None
            else ""
        )
        + (
            f"<div class='muted'>{fmt_int(u.reasoning_tokens)} düşünme</div>"
            if u and u.reasoning_tokens
            else ""
        )
        + (
            f"<div class='muted'>{fmt_int(u.cached_tokens)} önbellek</div>"
            if u and u.cached_tokens
            else ""
        )
    )
    return (
        f"<li class='step'><div class='sh'><div class='sn'>Adım {step.number}</div>"
        f"<div class='sm'>{fmt_s(step.duration)}</div>"
        f"<div class='bar'><span style='width:{share:.1f}%'></span></div></div>"
        f"<div class='sb'>{''.join(body)}</div><div class='st'>{stats or '—'}</div></li>"
    )


def _chip(tool: ToolSpan) -> str:
    state = " err" if tool.error else ""
    label = escape(tool.summary or tool.call.name)
    outcome = f"<span class='co'>{escape(tool.outcome)}</span>" if tool.outcome else ""
    tokens = f"<span class='ct'>≈{fmt_k(tool.tokens)}</span>" if tool.tokens else ""
    return (
        f"<span class='chip cat-{tool.category}{state}'>"
        f"<span class='cn'>{escape(tool.call.name)}</span>"
        f"<span class='cs'>{label if tool.summary else ''}</span>{outcome}{tokens}</span>"
    )


def _plan_html(turn: Turn) -> str:
    snapshots = [
        (step.number, tool.details)
        for step in turn.steps
        for tool in step.tools
        if tool.call.name == "update_plan" and not tool.error and tool.details.get("steps")
    ]
    if not snapshots:
        return ""
    mark = {"completed": "✓", "in_progress": "▸", "pending": "·"}
    final_steps: list[dict[str, Any]] = snapshots[-1][1].get("steps") or []
    final = "".join(
        f"<li class='plan-{escape(str(s.get('status')))}'>{mark.get(str(s.get('status')), '·')} "
        f"{escape(str(s.get('step', '')))}</li>"
        for s in final_steps
    )
    history = " → ".join(
        f"adım {number}: {d.get('completed', 0)}/{d.get('total', 0)}" for number, d in snapshots
    )
    return (
        "<section class='card'><h3>Plan</h3><p class='sub'>Modelin kendi yazdığı plan; "
        f"{len(snapshots)} kez güncellendi ({escape(history)}). Son hâli:</p>"
        f"<ul class='plan'>{final}</ul></section>"
    )


def _files_html(turn: Turn) -> str:
    files: dict[str, dict[str, Any]] = {}
    for step in turn.steps:
        for tool in step.tools:
            path = tool.details.get("path") or (
                tool.summary.split(" · ")[0] if tool.category in {"read", "write"} else None
            )
            if not path or tool.call.name == "list_dir":
                continue
            entry = files.setdefault(str(path), {"ops": [], "steps": [], "tokens": 0.0})
            verb = {"read": "okundu", "write": "yazıldı"}.get(tool.category, tool.call.name)
            if tool.call.name == "edit_file":
                verb = "düzenlendi"
            elif tool.call.name == "delete_file":
                verb = "silindi"
            if tool.error:
                verb += " (hata)"
            if verb not in entry["ops"]:
                entry["ops"].append(verb)
            if step.number not in entry["steps"]:
                entry["steps"].append(step.number)
            entry["tokens"] += tool.tokens or 0
    if not files:
        return ""
    ordered = sorted(files.items(), key=lambda kv: (-kv[1]["tokens"], kv[0]))[:15]
    peak = max((f["tokens"] for _, f in ordered), default=0) or 1
    rows = "".join(
        f"<tr><td class='fn'>{escape(path)}</td><td>{escape(', '.join(f['ops']))}</td>"
        f"<td>{', '.join(str(n) for n in f['steps'])}</td>"
        f"<td class='num'>{'≈' + fmt_k(f['tokens']) if f['tokens'] else '—'}</td>"
        f"<td class='fb'><span style='width:{100 * f['tokens'] / peak:.0f}%'></span></td></tr>"
        for path, f in ordered
    )
    return (
        f"<section class='card'><h3>Dosyalar</h3><p class='sub'>{len(files)} dosyaya dokunuldu; "
        "en çok bağlam kaplayan en üstte.</p><table><thead><tr><th>Dosya</th><th>İşlem</th>"
        "<th>Adım</th><th class='num'>Token</th><th></th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<p class='note'>≈ token: ölçülen girdi artışı, o adımdaki araç sonuçlarına karakter "
        "oranıyla bölüştürüldü. Tahmindir; adım toplamları ölçümdür.</p></section>"
    )
