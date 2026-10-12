"""Çekirdeğin ön yüze bildirdiği olaylar.

Örnekler: modelden metin parçası geldi, araç çağrısı başladı/bitti, kullanıcı
onayı gerekiyor, görev bitti. Çekirdek ile ön yüz arasındaki tek sözleşme
budur. Aynı çekirdeğin ileride TUI, masaüstü ya da web ön yüzüyle
çalışabilmesini sağlayan ayrım burada.

Olaylar da mesajlar gibi değiştirilemez ve JSON'a çevrilebilir: oturum kaydı
bu olayların satır satır yazılmış hâlidir, masaüstü ön yüzü de ileride aynı
olayları ağ üzerinden alacak.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.messages import ToolCall, ToolResult


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskStarted(_Event):
    """Kullanıcı bir görev verdi."""

    kind: Literal["task_started"] = "task_started"
    task: str


class ModelReplied(_Event):
    """Model cevap verdi: metin, araç istekleri ya da ikisi birden."""

    kind: Literal["model_replied"] = "model_replied"
    step: int
    text: str
    tool_calls: tuple[ToolCall, ...] = ()


class ToolFinished(_Event):
    """İstenen araç çalıştı; sonucu (ya da hatası) modele gidecek."""

    kind: Literal["tool_finished"] = "tool_finished"
    call: ToolCall
    result: ToolResult


class TaskFinished(_Event):
    """Döngü durdu.

    `done`: model araç istemeden cevap verdi, iş bitti.
    `step_limit`: adım sınırı doldu, model hâlâ araç istiyordu.
    """

    kind: Literal["task_finished"] = "task_finished"
    reason: Literal["done", "step_limit"]
    steps: int


Event = Annotated[
    TaskStarted | ModelReplied | ToolFinished | TaskFinished,
    Field(discriminator="kind"),
]
"""Çekirdeğin üretebileceği herhangi bir olay. JSON'dan okunurken `kind`
alanına bakılarak doğru sınıf seçilir."""
