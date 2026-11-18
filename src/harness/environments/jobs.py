"""Arka plan işleri: dev sunucusu, izleyici gibi bitmeyen komutlar.

Her iş kendi süreç ağacıyla başlar (environments/processes.py), çıktısı ayrı bir iş
parçacığında tampona okunur; olay döngüsünden bağımsızdır, turlar arasında yaşar. Model
çıktıyı parça parça okur (yalnız yenisi), işi durdurur. Harness kapanınca bütün işler ölür.
"""

import atexit
import codecs
import contextlib
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from harness.environments.processes import ProcessTree, spawn_options

MAX_JOBS = 5
BUFFER_CHARS = 200_000  # işin başından beri değil, son bu kadar karakter tutulur
READ_CHARS = 20_000  # bir okumada modele giden en fazla yeni çıktı


@dataclass(frozen=True)
class JobOutput:
    job_id: str
    command: str
    text: str  # son okumadan bu yana gelen çıktı (stdout ve stderr karışık, sırasıyla)
    dropped: int  # okunmadan tampondan düşen karakter
    exit_code: int | None  # None: çalışıyor

    @property
    def running(self) -> bool:
        return self.exit_code is None


class BackgroundJob:
    def __init__(self, job_id: str, command: str, argv: list[str], cwd: Path, env: dict[str, str]):
        self.job_id = job_id
        self.command = command
        self.started = time.monotonic()
        self._process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **spawn_options(),
        )
        self._tree = ProcessTree(self._process.pid)
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._text = ""
        self._read_at = 0  # _text içinde modelin okuduğu yer
        self._dropped = 0
        self._reader = threading.Thread(target=self._pump, name=f"job-{job_id}", daemon=True)
        self._reader.start()

    @property
    def exit_code(self) -> int | None:
        return self._process.poll()

    def _pump(self) -> None:
        assert self._process.stdout is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        stream = self._process.stdout.fileno()
        while chunk := os.read(stream, 65536):
            text = decoder.decode(chunk).replace("\r\n", "\n")
            with self._changed:
                self._text += text
                if len(self._text) > BUFFER_CHARS:
                    cut = len(self._text) - BUFFER_CHARS
                    self._dropped += max(0, cut - self._read_at)
                    self._read_at = max(0, self._read_at - cut)
                    self._text = self._text[cut:]
                self._changed.notify_all()
        with self._changed:
            self._changed.notify_all()

    def read(self, wait_seconds: float = 0) -> JobOutput:
        """Yeni çıktı; `wait_seconds` içinde yeni çıktı ya da iş bitişi beklenir."""
        deadline = time.monotonic() + wait_seconds
        with self._changed:
            while (
                len(self._text) == self._read_at
                and self._process.poll() is None
                and (left := deadline - time.monotonic()) > 0
            ):
                self._changed.wait(min(left, 0.2))
        if self._process.poll() is not None:
            self._reader.join(timeout=2)  # bitmiş işin son çıktısı da gelsin
        with self._lock:
            fresh = self._text[self._read_at :]
            dropped = self._dropped
            if len(fresh) > READ_CHARS:
                dropped += len(fresh) - READ_CHARS
                fresh = fresh[-READ_CHARS:]
            self._read_at = len(self._text)
            self._dropped = 0
        return JobOutput(self.job_id, self.command, fresh, dropped, self._process.poll())

    def stop(self) -> JobOutput:
        self._tree.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._process.wait(timeout=5)
        output = self.read()
        self._tree.close()
        return output


class JobTable:
    """Bir ortamın arka plan işleri; kimlikler `j1`, `j2`… biçimindedir."""

    def __init__(self) -> None:
        self._jobs: dict[str, BackgroundJob] = {}
        self._next = 1
        self._registered = False  # çıkışta durdurma, ilk iş başlayınca kaydedilir

    def start(self, command: str, argv: list[str], cwd: Path, env: dict[str, str]) -> BackgroundJob:
        running = [job for job in self._jobs.values() if job.exit_code is None]
        if len(running) >= MAX_JOBS:
            names = ", ".join(job.job_id for job in running)
            raise RuntimeError(
                f"En fazla {MAX_JOBS} arka plan işi çalışabilir ({names}); önce birini durdur."
            )
        if not self._registered:
            atexit.register(self.stop_all)
            self._registered = True
        job = BackgroundJob(f"j{self._next}", command, argv, cwd, env)
        self._next += 1
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> BackgroundJob | None:
        return self._jobs.get(job_id.strip())

    def all(self) -> list[BackgroundJob]:
        return list(self._jobs.values())

    def stop_all(self) -> None:
        for job in self._jobs.values():
            if job.exit_code is None:
                job.stop()
