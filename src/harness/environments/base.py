"""Environment arayüzü: komutu zaman aşımıyla çalıştırır, çıktıyı ve çıkış kodunu döner."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from harness.environments.jobs import BackgroundJob


class CommandResult(BaseModel):
    """Bir komutun bıraktığı iz. Çıkış kodu 0 değilse de komut çalışmıştır; hata değildir."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stdout: str
    stderr: str
    exit_code: int | None  # None: zaman aşımında öldürüldü, kod yok
    timed_out: bool = False
    # Komut bitti ama `&` ile bıraktığı süreç çıktı borusunu tutuyordu; o süreç öldürüldü.
    left_behind: bool = False


class Environment(Protocol):
    """Komutların çalıştığı yer; bugün bu makine, ileride Docker."""

    async def run(self, command: str, timeout_seconds: float) -> CommandResult: ...


@runtime_checkable
class BackgroundEnvironment(Protocol):
    """Arka plan işi de başlatabilen ortam (bugün yalnız yerel ortam)."""

    async def run(self, command: str, timeout_seconds: float) -> CommandResult: ...

    def start(self, command: str) -> BackgroundJob: ...
