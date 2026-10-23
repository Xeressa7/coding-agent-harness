"""Çekirdek → ön yüz sözleşmesi; JSONL'de eski kayıtlar da okunabilir."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.core.anatomy import Anatomy
from harness.messages import Message, TokenUsage, ToolCall, ToolResult, ToolSpec


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    timestamp: datetime | None = None


class TaskStarted(_Event):
    kind: Literal["task_started"] = "task_started"
    task: str
    model: str | None = None
    effort: str | None = None
    anatomy: Anatomy | None = None  # None: anatomi kaydı öncesinden kalma oturum


class ContextAssembled(_Event):
    """Bu adımda modele giden her şeyin birebir kopyası: mesajlar ve sunulan araç şemaları.

    "Model neyi gördü?" sorusunun cevabıdır; kayıt büyür ama tahmin değil, gerçektir.
    """

    kind: Literal["context_assembled"] = "context_assembled"
    step: int
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()


class ModelStarted(_Event):
    kind: Literal["model_started"] = "model_started"
    step: int


class ModelTextDelta(_Event):
    """Kısmi kullanıcı metni; tamamlanma/başarı veya token sayımı değildir."""

    kind: Literal["model_text_delta"] = "model_text_delta"
    step: int
    text: str


class ModelReplied(_Event):
    kind: Literal["model_replied"] = "model_replied"
    step: int
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    duration_seconds: float | None = Field(default=None, ge=0)
    first_text_seconds: float | None = Field(default=None, ge=0)
    usage: TokenUsage | None = None


class PermissionRequested(_Event):
    """İzin katmanının "sor" dediği an; devam etmek için ön yüzün cevabı (asend ile) beklenir.

    Cevapsız geçilirse (örn. eski bir tüketici hâlâ `async for` kullanıyorsa) güvenli
    taraf seçilir: araç reddedilmiş sayılır.
    """

    kind: Literal["permission_requested"] = "permission_requested"
    call: ToolCall


class ToolStarted(_Event):
    kind: Literal["tool_started"] = "tool_started"
    call: ToolCall


class ToolFinished(_Event):
    kind: Literal["tool_finished"] = "tool_finished"
    call: ToolCall
    result: ToolResult
    duration_seconds: float | None = Field(default=None, ge=0)


class TaskFinished(_Event):
    """done yalnız modelin araç istemeyi bıraktığıdır; test başarısı değildir."""

    kind: Literal["task_finished"] = "task_finished"
    reason: Literal["done", "step_limit", "error", "cancelled"]
    steps: int
    duration_seconds: float | None = Field(default=None, ge=0)
    error: str | None = None


Event = Annotated[
    TaskStarted
    | ContextAssembled
    | ModelStarted
    | ModelTextDelta
    | ModelReplied
    | PermissionRequested
    | ToolStarted
    | ToolFinished
    | TaskFinished,
    Field(discriminator="kind"),
]
