"""Environment arayüzü: komutu zaman aşımıyla çalıştırır, çıktıyı ve çıkış kodunu döner."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict


class CommandResult(BaseModel):
    """Bir komutun bıraktığı iz. Çıkış kodu 0 değilse de komut çalışmıştır; hata değildir."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stdout: str
    stderr: str
    exit_code: int | None  # None: zaman aşımında öldürüldü, kod yok
    timed_out: bool = False


class Environment(Protocol):
    """Komutların çalıştığı yer; bugün bu makine, ileride Docker."""

    async def run(self, command: str, timeout_seconds: float) -> CommandResult: ...
