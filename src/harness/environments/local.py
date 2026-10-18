"""Yerel ortam: komutu bu makinede, çalışma dizininde bash ile çalıştırır.

Windows'ta bash, Git for Windows'un getirdiği bash'tir; System32'deki bash.exe
WSL'e gider ve proje dosyalarını başka bir dosya sisteminden görür, kullanılmaz.
Zaman aşımında ya da görev iptal edildiğinde komut, başlattığı alt süreçlerle
birlikte öldürülür; arkada sahipsiz süreç kalmaz.
"""

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from harness.environments.base import CommandResult


def find_bash() -> Path | None:
    """Kullanılacak bash; bulunamazsa None."""
    if sys.platform != "win32":
        found = shutil.which("bash")
        return Path(found) if found else None
    git = shutil.which("git")
    candidates = [Path(git).resolve().parent.parent / "bin" / "bash.exe"] if git else []
    candidates.append(
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe"
    )
    return next((path for path in candidates if path.is_file()), None)


class LocalEnvironment:
    def __init__(self, root: Path, bash: Path | None = None) -> None:
        self._root = root.resolve()
        self._bash = bash or find_bash()

    async def run(self, command: str, timeout_seconds: float) -> CommandResult:
        if self._bash is None:
            raise FileNotFoundError("bash bulunamadı; Windows'ta Git for Windows kurulu olmalı.")
        # Alt süreçteki Python da UTF-8 yazsın: Türkçe harfler çıktıda bozulmasın.
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        process = await asyncio.create_subprocess_exec(
            str(self._bash),
            "-c",
            command,
            cwd=self._root,
            env=env,
            stdin=subprocess.DEVNULL,  # etkileşimli komut girdi bekleyip asılı kalmasın
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Komut kendi süreç grubunda başlar; böylece bütün ağacı birlikte öldürebiliriz.
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",
        )
        assert process.stdout is not None and process.stderr is not None
        # Çıktı tampona biz okuruz: zaman aşımında o ana kadar basılan kısım kaybolmasın.
        out, err = bytearray(), bytearray()
        reader = asyncio.ensure_future(
            asyncio.gather(_pump(process.stdout, out), _pump(process.stderr, err), process.wait())
        )
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.shield(reader), timeout_seconds)
        except TimeoutError:
            timed_out = True
            await _kill_tree(process)
            await _drain(reader)
        except asyncio.CancelledError:
            await _kill_tree(process)
            await _drain(reader)
            raise
        return CommandResult(
            stdout=_decode(out),
            stderr=_decode(err),
            exit_code=None if timed_out else process.returncode,
            timed_out=timed_out,
        )


async def _pump(stream: asyncio.StreamReader, buffer: bytearray) -> None:
    while chunk := await stream.read(65536):
        buffer.extend(chunk)


async def _drain(reader: "asyncio.Future[Any]") -> None:
    """Öldürülen sürecin borusu kapanınca okuma biter; kopuk bir torun boruyu tutarsa bırakılır."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(reader, 5)


def _decode(data: bytearray) -> str:
    # Windows programları satırı \r\n ile bitirir; modele her satırda fazladan \r gitmesin.
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


async def _kill_tree(process: asyncio.subprocess.Process) -> None:
    """Komutu ve başlattıklarını öldürür.

    Bilinen sınır (Windows): bash bitmiş ama `&` ile arka plana attığı iş sürüyorsa
    taskkill ağacı bulamaz, o iş yaşar. Kalıcı çözüm Job Object; POSIX'te süreç
    grubu bash ölse de yaşadığı için killpg arka plandakini de öldürür.
    """
    if sys.platform == "win32":
        if process.returncode is not None:
            return
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(process.pid),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        with contextlib.suppress(ProcessLookupError):  # grupta kimse kalmamış olabilir
            os.killpg(process.pid, signal.SIGKILL)
    await process.wait()
