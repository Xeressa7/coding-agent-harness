"""Döngü yöneticisi: Agent sınıfı (rapor Bölüm 7 ve 11).

Görevi alır ve modeli çağırır. Yanıtta araç çağrısı varsa çağrıyı izin
katmanından geçirip çalıştırır, sonucu mesaj geçmişine ekler ve modeli yeniden
çağırır. Model araç çağırmayı bıraktığında ya da adım/token limiti dolduğunda
durur.

Ekrana yazmaz: her adımda events.py'deki bir olayı üretir, onu çizmek ön
yüzün (cli/) işidir. İzin katmanı "sor" dediğinde PermissionRequested üretir
ve cevabı kendisi sormadan, `asend` ile geri gelecek şekilde bekler; cevapsız
(örn. sade `async for` ile) geçilirse güvenli taraf seçilir: araç reddedilir.
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from harness.core.anatomy import Anatomy
from harness.core.code_blocks import extract_code_blocks, save_code_blocks
from harness.core.context import truncated_history
from harness.core.events import (
    CodeBlocksSaved,
    ContextAssembled,
    Event,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    PermissionRequested,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.core.permissions import Decision, PermissionManager
from harness.llm.base import LLMClient, StreamingLLMClient
from harness.messages import AssistantMessage, Message, SystemMessage, ToolResult, UserMessage
from harness.tools.base import ToolRegistry


class Agent:
    """Modele sorar, istediği araçları çalıştırır, model "bitti" diyene kadar tekrarlar.

    Mesaj geçmişi `history`'de tutulur ve yalnız sona eklenerek büyür.
    `permissions` da açıktır: ön yüz, "bu oturum için her zaman izin ver"
    seçildiğinde doğrudan `agent.permissions.grant(ad)` çağırır.
    """

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        system_prompt: str,
        *,
        permissions: PermissionManager,
        max_steps: int = 20,
        model_label: str | None = None,
        effort: str | None = None,
        anatomy: Anatomy | None = None,
        root: Path | None = None,
        history: Sequence[Message] = (),
    ) -> None:
        """root: harness'ın kendisinin dosya yazdığı çalışma dizini (seviye 0 kod blokları).

        history: talimattan sonra gelen önceki konuşma; devam edilen oturum ya da
        sohbet ortasında model değişimi için.
        """
        if anatomy is not None and anatomy.save_code_blocks and root is None:
            raise ValueError("Kod bloklarını kaydeden anatomi bir çalışma dizini (root) ister.")
        self._model_label = model_label
        self._anatomy = anatomy
        self._root = root
        self._effort = effort
        self._llm = llm
        self._tools = tools
        self.permissions = permissions
        self._max_steps = max_steps
        self.history: list[Message] = [SystemMessage(text=system_prompt), *history]

    async def run(self, task: str) -> AsyncGenerator[Event, bool | None]:
        self.history.append(UserMessage(text=task))
        started = perf_counter()
        step = 0
        yield TaskStarted(
            task=task,
            model=self._model_label,
            effort=self._effort,
            anatomy=self._anatomy,
            timestamp=datetime.now(UTC),
        )
        try:
            for step in range(1, self._max_steps + 1):
                messages = tuple(truncated_history(self.history))
                specs = tuple(self._tools.specs())
                yield ContextAssembled(
                    step=step, messages=messages, tools=specs, timestamp=datetime.now(UTC)
                )
                yield ModelStarted(step=step, timestamp=datetime.now(UTC))
                model_started = perf_counter()
                consumer_seconds = 0.0
                if isinstance(self._llm, StreamingLLMClient):
                    reply: AssistantMessage | None = None
                    async with aclosing(self._llm.stream(messages, specs)) as parts:
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
                    reply = await self._llm.complete(messages, specs)
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
                    if self._anatomy and self._anatomy.save_code_blocks and self._root:
                        blocks, unnamed = extract_code_blocks(reply.text)
                        saved, rejected = save_code_blocks(blocks, self._root)
                        yield CodeBlocksSaved(
                            step=step,
                            saved=tuple(saved),
                            rejected=tuple(rejected),
                            unnamed=unnamed,
                            timestamp=datetime.now(UTC),
                        )
                    yield TaskFinished(
                        reason="done",
                        steps=step,
                        duration_seconds=perf_counter() - started,
                        timestamp=datetime.now(UTC),
                    )
                    return

                for call in reply.tool_calls:
                    decision = self.permissions.decide(call)
                    if decision is Decision.ASK:
                        answer = yield PermissionRequested(call=call)
                        decision = Decision.ALLOW if answer else Decision.DENY
                    if decision is Decision.DENY:
                        result = ToolResult(
                            tool_call_id=call.id,
                            content="İzin verilmedi, araç çalıştırılmadı.",
                            is_error=True,
                        )
                        self.history.append(result)
                        yield ToolFinished(
                            call=call,
                            result=result,
                            duration_seconds=0.0,
                            timestamp=datetime.now(UTC),
                        )
                        continue
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
