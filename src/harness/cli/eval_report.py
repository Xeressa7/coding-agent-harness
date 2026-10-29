"""`harness eval` çıktısı: koşu satırı, varyant özeti ve görev × varyant tablosu.

Hesap `core/evaluation.py`'dedir (summarize); burada yalnız gösterim var.
"""

from collections.abc import Sequence

from rich.table import Table
from rich.text import Text

from harness.core.evaluation import TaskResult, VariantSummary


def tokens(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value / 1000:.1f}k" if value >= 1000 else f"{value:.0f}"


def percent(value: float | None) -> str:
    return "—" if value is None else f"%{value * 100:.0f}"


def result_line(result: TaskResult) -> Text:
    """Koşu biter bitmez yazılan tek satır."""
    mark = Text("✔ geçti", style="h.ok") if result.passed else Text("✘ kaldı", style="h.error")
    used = (result.input_tokens or 0) + (result.output_tokens or 0)
    parts = [
        f"{result.task_id} · {result.variant} · tekrar {result.repeat}",
        f"{result.steps} adım",
        f"{result.duration_seconds:.0f} sn",
        f"{tokens(used) if result.input_tokens is not None else '—'} token",
    ]
    if not result.completed:
        parts.append(f"durma: {result.reason}")
    return Text.assemble(mark, " ", Text(" · ".join(parts), style="h.muted"))


def summary_table(summaries: Sequence[VariantSummary]) -> Table:
    table = Table(title="Varyant özeti", title_justify="left")
    table.add_column("Varyant")
    table.add_column("Geçti", justify="right")
    table.add_column("%95 aralık", justify="right")
    table.add_column("Bitti", justify="right")
    table.add_column("Adım", justify="right")
    table.add_column("Süre", justify="right")
    table.add_column("Girdi", justify="right")
    table.add_column("Çıktı", justify="right")
    table.add_column("Önbellek", justify="right")
    table.add_column("Araç hatası", justify="right")
    table.add_column("Müdahale", justify="right")
    for s in summaries:
        table.add_row(
            s.variant,
            f"{s.passed}/{s.runs} ({percent(s.pass_rate)})",
            f"{percent(s.pass_low)}–{percent(s.pass_high)}",
            f"{s.completed}/{s.runs}",
            f"{s.mean_steps:.1f}",
            f"{s.mean_seconds:.0f} sn",
            tokens(s.mean_input_tokens),
            tokens(s.mean_output_tokens),
            percent(s.cache_ratio),
            f"{s.tool_errors}/{s.tool_calls}",
            str(s.interventions),
        )
    table.caption = (
        "Adım, süre ve token koşu başına ortalamadır. Önbellek: girdinin önbellekten okunan payı. "
        "Müdahale: harness katmanlarının araya girişi. "
        "Aralıklar örtüşüyorsa fark kanıtlanmış sayılmaz."
    )
    return table


def task_table(results: Sequence[TaskResult]) -> Table:
    """Hangi görev hangi varyantta kaç tekrarda geçti."""
    variants = list(dict.fromkeys(r.variant for r in results))
    tasks = list(dict.fromkeys(r.task_id for r in results))
    table = Table(title="Görev × varyant (geçen / koşu)", title_justify="left")
    table.add_column("Görev")
    for variant in variants:
        table.add_column(variant, justify="center")
    for task in tasks:
        row: list[str | Text] = [task]
        for variant in variants:
            runs = [r for r in results if r.task_id == task and r.variant == variant]
            passed = sum(r.passed for r in runs)
            style = (
                "h.ok" if runs and passed == len(runs) else "h.error" if not passed else "h.warn"
            )
            row.append(Text(f"{passed}/{len(runs)}", style=style) if runs else Text("—"))
        table.add_row(*row)
    return table
