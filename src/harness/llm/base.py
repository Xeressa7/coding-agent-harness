"""LLMClient arayüzü.

Bütün adaptörlerin uyduğu sözleşme: mesaj geçmişini alır, modelin yanıtını
(metin ve/veya araç çağrıları) messages.py tipleriyle döner. Çekirdek yalnız
bu arayüzü tanır; karşısında sahte model mi, gerçek bir sağlayıcı mı olduğunu
bilmez.
"""

from collections.abc import AsyncGenerator, Sequence
from typing import Protocol, runtime_checkable

from harness.messages import AssistantMessage, Message, TextDelta, ToolSpec


class LLMClient(Protocol):
    """Bir dil modeliyle konuşan her adaptörün uyması gereken sözleşme."""

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        """Mesaj geçmişini ve kullanabileceği araçları modele gönderir, cevabını döner."""
        ...


@runtime_checkable
class StreamingLLMClient(Protocol):
    """Opsiyonel yetenek: sıralı metin parçaları, ardından tek tamamlanmış mesaj.

    Tamamlanma olmadan akış biterse çağrı başarısızdır. Tüketici erken çıkışta
    generator'ı kapatır; HTTP bağlantısı da kapanmalıdır.
    """

    def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | AssistantMessage, None]: ...
