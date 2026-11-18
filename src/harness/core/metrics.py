"""Kayıttan türetilen ölçümler: okuma verimi ve çalışma biçimi.

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

from harness.core.events import Event, ModelReplied, ToolFinished


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


# Çalışma biçimi (2. tur, 2026-11-18; Fan vd. 2609.20804'teki davranış ölçülerinden): hakemsiz,
# yalnız olaylardan. Uzun keşif (ilk düzenleme geç), uzun doğrulama kuyruğu ve tepe bağlam
# maliyetin nereden geldiğini gösterir. Yalnız dosya araçlarıyla yazma sayılır; bash ile yazma
# görünmez (rapora not edilir).
EDIT_TOOLS = frozenset({"edit_file", "write_file", "delete_file"})


@dataclass(frozen=True)
class WorkStats:
    steps: int  # model çağrısı
    edits: int  # başarılı dosya yazma/düzenleme/silme
    first_edit_step: int | None  # ilk düzenlemenin yapıldığı model çağrısı (yoksa None)
    after_last_edit: int | None  # son düzenlemeden sonraki model çağrısı: doğrulama kuyruğu
    peak_context: int | None  # tek çağrıda sağlayıcının saydığı en büyük girdi

    @property
    def first_edit_share(self) -> float | None:
        return self.first_edit_step / self.steps if self.first_edit_step and self.steps else None


def work_stats(events: Sequence[Event]) -> WorkStats:
    steps = edits = 0
    first = last = None
    peak: int | None = None
    for event in events:
        if isinstance(event, ModelReplied):
            steps += 1
            used = event.usage.input_tokens if event.usage else None
            if used is not None:
                peak = used if peak is None else max(peak, used)
        elif isinstance(event, ToolFinished) and event.call.name in EDIT_TOOLS:
            if event.result.is_error:
                continue
            edits += 1
            first = first or steps
            last = steps
    return WorkStats(
        steps=steps,
        edits=edits,
        first_edit_step=first,
        after_last_edit=steps - last if last is not None else None,
        peak_context=peak,
    )
