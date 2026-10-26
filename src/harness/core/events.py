"""Çekirdek → ön yüz sözleşmesi; JSONL'de eski kayıtlar da okunabilir."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.core.anatomy import Anatomy
from harness.messages import Message, TokenUsage, ToolCall, ToolResult, ToolSpec


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    timestamp: datetime | None = None


class TaskStarted(_Event):
    kind: Literal["task_started"] = "task_started"
    task: str
    provider: str | None = None  # None: sağlayıcı kaydı öncesinden kalma oturum (ChatGPT)
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
    reasoning: str = ""  # sağlayıcının gösterdiği düşünce özeti; gizli düşünce değildir


class PermissionRequested(_Event):
    """İzin katmanının "sor" dediği an; devam etmek için ön yüzün cevabı (asend ile) beklenir.

    Cevapsız geçilirse (örn. eski bir tüketici hâlâ `async for` kullanıyorsa) güvenli
    taraf seçilir: araç reddedilmiş sayılır.
    """

    kind: Literal["permission_requested"] = "permission_requested"
    call: ToolCall
    summary: str = ""  # aracın kendi anlatımı: "notes.txt", "$ pytest -q"


class ToolStarted(_Event):
    """Araç çalışmaya başladı. `category` ve `summary` telemetri içindir, modele gitmez."""

    kind: Literal["tool_started"] = "tool_started"
    call: ToolCall
    category: str | None = None  # read, search, write, exec, network, plan; eski kayıtta None
    summary: str = ""  # ne yapacak: "README.md · satır 1–40"


class ToolFinished(_Event):
    """Araç bitti. `outcome` sonucun kısa özeti ("40 satır", "çıkış kodu 1"),
    `details` aracın ölçümleri (satır, bayt, çıkış kodu, plan adımları...)."""

    kind: Literal["tool_finished"] = "tool_finished"
    call: ToolCall
    result: ToolResult
    duration_seconds: float | None = Field(default=None, ge=0)
    category: str | None = None
    summary: str = ""
    outcome: str = ""
    details: dict[str, Any] = {}


class CodeBlocksSaved(_Event):
    """Seviye 0: harness, modelin cevabındaki adlı kod bloklarını dosyaya yazdı.

    Bunu model değil harness yapar; araç çağrısı gibi gösterilmez.
    """

    kind: Literal["code_blocks_saved"] = "code_blocks_saved"
    step: int
    saved: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()  # çalışma dizini dışı ya da klasör
    unnamed: int = 0  # yolu olmayan, yazılmayan bloklar


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
    | CodeBlocksSaved
    | TaskFinished,
    Field(discriminator="kind"),
]
