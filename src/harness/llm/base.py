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
    `transient`: bağlantı kopması gibi geçici bir hata (durum kodu yoktur, yeniden denenebilir).
    `retry_after`: sunucunun "şu kadar saniye sonra dene" dediği süre (Retry-After başlığı).
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        transient: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient
        self.retry_after = retry_after

    @property
    def rejects_key(self) -> bool:
        return self.status in (401, 403)

    @property
    def retryable(self) -> bool:
        """Beklenip yeniden denenmeye değer mi: hız sınırı, sunucu hatası ya da bağlantı kopması."""
        return self.transient or self.status in RETRYABLE_STATUS


RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def retry_after_seconds(value: str | None) -> float | None:
    """Retry-After başlığı saniye olarak (yalnız sayı biçimi; tarih biçimi yok sayılır)."""
    try:
        return max(float(value), 0.0) if value else None
    except ValueError:
        return None


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
    images: bool | None = None  # görsel girdi kabul eder mi; None: sağlayıcı bildirmiyor


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
