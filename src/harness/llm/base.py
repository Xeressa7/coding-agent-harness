"""LLMClient arayüzü.

Bütün adaptörlerin uyduğu sözleşme: mesaj geçmişini alır, modelin yanıtını
(metin ve/veya araç çağrıları) messages.py tipleriyle döner. Çekirdek yalnız
bu arayüzü tanır; karşısında sahte model mi, gerçek bir sağlayıcı mı olduğunu
bilmez.
"""

from collections.abc import AsyncGenerator, Sequence
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel

from harness.messages import AssistantMessage, Message, ReasoningDelta, TextDelta, ToolSpec


class LLMError(Exception):
    """Model isteği başarısız; mesaj kullanıcıya gösterilebilir. Her adaptörün hatası bundandır.

    `status`, hata bir HTTP cevabından geliyorsa durum kodudur (401/403 = anahtar reddedildi).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def rejects_key(self) -> bool:
        return self.status in (401, 403)


def error_message(body: object) -> str | None:
    """Sağlayıcının hata gövdesindeki okunur mesaj.

    Biçimler: `{"error": {"message": ...}}` (OpenAI, Anthropic, OpenCode) ya da
    `{"error": "..."}`. Bulunamazsa None; çağıran ham gövdeyi gösterir.
    """
    if not isinstance(body, dict):
        return None
    error = cast(dict[str, object], body).get("error")
    message = cast(dict[str, object], error).get("message") if isinstance(error, dict) else error
    return str(message) if message else None


class Effort(BaseModel):
    """Modelin ne kadar düşüneceği: seviye adı ve sağlayıcının açıklaması."""

    name: str
    description: str = ""


class ModelInfo(BaseModel):
    """Sağlayıcıda kullanılabilen bir model ve desteklediği effort seviyeleri."""

    slug: str
    display_name: str
    description: str = ""
    default_effort: str | None = None
    efforts: list[Effort] = []


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

    Düşünce parçaları (ReasoningDelta) metin parçalarının arasına karışabilir.

    Tamamlanma olmadan akış biterse çağrı başarısızdır. Tüketici erken çıkışta
    generator'ı kapatır; HTTP bağlantısı da kapanmalıdır.
    """

    def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | ReasoningDelta | AssistantMessage, None]: ...
