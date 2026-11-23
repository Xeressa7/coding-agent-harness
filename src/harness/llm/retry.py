"""Geçici hatalarda bekleyip yeniden deneme: hız sınırı (429), sunucu hatası (5xx), kopma.

Her ticari harness'ta olan bu katman olmadan ajan ilk 429'da görevi bırakır (2026-11-23 pilotu:
üç ajan aynı anda GLM-5.3'e yüklenince harness 47. saniyede düştü, OpenCode ve ZCode bekleyip
sürdü). Kurallar:

- Yalnız `retryable` hatalar (LLMError.retryable) ve httpx bağlantı hataları yeniden denenir;
  anahtar reddi (401/403) ya da geçersiz istek (400) hemen yükselir.
- Akışta ilk parça çıktıktan sonra yeniden denenmez: model metni ekrana ve geçmişe yazılmaya
  başlamıştır, ikinci deneme cevabı ikiler.
- Bekleme üstel artar (2, 4, 8 ... en çok 60 sn); sunucu Retry-After verdiyse ona uyulur
  (en çok 120 sn). Toplam bekleme ~5 dakikayı aşınca son hata yükselir.

Her bekleme `waits` listesine yazılır. `HARNESS_RETRY_LOG` ortam değişkeni bir dosya yolu verirse
her bekleme oraya da bir JSON satırı olarak eklenir (deney koşucusu 429 süresini böyle ölçer; G03 /
5. tur ön kaydı: bekleme ajan süresinin %20'sini aşan koşu geçersiz). Değişken yoksa hiçbir şey
yazılmaz.
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field

import httpx

from harness.llm.base import LLMClient, LLMError, StreamingLLMClient
from harness.messages import AssistantMessage, Message, ReasoningDelta, TextDelta, ToolSpec

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class RetryPolicy:
    max_attempts: int = 8
    base_delay: float = 2.0
    max_delay: float = 60.0
    max_retry_after: float = 120.0
    max_total_wait: float = 300.0

    def delay(self, attempt: int, error: BaseException) -> float:
        """`attempt`. başarısız denemeden sonra beklenecek süre (1'den başlar)."""
        hinted = error.retry_after if isinstance(error, LLMError) else None
        if hinted is not None:
            return min(hinted, self.max_retry_after)
        return min(self.base_delay * 2 ** (attempt - 1), self.max_delay)


def is_retryable(error: BaseException) -> bool:
    if isinstance(error, LLMError):
        return error.retryable
    return isinstance(error, httpx.TransportError)


@dataclass
class Waited:
    attempt: int
    seconds: float
    reason: str


def _log_wait(wait: Waited) -> None:
    path = os.environ.get("HARNESS_RETRY_LOG")
    if not path:
        return
    line = {
        "time": time.time(),
        "attempt": wait.attempt,
        "seconds": wait.seconds,
        "reason": wait.reason,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass  # ölçüm kaydı ajanı düşürmemeli


@dataclass
class RetryingLLM:
    """Yalnız `complete` sunan istemci için yeniden deneme sarmalayıcısı."""

    inner: LLMClient
    policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Sleep = asyncio.sleep
    waits: list[Waited] = field(default_factory=lambda: [])

    async def _backoff(self, attempt: int, waited: float, error: BaseException) -> float:
        """Bekler ve toplam bekleneni döner; bekleme hakkı bittiyse hatayı yükseltir."""
        if not is_retryable(error) or attempt >= self.policy.max_attempts:
            raise error
        seconds = self.policy.delay(attempt, error)
        if waited + seconds > self.policy.max_total_wait:
            raise error
        wait = Waited(attempt, seconds, str(error)[:200])
        self.waits.append(wait)
        _log_wait(wait)
        await self.sleep(seconds)
        return waited + seconds

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        attempt, waited = 1, 0.0
        while True:
            try:
                return await self.inner.complete(history, tools)
            except (LLMError, httpx.TransportError) as error:
                waited = await self._backoff(attempt, waited, error)
                attempt += 1


@dataclass
class RetryingStreamingLLM(RetryingLLM):
    """Akış sunan istemci: ilk parça çıkana kadar oluşan geçici hatalar yeniden denenir."""

    inner: LLMClient

    async def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | ReasoningDelta | AssistantMessage, None]:
        assert isinstance(self.inner, StreamingLLMClient)
        attempt, waited = 1, 0.0
        while True:
            started = False
            try:
                async with aclosing(self.inner.stream(history, tools)) as parts:
                    async for part in parts:
                        started = True
                        yield part
                return
            except (LLMError, httpx.TransportError) as error:
                if started:
                    raise
                waited = await self._backoff(attempt, waited, error)
                attempt += 1


def with_retry(llm: LLMClient, policy: RetryPolicy | None = None) -> RetryingLLM:
    """Akış yeteneği korunarak yeniden deneme eklenir."""
    policy = policy or RetryPolicy()
    if isinstance(llm, StreamingLLMClient):
        return RetryingStreamingLLM(llm, policy)
    return RetryingLLM(llm, policy)
