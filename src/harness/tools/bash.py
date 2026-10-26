"""bash: terminal komutu çalıştırır; stdout, stderr ve çıkış kodunu döner (FR-05).

Komutu kendisi çalıştırmaz, environments/ altındaki ortama verir. Zaman aşımı
oradadır. Çıktıyı kırpmaz: dev log bağlamı şişirir ve bunu yönetmek bağlam
yöneticisinin (ince katman) işidir; araç kırparsa o katmanın etkisi ölçülemez.
"""

from pydantic import BaseModel, Field

from harness.environments.base import CommandResult, Environment
from harness.tools.base import Tool, ToolError, ToolOutput, count_lines, short


class BashParams(BaseModel):
    command: str = Field(min_length=1, description="Çalıştırılacak bash komutu")
    timeout_seconds: int = Field(default=60, ge=1, le=600, description="Zaman aşımı (saniye)")


class Bash(Tool[BashParams]):
    name = "bash"
    description = (
        "Çalışma dizininde bash komutu çalıştırır; çıkış kodunu, stdout ve stderr'i döner. "
        "Komuta girdi verilmez; etkileşimli komutlar çalışmaz."
    )
    params_model = BashParams
    category = "exec"

    def __init__(self, environment: Environment) -> None:
        self._environment = environment

    def describe(self, params: BashParams) -> str:
        return f"$ {short(params.command, 70)}"

    async def run(self, params: BashParams) -> ToolOutput:
        try:
            result = await self._environment.run(params.command, params.timeout_seconds)
        except FileNotFoundError as e:
            raise ToolError(str(e)) from e
        if result.timed_out:
            raise ToolError(
                f"Komut {params.timeout_seconds} sn içinde bitmedi, durduruldu.\n" + _format(result)
            )
        out, err = count_lines(result.stdout), count_lines(result.stderr)
        output = f"{out} satır çıktı" + (f", {err} satır hata çıktısı" if err else "")
        return ToolOutput(
            _format(result),
            summary=f"çıkış kodu {result.exit_code} · {output}",
            details={"exit_code": result.exit_code, "stdout_lines": out, "stderr_lines": err},
        )


def _format(result: CommandResult) -> str:
    parts = [f"çıkış kodu: {result.exit_code if result.exit_code is not None else '—'}"]
    if result.stdout:
        parts.append("--- stdout ---\n" + result.stdout.rstrip("\n"))
    if result.stderr:
        parts.append("--- stderr ---\n" + result.stderr.rstrip("\n"))
    if not result.stdout and not result.stderr:
        parts.append("(çıktı yok)")
    return "\n".join(parts)
