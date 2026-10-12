"""LLMClient arayüzü.

Bütün adaptörlerin uyduğu sözleşme: mesaj geçmişini alır, modelin yanıtını
(metin ve/veya araç çağrıları) messages.py tipleriyle döner. Çekirdek yalnız
bu arayüzü tanır; karşısında sahte model mi, gerçek bir sağlayıcı mı olduğunu
bilmez.
"""

from collections.abc import Sequence
from typing import Protocol

from harness.messages import AssistantMessage, Message, ToolSpec


class LLMClient(Protocol):
    """Bir dil modeliyle konuşan her adaptörün uyması gereken sözleşme."""

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        """Mesaj geçmişini ve kullanabileceği araçları modele gönderir, cevabını döner."""
        ...
