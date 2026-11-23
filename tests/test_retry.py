"""Yeniden deneme: 429/5xx/bağlantı hatasında bekle, akış başladıktan sonra asla."""

from collections.abc import AsyncGenerator, Sequence

import httpx
import pytest

from harness.llm.base import LLMError, StreamingLLMClient, retry_after_seconds
from harness.llm.retry import RetryingStreamingLLM, RetryPolicy, with_retry
from harness.messages import AssistantMessage, Message, TextDelta, ToolSpec

pytestmark = pytest.mark.anyio
REPLY = AssistantMessage(text="tamam")


class Flaky:
    """Verilen hataları sırayla atar; `after_parts` kadar parça verdikten sonra da atabilir."""

    def __init__(self, errors: list[BaseException], after_parts: int = 0) -> None:
        self.errors = errors
        self.after_parts = after_parts
        self.calls = 0

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return REPLY

    async def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | AssistantMessage, None]:
        self.calls += 1
        for _ in range(self.after_parts):
            yield TextDelta(text="parça ")
        if self.errors:
            raise self.errors.pop(0)
        yield REPLY


def recorder() -> tuple[list[float], object]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return slept, sleep


async def collect(llm: RetryingStreamingLLM) -> list[object]:
    return [part async for part in llm.stream([])]


async def test_rate_limit_waits_and_honours_retry_after() -> None:
    inner = Flaky(
        [LLMError("HTTP 429", status=429, retry_after=7), LLMError("HTTP 503", status=503)]
    )
    slept, sleep = recorder()
    llm = RetryingStreamingLLM(inner, RetryPolicy(), sleep)  # type: ignore[arg-type]
    assert await collect(llm) == [REPLY]
    assert inner.calls == 3 and slept == [7, 4.0]  # Retry-After, sonra üstel (2. deneme: 4 sn)
    assert [w.attempt for w in llm.waits] == [1, 2]


async def test_connection_error_is_retried_but_bad_request_is_not() -> None:
    slept, sleep = recorder()
    ok = RetryingStreamingLLM(Flaky([httpx.ConnectError("kopuk")]), RetryPolicy(), sleep)  # type: ignore[arg-type]
    assert await collect(ok) == [REPLY]
    bad = RetryingStreamingLLM(Flaky([LLMError("HTTP 400", status=400)]), RetryPolicy(), sleep)  # type: ignore[arg-type]
    with pytest.raises(LLMError, match="400"):
        await collect(bad)
    key = RetryingStreamingLLM(Flaky([LLMError("HTTP 401", status=401)]), RetryPolicy(), sleep)  # type: ignore[arg-type]
    with pytest.raises(LLMError, match="401"):
        await collect(key)
    assert slept == [2.0]


async def test_no_retry_after_the_stream_started() -> None:
    slept, sleep = recorder()
    inner = Flaky([LLMError("HTTP 502", status=502)], after_parts=2)
    llm = RetryingStreamingLLM(inner, RetryPolicy(), sleep)  # type: ignore[arg-type]
    with pytest.raises(LLMError):
        await collect(llm)
    assert inner.calls == 1 and slept == []


async def test_gives_up_when_attempts_or_total_wait_run_out() -> None:
    slept, sleep = recorder()
    errors: list[BaseException] = [LLMError("HTTP 429", status=429) for _ in range(20)]
    llm = RetryingStreamingLLM(Flaky(errors), RetryPolicy(max_total_wait=20), sleep)  # type: ignore[arg-type]
    with pytest.raises(LLMError, match="429"):
        await collect(llm)
    assert slept == [2.0, 4.0, 8.0]  # 2+4+8=14; sıradaki 16 sınırı (20) aşar


async def test_complete_is_retried_and_streaming_capability_is_kept() -> None:
    slept, sleep = recorder()
    llm = with_retry(Flaky([LLMError("HTTP 429", status=429)]))  # type: ignore[arg-type]
    llm.sleep = sleep  # type: ignore[assignment]
    assert isinstance(llm, StreamingLLMClient)
    assert await llm.complete([]) == REPLY and slept == [2.0]


def test_retry_after_header_parsing() -> None:
    assert retry_after_seconds("12") == 12 and retry_after_seconds("1.5") == 1.5
    assert retry_after_seconds(None) is None and retry_after_seconds("Wed, 21 Oct") is None
