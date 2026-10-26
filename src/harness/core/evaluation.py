"""Görev seti değerlendirmesi (rapor Bölüm 20).

Her görev bir klasördür: `task.md` (agent'a verilecek görev metni), `repo/`
(başlangıç durumu) ve `tests/` (gizli testler — agent görmez, bitince repo
kopyasına eklenip pytest ile koşulur). Her koşu geçici bir kopyada yürütülür;
asıl `repo/` klasörü değişmez. Değerlendirme güvenilir bir ortamda yapıldığı
için izin katmanı baştan "hepsine izin ver" durumuna getirilir — kullanıcıya
soru sorulmaz.
"""

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness.core.agent import Agent
from harness.core.anatomy import Anatomy
from harness.core.events import ModelReplied, TaskFinished
from harness.core.permissions import PermissionManager
from harness.llm.base import LLMClient


@dataclass(frozen=True)
class TaskResult:
    """Tek bir görevin sonucu: testler geçti mi, kaç adım, ne kadar token/süre."""

    task_id: str
    passed: bool
    reason: str
    steps: int
    duration_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    detail: str


async def run_task(
    task_dir: Path,
    llm: LLMClient,
    anatomy: Anatomy,
    system_prompt: str,
    max_steps: int = 20,
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

        agent = Agent(llm, tools, system_prompt, permissions=permissions, max_steps=max_steps)

        steps = 0
        reason = "error"
        duration = 0.0
        input_tokens = 0
        output_tokens = 0
        has_usage = False
        async for event in agent.run(task_text):
            if isinstance(event, ModelReplied) and event.usage is not None:
                has_usage = True
                input_tokens += event.usage.input_tokens or 0
                output_tokens += event.usage.output_tokens or 0
            elif isinstance(event, TaskFinished):
                steps = event.steps
                reason = event.reason
                duration = event.duration_seconds or 0.0

        passed, detail = _grade(work, task_dir / "tests")
        return TaskResult(
            task_id=task_id,
            passed=passed,
            reason=reason,
            steps=steps,
            duration_seconds=duration,
            input_tokens=input_tokens if has_usage else None,
            output_tokens=output_tokens if has_usage else None,
            detail=detail,
        )


async def run_all(
    tasks_dir: Path,
    llm: LLMClient,
    anatomy: Anatomy,
    system_prompt: str,
    max_steps: int = 20,
) -> list[TaskResult]:
    """`tasks_dir` altındaki her görev klasörünü sırayla çalıştırır."""
    task_dirs = sorted(p for p in tasks_dir.iterdir() if p.is_dir())
    return [
        await run_task(task_dir, llm, anatomy, system_prompt, max_steps) for task_dir in task_dirs
    ]


def _grade(work: Path, tests_dir: Path) -> tuple[bool, str]:
    if not tests_dir.is_dir():
        return False, f"{tests_dir}: tests/ klasörü yok."
    shutil.copytree(tests_dir, work, dirs_exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=120,
    )
    detail = (result.stdout + result.stderr)[-2000:]
    return result.returncode == 0, detail
