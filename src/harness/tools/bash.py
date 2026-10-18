"""bash: terminal komutu çalıştırır; stdout, stderr ve çıkış kodunu döner (FR-05).

Komutu kendisi çalıştırmaz, environments/ altındaki ortama verir. Zaman aşımı
oradadır. Çıktıyı kırpmaz: dev log bağlamı şişirir ve bunu yönetmek bağlam
yöneticisinin (ince katman) işidir; araç kırparsa o katmanın etkisi ölçülemez.
"""

from pydantic import BaseModel, Field

from harness.environments.base import CommandResult, Environment
from harness.tools.base import Tool, ToolError


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

    def __init__(self, environment: Environment) -> None:
        self._environment = environment

    async def run(self, params: BashParams) -> str:
        try:
            result = await self._environment.run(params.command, params.timeout_seconds)
        except FileNotFoundError as e:
            raise ToolError(str(e)) from e
        if result.timed_out:
            raise ToolError(
                f"Komut {params.timeout_seconds} sn içinde bitmedi, durduruldu.\n" + _format(result)
            )
        return _format(result)


def _format(result: CommandResult) -> str:
    parts = [f"çıkış kodu: {result.exit_code if result.exit_code is not None else '—'}"]
    if result.stdout:
        parts.append("--- stdout ---\n" + result.stdout.rstrip("\n"))
    if result.stderr:
        parts.append("--- stderr ---\n" + result.stderr.rstrip("\n"))
    if not result.stdout and not result.stderr:
        parts.append("(çıktı yok)")
    return "\n".join(parts)
