"""Çalışma özeti: tam ekranın sağ panelindeki "Özet" görünümü.

Akış günlüğü her olayı sırayla yazar (eğitim için); özet ise "şu an ne durumda?"
sorusunu cevaplar: hangi turdayız, ne çalışıyor, plan nerede, hangi dosyalara
dokunuldu, ne kadar token gitti. Olayları okur, kendisi hiçbir şey yürütmez.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from rich.text import Text

from harness.cli.render import plan_lines
from harness.core.events import (
    Event,
    ModelReplied,
    ModelStarted,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)

# Dosyaya ne yapıldı: harf ve sıralama önceliği (değiştirilenler üstte).
FILE_MARKS = {"read_file": "O", "edit_file": "D", "write_file": "Y", "delete_file": "S"}
_CHANGED = {"D", "Y", "S"}
MAX_FILES = 12
LEGEND = {"O": "oku", "D": "düzen", "Y": "yaz", "S": "sil"}
FINISH_LABELS = {
    "done": "bitti",
    "step_limit": "adım sınırı",
    "cancelled": "durduruldu",
    "error": "hata",
}


def elapsed(value: float) -> str:
    """Canlı sayaç için kısa süre: 4.2 sn, 37 sn, 2 dk 05 sn."""
    if value < 10:
        return f"{value:.1f} sn"
    if value < 60:
        return f"{value:.0f} sn"
    minutes, rest = divmod(int(value), 60)
    return f"{minutes} dk {rest:02d} sn"


def _compact(count: int | None) -> str:
    if count is None:
        return "—"
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def _path_of(arguments: str) -> str | None:
    try:
        data: object = json.loads(arguments)
    except ValueError:
        return None
    if isinstance(data, dict):
        path = data.get("path")  # type: ignore[union-attr]
        return path if isinstance(path, str) and path else None
    return None


def fit_path(path: str, width: int) -> str:
    """Dar panele sığdırır: baştan kısaltılır, dosya adı hep görünür."""
    if len(path) <= width:
        return path
    return "…" + path[-(width - 1) :] if width > 1 else path[-1:]


@dataclass
class WorkSummary:
    turn: int = 0
    step: int = 0
    activity: str = "Hazır"
    started: float | None = None  # sürmekte olan turun başlangıcı (monotonic)
    last_duration: float | None = None
    last_finish: str = ""
    plan: list[str] = field(default_factory=lambda: [])
    files: dict[str, str] = field(default_factory=lambda: {})  # yol -> harfler ("OD")
    model_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage_seen: bool = False
    last_context: int | None = None  # son model çağrısının girdi token'ı ≈ bağlam boyutu

    def reset(self) -> None:
        vars(self).update(vars(WorkSummary()))

    def update(self, event: Event) -> None:
        match event:
            case TaskStarted():
                self.turn += 1
                self.step = 0
                self.started = time.monotonic()
                self.activity = "Başladı"
            case ModelStarted(step=step):
                self.step = step
                self.activity = "Model düşünüyor"
            case ModelReplied(usage=usage):
                self.model_calls += 1
                if usage is not None:
                    self.usage_seen = True
                    self.input_tokens += usage.input_tokens or 0
                    self.output_tokens += usage.output_tokens or 0
                    if usage.input_tokens is not None:
                        self.last_context = usage.input_tokens
            case ToolStarted(call=call):
                self.activity = f"{call.name} çalışıyor"
            case ToolFinished(call=call, result=result, details=details):
                self.tool_calls += 1
                self.activity = "Model bekleniyor"
                if result.is_error:
                    self.tool_errors += 1
                    return
                if call.name == "update_plan":
                    self.plan = plan_lines(details)
                mark = FILE_MARKS.get(call.name)
                path = _path_of(call.arguments) if mark else None
                if mark and path:
                    seen = self.files.get(path, "")
                    self.files[path] = seen if mark in seen else seen + mark
            case TaskFinished(reason=reason):
                if self.started is not None:
                    self.last_duration = time.monotonic() - self.started
                self.started = None
                self.last_finish = FINISH_LABELS.get(reason, reason)
                self.activity = "Hazır"
            case _:
                pass

    def render(self, width: int = 30) -> Text:
        text = Text()
        if self.turn == 0:
            text.append("Henüz görev yok.\n", style="dim")
            text.append("Ctrl+B: panel görünümü", style="dim")
            return text
        if self.started is not None:
            took = elapsed(time.monotonic() - self.started)
            text.append(f"TUR {self.turn} · adım {self.step} · {took}\n", style="bold")
            text.append(f"● {self.activity}\n")
        else:
            took = f" · {elapsed(self.last_duration)}" if self.last_duration is not None else ""
            text.append(f"TUR {self.turn} · {self.last_finish}{took}\n", style="bold")
        if self.plan:
            text.append("\nPLAN\n", style="bold")
            for line in self.plan:
                text.append(fit_path(line, width) + "\n")
        if self.files:
            text.append("\nDOSYALAR\n", style="bold")
            ordered = sorted(self.files.items(), key=lambda item: not (_CHANGED & set(item[1])))
            for path, marks in ordered[:MAX_FILES]:
                style = "" if _CHANGED & set(marks) else "dim"
                text.append(f"{marks:<2} {fit_path(path, width - 3)}\n", style=style)
            if len(ordered) > MAX_FILES:
                text.append(f"… +{len(ordered) - MAX_FILES} dosya\n", style="dim")
            present = set("".join(self.files.values()))
            legend = [f"{m} {w}" for m, w in LEGEND.items() if m in present]
            text.append(" · ".join(legend) + "\n", style="dim")
        text.append("\nOTURUM\n", style="bold")
        rows: list[tuple[str, Any]] = [
            ("model", self.model_calls),
            (
                "araç",
                f"{self.tool_calls}" + (f" · {self.tool_errors} hata" if self.tool_errors else ""),
            ),
            (
                "token",
                f"↑{_compact(self.input_tokens)} ↓{_compact(self.output_tokens)}"
                if self.usage_seen
                else "—",
            ),
            ("bağlam", _compact(self.last_context)),
        ]
        for label, value in rows:
            text.append(f"{label:<7}", style="dim")
            text.append(f"{value}\n")
        text.rstrip()
        return text
