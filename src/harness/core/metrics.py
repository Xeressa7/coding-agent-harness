"""Kayıttan türetilen ölçümler: okuma verimi.

Tekrar okuma: model aynı dosyanın aynı satırlarını yeniden okuduğunda bağlama
yeni bilgi girmez ama token harcanır (2026-10-29 gerçek koşu: bir dosya 11 kez,
çakışan aralıklarla okundu). Ölçüm: okunan toplam satır ile bunların kaçının
daha önce okunmuş olduğu. Saf fonksiyondur; canlı kayıt, deney ve eski kayıt
aynı tanımla ölçülür.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from harness.core.events import Event, ToolFinished


@dataclass(frozen=True)
class ReadStats:
    lines_read: int  # read_file'ın gösterdiği satırların toplamı
    reread_lines: int  # bunlardan aynı dosyada daha önce okunmuş olanlar
    files: int

    @property
    def reread_ratio(self) -> float | None:
        return self.reread_lines / self.lines_read if self.lines_read else None


def read_stats(events: Sequence[Event]) -> ReadStats:
    seen: dict[str, set[int]] = {}
    total = again = 0
    for event in events:
        if not isinstance(event, ToolFinished) or event.call.name != "read_file":
            continue
        if event.result.is_error:
            continue
        span = _span(event)
        if span is None:
            continue
        path, start, end = span
        lines = seen.setdefault(path, set())
        shown = range(start, end + 1)
        total += len(shown)
        again += sum(1 for number in shown if number in lines)
        lines.update(shown)
    return ReadStats(lines_read=total, reread_lines=again, files=len(seen))


def _span(event: ToolFinished) -> tuple[str, int, int] | None:
    """(dosya, ilk satır, son satır); eski kayıtta aralık argümanlardan ve satır sayısından."""
    details = event.details
    try:
        arguments: dict[str, Any] = json.loads(event.call.arguments) or {}
    except ValueError:
        arguments = {}
    path = details.get("path") or arguments.get("path")
    if not path:
        return None
    start = int(details.get("start") or arguments.get("start_line") or 1)
    if details.get("end"):
        end = int(details["end"])
    elif details.get("lines") is not None:
        end = start + int(details["lines"]) - 1
    else:
        return None
    key = str(path).replace("\\", "/").removeprefix("./").casefold()
    return (key, start, end) if end >= start else None
