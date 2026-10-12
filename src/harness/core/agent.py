"""Döngü yöneticisi: Agent sınıfı (rapor Bölüm 7 ve 11).

Görevi alır ve modeli çağırır. Yanıtta araç çağrısı varsa çağrıyı izin
katmanından geçirip çalıştırır, sonucu mesaj geçmişine ekler ve modeli yeniden
çağırır. Model araç çağırmayı bıraktığında ya da adım/token limiti dolduğunda
durur.

Ekrana yazmaz, kullanıcıdan girdi almaz: her adımda events.py'deki bir olayı
üretir. O olayı ekrana çizmek ön yüzün (cli/) işidir.
"""

from collections.abc import AsyncIterator

from harness.core.events import Event, ModelReplied, TaskFinished, TaskStarted, ToolFinished
from harness.llm.base import LLMClient
from harness.messages import Message, SystemMessage, UserMessage
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
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._max_steps = max_steps
        self.history: list[Message] = [SystemMessage(text=system_prompt)]

    async def run(self, task: str) -> AsyncIterator[Event]:
        """Görevi çalıştırır; her adımda bir olay verir (async generator)."""
        self.history.append(UserMessage(text=task))
        yield TaskStarted(task=task)

        for step in range(1, self._max_steps + 1):
            reply = await self._llm.complete(self.history, self._tools.specs())
            self.history.append(reply)
            yield ModelReplied(step=step, text=reply.text, tool_calls=reply.tool_calls)

            if not reply.tool_calls:
                yield TaskFinished(reason="done", steps=step)
                return

            # TODO(izin katmanı): çağrı çalışmadan önce core/permissions.py'den geçecek.
            for call in reply.tool_calls:
                result = await self._tools.call(call)
                self.history.append(result)
                yield ToolFinished(call=call, result=result)

        yield TaskFinished(reason="step_limit", steps=self._max_steps)
