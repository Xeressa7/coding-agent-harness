"""Ortak veri tipleri: Message, ToolCall, ToolResult (rapor Bölüm 12).

Paketin en alt katmanı: hiçbir harness modülünü import etmez, herkes onu
import eder. Sağlayıcıdan bağımsızdır; OpenAI ya da Anthropic biçimine çeviri
llm/ altındaki adaptörlerin işidir. Bir mesaj dört rolden birini taşır:
system, user, assistant, tool.

Mesajlar yazıldıktan sonra değişmez; konuşma yalnız sona eklenerek büyür.
Böylece mesaj geçmişi aynı zamanda ajanın ne yaptığının güvenilir kaydıdır.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Record(BaseModel):
    """Bütün tiplerin ortak tabanı: değiştirilemez, tanımsız alan kabul etmez."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolCall(_Record):
    """Modelin bir aracı çalıştırma isteği.

    `arguments` modelin yazdığı ham JSON metnidir; burada çözülmez. Model bozuk
    JSON yazabilir: bunu yakalayıp hatayı modele bildirmek aracın işidir.
    """

    id: str
    name: str
    arguments: str


class ToolSpec(_Record):
    """Bir aracın modele tanıtımı: adı, ne işe yaradığı, girdilerinin JSON Schema'sı.

    Sağlayıcıdan bağımsızdır; OpenAI ya da başka bir biçime çeviri adaptörün işidir.
    """

    name: str
    description: str
    parameters: dict[str, Any]


class SystemMessage(_Record):
    """Modele verilen talimatlar."""

    role: Literal["system"] = "system"
    text: str


class UserMessage(_Record):
    """Kullanıcının yazdığı."""

    role: Literal["user"] = "user"
    text: str


class TokenUsage(_Record):
    """Sağlayıcının bildirdiği sayımlar; None bilinmiyor, 0 gerçek sıfırdır.

    Cache girdi, reasoning çıktı sayımının alt kümesidir; toplama eklenmez.
    """

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class TextDelta(_Record):
    """Kullanıcıya yönelik çıktı metninin parçası; araç/reasoning verisi değildir."""

    text: str


class AssistantMessage(_Record):
    """Modelin cevabı: metin, araç istekleri ya da ikisi birden.

    `reasoning` sağlayıcının gösterdiği düşünce metnidir (özet ya da açık düşünce);
    gizli düşüncenin kendisi değildir ve vermeyen sağlayıcıda boştur.
    `provider_data` aynı sağlayıcıya sonraki istekte aynen geri gönderilmesi gereken
    veridir (ör. imzalı düşünce blokları); anlamını yalnız o adaptör bilir.
    """

    role: Literal["assistant"] = "assistant"
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage | None = None
    first_text_seconds: float | None = Field(default=None, ge=0)
    reasoning: str = ""
    provider_data: dict[str, Any] | None = None


class ToolResult(_Record):
    """Bir araç isteğinin sonucu; hangi isteğe ait olduğunu `tool_call_id` söyler."""

    role: Literal["tool"] = "tool"
    tool_call_id: str
    content: str
    is_error: bool = False


Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolResult,
    Field(discriminator="role"),
]
"""Mesaj geçmişindeki herhangi bir satır. JSON'dan okunurken `role` alanına
bakılarak doğru sınıf seçilir."""
