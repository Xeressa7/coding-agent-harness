"""bash: terminal komutu çalıştırır; stdout, stderr ve çıkış kodunu döner (FR-05).

Komutu kendisi çalıştırmaz, environments/ altındaki ortama verir. Zaman aşımı
oradadır. Çıktıyı kırpmaz: dev log bağlamı şişirir ve bunu yönetmek bağlam
yöneticisinin (ince katman) işidir; araç kırparsa o katmanın etkisi ölçülemez.

Kalıcı çalışma klasörü (`persistent_cwd` katmanı): her komut ayrı bir bash'te koşar;
`cd` bir sonraki komuta taşınsın diye araç komutun bittiği klasörü okur ve sonrakini
orada başlatır. Klasör, komutun sonuna eklenen bir çıkış kancasıyla tek satır olarak
stdout'a yazılır ve modele gitmeden çıktıdan silinir; ortamdan bağımsızdır (yarın
Docker'da da çalışır). Kök dışına çıkan klasör taşınmaz: sonraki komut eski yerinde
başlar ve model bunu sonuçtan okur. Dosya araçlarının yolları her zaman köke göredir.
"""

import asyncio
import secrets
import time
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from harness.environments.base import BackgroundEnvironment, CommandResult, Environment
from harness.environments.jobs import JobOutput
from harness.tools.base import Tool, ToolError, ToolOutput, count_lines, short


class BashParams(BaseModel):
    command: str = Field(min_length=1, description="Çalıştırılacak bash komutu")
    timeout_seconds: int = Field(default=60, ge=1, le=600, description="Zaman aşımı (saniye)")
    background: bool = Field(
        default=False,
        description="Bitmeyen komut (dev sunucusu, izleyici) için true: arka planda başlar, "
        "ilk saniyelerin çıktısı ve iş kimliği döner; job_output ile oku, job_stop ile durdur",
    )


BACKGROUND_FIRST_SECONDS = 3.0  # arka plan işinin ilk çıktısı için beklenen süre
TIMEOUT_HINT = (
    "\n[harness] Uzun süren test takımı için: önce değiştirdiğin kodun testlerini ayrı çalıştır; "
    "tam takımı da çalıştır ama background: true ile başlatıp job_output ile izle ya da "
    "timeout_seconds'ı artır (en çok 600). Aynı komutu aynı sınırla yineleme."
)


