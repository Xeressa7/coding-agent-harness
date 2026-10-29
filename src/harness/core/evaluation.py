"""Görev seti değerlendirmesi (rapor Bölüm 20).

Her görev bir klasördür: `task.md` (agent'a verilecek görev metni), `repo/`
(başlangıç durumu) ve `tests/` (gizli testler — agent görmez, bitince repo
kopyasına eklenip pytest ile koşulur). Her koşu geçici bir kopyada yürütülür;
asıl `repo/` klasörü değişmez. Değerlendirme güvenilir bir ortamda yapıldığı
için izin katmanı baştan "hepsine izin ver" durumuna getirilir — kullanıcıya
soru sorulmaz.

Deney matrisi (`run_matrix`): görevler × varyantlar (model + anatomi) × tekrar.
Modeller her koşuda farklı davranabildiği için tek koşu bir şey kanıtlamaz;
tekrarlar sırayla iç içe koşulur (önce her şeyin 1. tekrarı), böylece yarıda
kesilen bir deney de dengeli bir karşılaştırma bırakır. Her koşunun olayları
ayrı bir oturum kaydına yazılır (replay, inspect ve canlı izleme için).
"""

import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from harness.core.agent import Agent
from harness.core.anatomy import Anatomy
from harness.core.events import (
    Event,
    HarnessIntervened,
    ModelReplied,
    TaskFinished,
    ToolFinished,
)
from harness.core.metrics import read_stats
from harness.core.permissions import PermissionManager
from harness.core.session_log import SessionLogger
from harness.llm.base import LLMClient


@dataclass(frozen=True)
class TaskResult:
    """Tek bir görevin sonucu: testler geçti mi, kaç adım, ne kadar token/süre.

    `passed` gizli testlerin sonucudur; `reason == "done"` ise modelin kendi
    "bitti" demesidir. İkisi ayrı tutulur: adım sınırına takılan koşu da testleri
    geçmiş olabilir, "bitti" diyen koşu da testlerden kalabilir.
    """

    task_id: str
    passed: bool
    reason: str
    steps: int
    duration_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    detail: str
    cached_tokens: int | None = None
    tool_calls: int = 0
    tool_errors: int = 0
    interventions: int = 0  # harness katmanlarının araya girişi (HarnessIntervened)
    lines_read: int = 0  # read_file'ın gösterdiği satırlar (core/metrics.py)
    reread_lines: int = 0  # bunlardan daha önce okunmuş olanlar
    variant: str = ""
    repeat: int = 1
    log: str | None = None  # bu koşunun oturum kaydı (.jsonl)

    @property
    def completed(self) -> bool:
        return self.reason == "done"


@dataclass(frozen=True)
class Variant:
    """Deneyin bir kolu: hangi model, hangi anatomi. `make_llm` her koşuya yeni istemci verir."""

    label: str
    anatomy: Anatomy
    make_llm: Callable[[], LLMClient]
    system_prompt: str
    provider: str | None = None  # kayda (TaskStarted) girer
    model: str | None = None
    effort: str | None = None


async def run_task(
    task_dir: Path,
    llm: LLMClient,
    anatomy: Anatomy,
    system_prompt: str,
    max_steps: int = 20,
    *,
    log: SessionLogger | None = None,
    provider: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> TaskResult:
    """Bir görevi geçici bir kopyada çalıştırır, bitince gizli testlerle değerlendirir."""
    task_id = task_dir.name
    task_text = (task_dir / "task.md").read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix=f"harness-eval-{task_id}-") as raw_work:
        work = Path(raw_work)
        shutil.copytree(task_dir / "repo", work, dirs_exist_ok=True)

        tools = anatomy.tool_registry(work)
        permissions = PermissionManager(work, tools)
        for spec in tools.specs():
            permissions.grant(spec.name)

        agent = Agent(
            llm,
            tools,
            system_prompt,
            permissions=permissions,
            max_steps=max_steps,
            anatomy=anatomy,
            root=work,
            provider=provider,
            model_label=model,
            effort=effort,
        )

        steps = 0
        reason = "error"
        duration = 0.0
        input_tokens = output_tokens = cached_tokens = 0
        has_usage = has_cache = False
        tool_calls = tool_errors = interventions = 0
        error = ""
        seen: list[Event] = []
        try:
            async for event in agent.run(task_text):
                seen.append(event)
                if log is not None:
                    log.write(event)
                if isinstance(event, ModelReplied) and event.usage is not None:
                    has_usage = True
                    input_tokens += event.usage.input_tokens or 0
                    output_tokens += event.usage.output_tokens or 0
                    if event.usage.cached_tokens is not None:
                        has_cache = True
                        cached_tokens += event.usage.cached_tokens
                elif isinstance(event, ToolFinished):
                    tool_calls += 1
                    tool_errors += event.result.is_error
                elif isinstance(event, HarnessIntervened):
                    interventions += 1
                elif isinstance(event, TaskFinished):
                    steps = event.steps
                    reason = event.reason
                    duration = event.duration_seconds or 0.0
                    error = event.error or ""
        except Exception:
            pass  # TaskFinished(reason="error") zaten geldi; değerlendirme yine yapılır

        passed, detail = _grade(work, task_dir / "tests")
        reading = read_stats(seen)
        return TaskResult(
            task_id=task_id,
            passed=passed,
            reason=reason,
            steps=steps,
            duration_seconds=duration,
            input_tokens=input_tokens if has_usage else None,
            output_tokens=output_tokens if has_usage else None,
            detail=f"{error}\n{detail}".strip() if error else detail,
            cached_tokens=cached_tokens if has_cache else None,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            interventions=interventions,
            lines_read=reading.lines_read,
            reread_lines=reading.reread_lines,
            log=str(log.path) if log is not None else None,
        )


