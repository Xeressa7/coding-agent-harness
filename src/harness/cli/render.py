"""Çekirdeğin olaylarını terminale çizer: model metni, araç çağrıları, onay istemleri (Rich)."""

import json
from typing import Any, Literal

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from harness.cli.inspector import code_blocks_summary, intervention_summary
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
from harness.messages import ToolCall

_MAX_SHOWN_LINES = 12

PermissionChoice = Literal["allow", "deny", "always"]
_CHOICES: dict[str, PermissionChoice] = {"e": "allow", "h": "deny", "o": "always"}


def render(console: Console, event: Event) -> None:
    """Tek bir olayı ekrana çizer. Ön yüzün çekirdekten bildiği tek şey olaylardır."""
    match event:
        case TaskStarted(task=task):
            console.rule("[bold]Yeni görev")
            console.print(Text.assemble(("Kullanıcı: ", "h.user"), task))
        case ModelReplied(step=step, text=text, tool_calls=calls, reasoning=reasoning):
            console.print()
            console.print(Text(f"[adım {step}] Model:", style="h.agent"))
            if reasoning:
                console.print(Text(f"düşünce · {reasoning}", style="h.thought"))
            if text:
                # Gerçek modeller cevabı Markdown ile biçimlendirir (kalın, liste, kod).
                console.print(Markdown(text))
            for call in calls:
                console.print(Text.assemble(("  → araç isteği: ", "h.warn"), _describe(call)))
        case ToolFinished(call=call, result=result, summary=summary, outcome=outcome):
            style = "h.error" if result.is_error else "h.ok"
            kind = "araç hatası (modele geri döndü)" if result.is_error else "araç sonucu"
            title = " · ".join(part for part in (kind, call.name, summary, outcome) if part)
            # Text(): dosya içeriğindeki [köşeli parantez] Rich biçim komutu sanılmasın.
            body = Text(_shorten(result.content))
            console.print(Panel(body, title=Text(title), border_style=style))
        case CodeBlocksSaved():
            console.print(Text(f"[harness] {code_blocks_summary(event)}", style="h.harness"))
        case HarnessIntervened():
            console.print(Text(f"[harness] {intervention_summary(event)}", style="h.harness"))
        case TaskFinished(reason="done", steps=steps):
            console.print()
            console.rule(f"[h.ok.strong]Görev bitti · {steps} adım")
        case TaskFinished(reason="step_limit", steps=steps):
            console.print()
            console.rule(f"[h.warn.strong]Adım sınırı doldu · {steps} adım")
        case TaskFinished(reason=reason, error=error):
            console.print(
                Text(f"{'Durduruldu' if reason == 'cancelled' else 'Hata'}: {error or '—'}")
            )
        case PermissionRequested(call=call):
            console.print(Text.assemble(("  ⚠ izin isteniyor: ", "h.warn"), _describe(call)))
        case (
            ContextAssembled()
            | ModelStarted()
            | ToolStarted()
            | ModelTextDelta()
            | ModelReasoningDelta()
        ):
            # Ayrıntılı replay tamamlanmış ModelReplied'ı zaten çizer; bağlam HTML incelemededir.
            pass


def ask_permission(console: Console, call: ToolCall) -> PermissionChoice:
    """PermissionRequested'ı render() yalnız gösterir; soruyu burada sorar.

    (e)vet (bu kez), (h)ayır, (o)turum için her zaman (bu araç bir daha sorulmaz).
    """
    # Text.assemble: araç argümanındaki [köşeli parantez] Rich biçim komutu sanılmasın.
    prompt = Text.assemble(
        ("İzin ver mi? ", "h.warn"), _describe(call), "  (e)vet / (h)ayır / (o)turum boyu"
    )
    choice = Prompt.ask(prompt, choices=list(_CHOICES), default="h", console=console)
    return _CHOICES[choice]


def _describe(call: ToolCall) -> str:
    try:
        args = json.loads(call.arguments)
    except json.JSONDecodeError:
        return f"{call.name}({call.arguments})"
    if not isinstance(args, dict):
        return f"{call.name}({call.arguments})"
    shown = ", ".join(f"{key}={value!r}" for key, value in args.items())  # type: ignore[reportUnknownVariableType]
    return f"{call.name}({shown})"


_PLAN_MARK = {"completed": "✓", "in_progress": "▸", "pending": "·"}


def live_outcome(event: ToolFinished) -> str:
    """Canlı ekranlar için tek satır: araç ve sonucu; yol, komut ve çıktı yok.

    Ayrıntılar akan sohbeti bölmesin, rapordan incelensin (kullanıcı tercihi, 2026-10-17).
    Hata metni yol içerebildiği için hatada yalnız "hata" yazılır.
    """
    if event.result.is_error:
        refused = event.outcome == "izin verilmedi"
        return f"! {event.call.name} · {'izin verilmedi' if refused else 'hata'}"
    return f"✓ {event.call.name}" + (f" · {event.outcome}" if event.outcome else "")


def plan_lines(details: dict[str, Any]) -> list[str]:
    """update_plan'ın kayda geçen adımları, ekranda gösterilecek satırlar olarak."""
    steps: list[dict[str, Any]] = details.get("steps") or []
    return [f"{_PLAN_MARK.get(str(s.get('status')), '·')} {s.get('step', '')}" for s in steps]


def _shorten(content: str) -> str:
    """Uzun sonucu ekranda kısaltır. Model tam hâlini görür; bu yalnız görüntü."""
    lines = content.splitlines()
    if len(lines) <= _MAX_SHOWN_LINES:
        return content
    hidden = len(lines) - _MAX_SHOWN_LINES
    return "\n".join([*lines[:_MAX_SHOWN_LINES], f"… (+{hidden} satır)"])
