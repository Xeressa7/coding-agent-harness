"""job_output ve job_stop: bash'in `background: true` ile başlattığı işleri izler ve durdurur.

Okuma yalnız son okumadan bu yana gelen çıktıyı verir; model aynı log'u tekrar tekrar
bağlama taşımaz. `wait_seconds` yeni çıktı ya da iş bitişi gelene kadar bekler: "sunucu
hazır mı?" için döngüde art arda çağırmak yerine tek çağrı yeter.
"""

import asyncio

from pydantic import BaseModel, Field

from harness.environments.jobs import JobTable
from harness.tools.base import Tool, ToolError, ToolOutput, short
from harness.tools.bash import job_text


class JobOutputParams(BaseModel):
    job_id: str = Field(default="", description="İş kimliği (ör. j1); boş: bütün işlerin listesi")
    wait_seconds: int = Field(
        default=0, ge=0, le=60, description="Yeni çıktı ya da bitiş için en fazla bekleme (sn)"
    )


class JobOutput(Tool[JobOutputParams]):
    name = "job_output"
    description = (
        "Arka plan işinin son okumadan bu yana yeni çıktısını ve durumunu döner; "
        "job_id boşsa işleri listeler."
    )
    params_model = JobOutputParams
    category = "read"

    def __init__(self, jobs: JobTable) -> None:
        self._jobs = jobs

    def describe(self, params: JobOutputParams) -> str:
        return params.job_id or "işler"

    async def run(self, params: JobOutputParams) -> ToolOutput:
        if not params.job_id:
            jobs = self._jobs.all()
            if not jobs:
                return ToolOutput("Arka plan işi yok.", summary="iş yok", details={"jobs": 0})
            lines = [
                f"{job.job_id} · "
                + ("çalışıyor" if job.exit_code is None else f"bitti ({job.exit_code})")
                + f" · {short(job.command, 80)}"
                for job in jobs
            ]
            return ToolOutput(
                "\n".join(lines), summary=f"{len(jobs)} iş", details={"jobs": len(jobs)}
            )
        job = self._jobs.get(params.job_id)
        if job is None:
            raise ToolError(f"{params.job_id}: böyle bir iş yok; liste için job_id'yi boş bırak.")
        output = await asyncio.to_thread(job.read, params.wait_seconds)
        state = "çalışıyor" if output.running else f"bitti, çıkış kodu {output.exit_code}"
        return ToolOutput(
            f"{job.job_id} · {state}\n{job_text(output)}",
            summary=f"{job.job_id} · {state} · {len(output.text):,} karakter".replace(",", "."),
            details={"job_id": job.job_id, "running": output.running, "chars": len(output.text)},
        )


class JobStopParams(BaseModel):
    job_id: str = Field(description="Durdurulacak işin kimliği (ör. j1)")


class JobStop(Tool[JobStopParams]):
    name = "job_stop"
    description = (
        "Arka plan işini başlattığı bütün süreçlerle birlikte durdurur; son çıktıyı döner."
    )
    params_model = JobStopParams
    category = "exec"

    def __init__(self, jobs: JobTable) -> None:
        self._jobs = jobs

    def describe(self, params: JobStopParams) -> str:
        return params.job_id

    async def run(self, params: JobStopParams) -> ToolOutput:
        job = self._jobs.get(params.job_id)
        if job is None:
            raise ToolError(f"{params.job_id}: böyle bir iş yok.")
        if job.exit_code is not None:
            output = await asyncio.to_thread(job.read)
            head = f"{job.job_id} zaten bitmişti (çıkış kodu {output.exit_code})."
        else:
            output = await asyncio.to_thread(job.stop)
            head = f"{job.job_id} durduruldu."
        return ToolOutput(
            f"{head}\n{job_text(output, label='son çıktı')}",
            summary=f"{job.job_id} · durdu",
            details={"job_id": job.job_id, "exit_code": output.exit_code},
        )