async def run_all(
    tasks_dir: Path,
    llm: LLMClient,
    anatomy: Anatomy,
    system_prompt: str,
    max_steps: int = 20,
) -> list[TaskResult]:
    """`tasks_dir` altındaki her görev klasörünü sırayla çalıştırır."""
    return [
        await run_task(task_dir, llm, anatomy, system_prompt, max_steps)
        for task_dir in find_tasks(tasks_dir)
    ]


def find_tasks(tasks_dir: Path, only: Sequence[str] = ()) -> list[Path]:
    """task.md içeren görev klasörleri; `only` verilirse adı bunlardan biriyle başlayanlar."""
    found = sorted(p for p in tasks_dir.iterdir() if p.is_dir() and (p / "task.md").is_file())
    if only:
        found = [p for p in found if any(p.name.startswith(prefix) for prefix in only)]
    return found


async def run_matrix(
    tasks: Sequence[Path],
    variants: Sequence[Variant],
    out_dir: Path,
    *,
    repeats: int = 1,
    max_steps: int = 20,
    on_result: Callable[[TaskResult], None] | None = None,
) -> list[TaskResult]:
    """Her görevi her varyantla `repeats` kez koşar; sonuçları `results.jsonl`'ye ekler."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results_file = out_dir / "results.jsonl"
    results: list[TaskResult] = []
    for repeat in range(1, repeats + 1):
        for task_dir in tasks:
            for variant in variants:
                log = SessionLogger(
                    out_dir / "runs" / slug(variant.label) / f"{task_dir.name}-r{repeat}.jsonl"
                )
                result = await run_task(
                    task_dir,
                    variant.make_llm(),
                    variant.anatomy,
                    variant.system_prompt,
                    max_steps,
                    log=log,
                    provider=variant.provider,
                    model=variant.model,
                    effort=variant.effort,
                )
                result = replace(result, variant=variant.label, repeat=repeat)
                results.append(result)
                with results_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
                if on_result is not None:
                    on_result(result)
    return results


def read_results(path: Path) -> list[TaskResult]:
    """`results.jsonl` ya da onu içeren deney klasörü."""
    if path.is_dir():
        path = path / "results.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [TaskResult(**json.loads(line)) for line in lines if line.strip()]


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "varyant"


# ---------------------------------------------------------------- özet


@dataclass(frozen=True)
class VariantSummary:
    """Bir varyantın bütün koşularının özeti; token ortalamaları yalnız sayım bildirenlerden."""

    variant: str
    runs: int
    passed: int
    completed: int
    pass_low: float  # geçme oranı için %95 Wilson aralığı
    pass_high: float
    mean_steps: float
    mean_seconds: float
    mean_input_tokens: float | None
    mean_output_tokens: float | None
    cache_ratio: float | None  # önbellekten okunan girdi / toplam girdi
    tool_calls: int
    tool_errors: int
    interventions: int
    reread_ratio: float | None  # okunan satırların tekrar okunan payı

    @property
    def pass_rate(self) -> float:
        return self.passed / self.runs if self.runs else 0.0


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Az sayıda denemede de anlamlı kalan geçme oranı aralığı (Wilson skoru)."""
    if trials == 0:
        return 0.0, 1.0
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def summarize(results: Sequence[TaskResult]) -> list[VariantSummary]:
    """Varyant başına özet; varyantlar ilk görüldükleri sırada."""
    groups: dict[str, list[TaskResult]] = {}
    for result in results:
        groups.setdefault(result.variant, []).append(result)
    summaries: list[VariantSummary] = []
    for variant, runs in groups.items():
        passed = sum(r.passed for r in runs)
        low, high = wilson_interval(passed, len(runs))
        counted = [r for r in runs if r.input_tokens is not None]
        cached = [r for r in counted if r.cached_tokens is not None]
        cached_input = sum(r.input_tokens or 0 for r in cached)
        summaries.append(
            VariantSummary(
                variant=variant,
                runs=len(runs),
                passed=passed,
                completed=sum(r.completed for r in runs),
                pass_low=low,
                pass_high=high,
                mean_steps=sum(r.steps for r in runs) / len(runs),
                mean_seconds=sum(r.duration_seconds for r in runs) / len(runs),
                mean_input_tokens=_mean([r.input_tokens or 0 for r in counted]),
                mean_output_tokens=_mean([r.output_tokens or 0 for r in counted]),
                cache_ratio=(
                    sum(r.cached_tokens or 0 for r in cached) / cached_input
                    if cached_input
                    else None
                ),
                tool_calls=sum(r.tool_calls for r in runs),
                tool_errors=sum(r.tool_errors for r in runs),
                interventions=sum(r.interventions for r in runs),
                reread_ratio=(
                    sum(r.reread_lines for r in runs) / read
                    if (read := sum(r.lines_read for r in runs))
                    else None
                ),
            )
        )
    return summaries


def _mean(values: Sequence[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _grade(work: Path, tests_dir: Path) -> tuple[bool, str]:
    if not tests_dir.is_dir():
        return False, f"{tests_dir}: tests/ klasörü yok."
    shutil.copytree(tests_dir, work, dirs_exist_ok=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "Gizli testler 120 saniyede bitmedi."
    detail = (result.stdout + result.stderr)[-2000:]
    return result.returncode == 0, detail
