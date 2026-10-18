"""Döngü yöneticisi: Agent sınıfı (rapor Bölüm 7 ve 11).

Görevi alır ve modeli çağırır. Yanıtta araç çağrısı varsa çağrıyı izin
katmanından geçirip çalıştırır, sonucu mesaj geçmişine ekler ve modeli yeniden
çağırır. Model araç çağırmayı bıraktığında ya da adım/token limiti dolduğunda
durur.

Ekrana yazmaz, kullanıcıdan girdi almaz: her adımda events.py'deki bir olayı
üretir. O olayı ekrana çizmek ön yüzün (cli/) işidir.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import UTC, datetime
from time import perf_counter

from harness.core.events import (
    Event,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.llm.base import LLMClient, StreamingLLMClient
from harness.messages import AssistantMessage, Message, SystemMessage, UserMessage
from harness.tools.base import ToolRegistry


class Agent:
    """Modele sorar, istediği araçları çalıştırır, model "bitti" diyene kadar tekrarlar.

    Mesaj geçmişi `history`'de tutulur ve yalnız sona eklenerek büyür.
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        system_prompt: str,
        max_steps: int = 20,
        model_label: str | None = None,
        effort: str | None = None,
    ) -> None:
        self._model_label = model_label
        self._effort = effort
        self._llm = llm
        self._tools = tools
        self._max_steps = max_steps
        self.history: list[Message] = [SystemMessage(text=system_prompt)]

    async def run(self, task: str) -> AsyncGenerator[Event, None]:
        self.history.append(UserMessage(text=task))
        started = perf_counter()
        step = 0
        yield TaskStarted(
            task=task, model=self._model_label, effort=self._effort, timestamp=datetime.now(UTC)
        )
        try:
            for step in range(1, self._max_steps + 1):
                yield ModelStarted(step=step, timestamp=datetime.now(UTC))
                model_started = perf_counter()
                consumer_seconds = 0.0
                if isinstance(self._llm, StreamingLLMClient):
                    reply: AssistantMessage | None = None
                    async with aclosing(
                        self._llm.stream(self.history, self._tools.specs())
                    ) as parts:
                        async for part in parts:
                            if isinstance(part, AssistantMessage):
                                reply = part
                                break
                            if part.text:
                                paused = perf_counter()
                                yield ModelTextDelta(
                                    step=step, text=part.text, timestamp=datetime.now(UTC)
                                )
                                consumer_seconds += perf_counter() - paused
                    if reply is None:
                        raise RuntimeError("Model akışı tamamlanmış yanıt vermeden bitti.")
                else:
                    reply = await self._llm.complete(self.history, self._tools.specs())
                model_duration = perf_counter() - model_started - consumer_seconds
                self.history.append(reply)
                yield ModelReplied(
                    step=step,
                    text=reply.text,
                    tool_calls=reply.tool_calls,
                    usage=reply.usage,
                    first_text_seconds=reply.first_text_seconds,
                    duration_seconds=model_duration,
                    timestamp=datetime.now(UTC),
                )

                if not reply.tool_calls:
                    yield TaskFinished(
                        reason="done",
                        steps=step,
                        duration_seconds=perf_counter() - started,
                        timestamp=datetime.now(UTC),
                    )
                    return

                # TODO(izin katmanı): çağrı çalışmadan önce izin kontrolü eklenecek.
                for call in reply.tool_calls:
                    yield ToolStarted(call=call, timestamp=datetime.now(UTC))
                    tool_started = perf_counter()
                    result = await self._tools.call(call)
                    tool_duration = perf_counter() - tool_started
                    self.history.append(result)
                    yield ToolFinished(
                        call=call,
                        result=result,
                        duration_seconds=tool_duration,
                        timestamp=datetime.now(UTC),
                    )
        except asyncio.CancelledError:
            yield TaskFinished(
                reason="cancelled",
                steps=step,
                duration_seconds=perf_counter() - started,
                timestamp=datetime.now(UTC),
            )
            raise
        except Exception as exc:
            yield TaskFinished(
                reason="error",
                steps=step,
                error=str(exc),
                duration_seconds=perf_counter() - started,
                timestamp=datetime.now(UTC),
            )
            raise

        yield TaskFinished(
            reason="step_limit",
            steps=step,
            duration_seconds=perf_counter() - started,
            timestamp=datetime.now(UTC),
        )
