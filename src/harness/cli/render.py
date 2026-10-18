"""Çekirdeğin olaylarını terminale çizer: model metni, araç çağrıları, onay istemleri (Rich)."""

import json

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from harness.core.events import (
    Event,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.messages import ToolCall

_MAX_SHOWN_LINES = 12


def render(console: Console, event: Event) -> None:
    """Tek bir olayı ekrana çizer. Ön yüzün çekirdekten bildiği tek şey olaylardır."""
    match event:
        case TaskStarted(task=task):
            console.rule("[bold]Yeni görev")
            console.print(Text.assemble(("Kullanıcı: ", "bold cyan"), task))
        case ModelReplied(step=step, text=text, tool_calls=calls):
            console.print()
            console.print(Text(f"[adım {step}] Model:", style="bold magenta"))
            if text:
                # Gerçek modeller cevabı Markdown ile biçimlendirir (kalın, liste, kod).
                console.print(Markdown(text))
            for call in calls:
                console.print(Text.assemble(("  → araç isteği: ", "yellow"), _describe(call)))
        case ToolFinished(result=result):
            style = "red" if result.is_error else "green"
            title = "araç hatası (modele geri döndü)" if result.is_error else "araç sonucu"
            # Text(): dosya içeriğindeki [köşeli parantez] Rich biçim komutu sanılmasın.
            body = Text(_shorten(result.content))
            console.print(Panel(body, title=title, border_style=style))
        case TaskFinished(reason="done", steps=steps):
            console.print()
            console.rule(f"[bold green]Görev bitti · {steps} adım")
        case TaskFinished(reason="step_limit", steps=steps):
            console.print()
            console.rule(f"[bold yellow]Adım sınırı doldu · {steps} adım")
        case TaskFinished(reason=reason, error=error):
            console.print(
                Text(f"{'Durduruldu' if reason == 'cancelled' else 'Hata'}: {error or '—'}")
            )
        case ModelStarted() | ToolStarted() | ModelTextDelta():
            # Ayrıntılı replay tamamlanmış ModelReplied'ı zaten çizer.
            pass


def _describe(call: ToolCall) -> str:
    try:
        args = json.loads(call.arguments)
    except json.JSONDecodeError:
        return f"{call.name}({call.arguments})"
    if not isinstance(args, dict):
        return f"{call.name}({call.arguments})"
    shown = ", ".join(f"{key}={value!r}" for key, value in args.items())  # type: ignore[reportUnknownVariableType]
    return f"{call.name}({shown})"


def _shorten(content: str) -> str:
    """Uzun sonucu ekranda kısaltır. Model tam hâlini görür; bu yalnız görüntü."""
    lines = content.splitlines()
    if len(lines) <= _MAX_SHOWN_LINES:
        return content
    hidden = len(lines) - _MAX_SHOWN_LINES
    return "\n".join([*lines[:_MAX_SHOWN_LINES], f"… (+{hidden} satır)"])
