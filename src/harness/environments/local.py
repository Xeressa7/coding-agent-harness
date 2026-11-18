"""Yerel ortam: komutu bu makinede, çalışma dizininde bash ile çalıştırır.

Windows'ta bash, Git for Windows'un getirdiği bash'tir; System32'deki bash.exe
WSL'e gider ve proje dosyalarını başka bir dosya sisteminden görür, kullanılmaz.
Zaman aşımında ya da görev iptal edildiğinde komut, başlattığı alt süreçlerle
birlikte öldürülür; arkada sahipsiz süreç kalmaz (environments/processes.py).

Komut bittiği hâlde `&` ile bıraktığı bir süreç çıktı borusunu tutuyorsa (ör.
`npm run dev &`) eskiden araç zaman aşımına kadar beklerdi. Artık kısa bir bekleyişten
sonra o süreç öldürülür ve sonuç bunu söyler; bitmeyen işler için arka plan işi vardır.
"""

import asyncio
import contextlib
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from harness.environments.base import CommandResult
from harness.environments.jobs import BackgroundJob, JobTable
from harness.environments.processes import ProcessTree, spawn_options

ORPHAN_GRACE_SECONDS = 1.0  # komut bittikten sonra çıktı borularının kapanması için süre


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


def _environment() -> dict[str, str]:
    # Alt süreçteki Python da UTF-8 yazsın: Türkçe harfler çıktıda bozulmasın.
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


class LocalEnvironment:
    def __init__(self, root: Path, bash: Path | None = None) -> None:
        self._root = root.resolve()
        self._bash = bash or find_bash()
        self.jobs = JobTable()
        # `&` ile çıktısı yönlendirilmiş, boruyu bırakmış süreçler harness yaşadıkça yaşar.
        self._kept: list[ProcessTree] = []

    async def run(self, command: str, timeout_seconds: float) -> CommandResult:
        if self._bash is None:
            raise FileNotFoundError("bash bulunamadı; Windows'ta Git for Windows kurulu olmalı.")
        process = await asyncio.create_subprocess_exec(
            str(self._bash),
            "-c",
            command,
            cwd=self._root,
            env=_environment(),
            stdin=asyncio.subprocess.DEVNULL,  # etkileşimli komut girdi bekleyip asılı kalmasın
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **spawn_options(),  # süreç ağacı birlikte izlenir ve öldürülür
        )
        tree = ProcessTree(process.pid)
        assert process.stdout is not None and process.stderr is not None
        # Çıktı tampona biz okuruz: zaman aşımında o ana kadar basılan kısım kaybolmasın.
        out, err = bytearray(), bytearray()
        pumps = asyncio.ensure_future(
            asyncio.gather(_pump(process.stdout, out), _pump(process.stderr, err))
        )
        timed_out = left_behind = False
        try:
            try:
                await asyncio.wait_for(_exited(process), timeout_seconds)
            except TimeoutError:
                timed_out = True
                tree.kill()
                await _exited(process)
            else:
                try:
                    await asyncio.wait_for(asyncio.shield(pumps), ORPHAN_GRACE_SECONDS)
                except TimeoutError:  # bash bitti, bıraktığı süreç boruyu tutuyor
                    left_behind = True
                    tree.kill()
            await _drain(pumps)
        except asyncio.CancelledError:
            tree.kill()
            await _drain(pumps)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), 5)
            raise
        finally:
            self._keep_or_close(tree)
        return CommandResult(
            stdout=_decode(out),
            stderr=_decode(err),
            exit_code=None if timed_out else process.returncode,
            timed_out=timed_out,
            left_behind=left_behind,
        )

    def start(self, command: str) -> BackgroundJob:
        """Komutu arka plan işi olarak başlatır; bitmesi beklenmez."""
        if self._bash is None:
            raise FileNotFoundError("bash bulunamadı; Windows'ta Git for Windows kurulu olmalı.")
        argv = [str(self._bash), "-c", command]
        return self.jobs.start(command, argv, self._root, _environment())

    def _keep_or_close(self, tree: ProcessTree) -> None:
        """Ağaçta hâlâ süreç varsa (çıktısı dosyaya yönlendirilmiş `&` işi) tutamaç saklanır;
        Windows'ta tutamacı kapatmak onları da öldürürdü."""
        still: list[ProcessTree] = []
        for kept in self._kept:
            if kept.alive():
                still.append(kept)
            else:
                kept.close()
        self._kept = still
        if tree.alive():
            self._kept.append(tree)
        else:
            tree.close()


async def _exited(process: asyncio.subprocess.Process) -> None:
    """Sürecin kendisi bitince döner. `process.wait()` borular kapanana kadar da bekler;
    `&` ile bırakılan torun boruyu tutarsa bash'in bittiği görülemezdi."""
    while process.returncode is None:
        await asyncio.sleep(0.05)


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