class Bash(Tool[BashParams]):
    name = "bash"
    description = (
        "Çalışma dizininde bash komutu çalıştırır; çıkış kodunu, stdout ve stderr'i döner. "
        "Komuta girdi verilmez; etkileşimli komutlar çalışmaz."
    )
    params_model = BashParams
    category = "exec"

    def __init__(
        self,
        environment: Environment,
        *,
        root: Path | None = None,
        guidance: bool = False,
        timeout_hint: bool = False,
    ) -> None:
        """root verilirse `cd` komutlar arasında korunur (kalıcı çalışma klasörü).

        guidance (`tool_guidance` katmanı): açıklama okuma ve arama için dosya araçlarını
        önerir; pilotta model dosyaları bash'te cat/sed ile parça parça okuyordu.

        timeout_hint (`timeout_hint` katmanı): zaman aşımı sonucu daha dar bir test ya da arka
        plan işi önerir; pilotta tam takım 600 sn'de kesildi ve model aynı komutu yineledi."""
        self._timeout_hint = timeout_hint
        self._environment = environment
        self._root = root.resolve() if root is not None else None
        self._cwd = PurePosixPath()  # köke göre; "." kökün kendisi
        self._marker = f"__harness_cwd_{secrets.token_hex(4)}__"
        if self._root is not None:
            self.description = (
                "Bash komutu çalıştırır; çıkış kodunu, stdout ve stderr'i döner. `cd` sonraki "
                "komutlara taşınır (kök dışına çıkmaz); dosya araçlarının yolları yine köke "
                "göredir. Komuta girdi verilmez; etkileşimli komutlar çalışmaz."
            )
        if guidance:
            self.description += (
                " Dosya okumak için cat/head/tail/sed yerine read_file, aramak için grep/find "
                "yerine search kullan; bash derleme, test, git ve kurulu araçları çalıştırmak "
                "içindir."
            )

    @property
    def cwd(self) -> str:
        """Sonraki komutun başlayacağı klasör, köke göre ("." kök)."""
        return str(self._cwd)

    def describe(self, params: BashParams) -> str:
        where = f"{self.cwd}/ " if self._root is not None and self.cwd != "." else ""
        return f"{where}{'& ' if params.background else '$ '}{short(params.command, 70)}"

    async def run(self, params: BashParams) -> ToolOutput:
        if params.background:
            return await self._start(params.command)
        command = self._wrap(params.command) if self._root is not None else params.command
        try:
            result = await self._environment.run(command, params.timeout_seconds)
        except FileNotFoundError as e:
            raise ToolError(str(e)) from e
        note = None
        if self._root is not None:
            result, note = self._follow(result)
        if result.timed_out:
            hint = TIMEOUT_HINT if self._timeout_hint else ""
            raise ToolError(
                f"Komut {params.timeout_seconds} sn içinde bitmedi, durduruldu.\n"
                + _format(result)
                + hint
            )
        out, err = count_lines(result.stdout), count_lines(result.stderr)
        output = f"{out} satır çıktı" + (f", {err} satır hata çıktısı" if err else "")
        details: dict[str, object] = {
            "exit_code": result.exit_code,
            "stdout_lines": out,
            "stderr_lines": err,
        }
        text = _format(result)
        if self._root is not None:
            details["cwd"] = self.cwd
            if self.cwd != ".":
                text += f"\n(çalışma klasörü: {self.cwd}/)"
        if result.left_behind:
            details["left_behind"] = True
            text += (
                "\n[harness] Komut bitti ama `&` ile bıraktığı süreç çıktıyı tutuyordu; o süreç "
                "durduruldu. Bitmeyen komutlar için background: true kullan."
            )
        if note:
            text += f"\n{note}"
        return ToolOutput(
            text, summary=f"çıkış kodu {result.exit_code} · {output}", details=details
        )

    async def _start(self, command: str) -> ToolOutput:
        environment = self._environment
        if not isinstance(environment, BackgroundEnvironment):
            raise ToolError("Bu ortam arka plan işi çalıştıramıyor.")
        if self._root is not None and self.cwd != ".":
            start = self.cwd.replace("'", "'\\''")
            command = f"cd -- '{start}' && {command}"
        try:
            job = environment.start(command)
        except (RuntimeError, FileNotFoundError) as e:
            raise ToolError(str(e)) from e
        deadline = time.monotonic() + BACKGROUND_FIRST_SECONDS
        while job.exit_code is None and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        first = await asyncio.to_thread(job.read)
        if first.running:
            head = f"Arka plan işi {job.job_id} başladı, çalışıyor."
            tail = f"Yeni çıktı için job_output ({job.job_id}), durdurmak için job_stop."
        else:
            head = f"Arka plan işi {job.job_id} {BACKGROUND_FIRST_SECONDS:.0f} sn içinde bitti, "
            head += f"çıkış kodu {first.exit_code}."
            tail = ""
        body = job_text(first, label=f"ilk {BACKGROUND_FIRST_SECONDS:.0f} sn çıktısı")
        return ToolOutput(
            "\n".join(part for part in (head, body, tail) if part),
            summary=f"{job.job_id} · " + ("çalışıyor" if first.running else "bitti"),
            details={"job_id": job.job_id, "running": first.running, "exit_code": first.exit_code},
        )

    def _wrap(self, command: str) -> str:
        """Komutu kayıtlı klasörde başlatır; bittiği klasörü işaretli bir satırla yazdırır."""
        start = str(self._cwd).replace("'", "'\\''")
        return (
            f"__harness_where() {{ printf '\\n{self._marker}%s\\n' "
            '"$(pwd -W 2>/dev/null || pwd)"; }\n'
            "trap __harness_where EXIT\n"
            f"cd -- '{start}' 2>/dev/null || "
            f"printf '[harness] {start}/ klasörü yok; komut kökte çalışıyor.\\n' >&2\n"
            f"{command}\n"
        )

    def _follow(self, result: CommandResult) -> tuple[CommandResult, str | None]:
        """Çıktıdaki klasör satırını siler, kök içindeyse sonraki komut için saklar."""
        assert self._root is not None
        lines = result.stdout.split("\n")
        found = [i for i, line in enumerate(lines) if line.startswith(self._marker)]
        if not found:
            return result, None  # zaman aşımı, `exec` ya da kancayı ezen komut: klasör aynı
        where = lines[found[-1]][len(self._marker) :].strip()
        cut = found[-1] - 1 if found[-1] > 0 and lines[found[-1] - 1] == "" else found[-1]
        stdout = "\n".join(lines[:cut] + lines[found[-1] + 1 :])
        result = result.model_copy(update={"stdout": stdout})
        try:
            inside = Path(where).resolve().relative_to(self._root)
        except ValueError:
            return result, (
                f"[harness] Komut kök dışında bitti ({where}); çalışma klasörü kök dışına "
                f"taşınmaz, sonraki komut {self.cwd}/ klasöründe başlar."
            )
        self._cwd = PurePosixPath(inside.as_posix())
        return result, None


def _format(result: CommandResult) -> str:
    parts = [f"çıkış kodu: {result.exit_code if result.exit_code is not None else '—'}"]
    if result.stdout:
        parts.append("--- stdout ---\n" + result.stdout.rstrip("\n"))
    if result.stderr:
        parts.append("--- stderr ---\n" + result.stderr.rstrip("\n"))
    if not result.stdout and not result.stderr:
        parts.append("(çıktı yok)")
    return "\n".join(parts)


def job_text(output: JobOutput, *, label: str = "yeni çıktı") -> str:
    """Arka plan işinin okunan çıktısı; okunmadan düşen kısım varsa söylenir."""
    lost = f"[harness: okunmamış {output.dropped:,} karakter atlandı]\n".replace(",", ".")
    body = output.text.rstrip("\n") if output.text else "(çıktı yok)"
    return f"--- {label} ---\n" + (lost if output.dropped else "") + body
