"""Oturum kaydedici: SessionLogger (rapor Bölüm 12, FR-08).

Her model yanıtını, araç çağrısını ve sonucunu JSONL dosyasına satır satır
yazar; oturum sonradan adım adım incelenebilir. API anahtarları kayda girmez.

Kayıt, çekirdeğin ürettiği olayların kendisidir: her satır bir olay. Bu yüzden
kayıt geri okunup aynı ön yüzle yeniden oynatılabilir.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from harness.core.events import (
    ContextAssembled,
    Event,
    HarnessIntervened,
    ModelReplied,
    TaskStarted,
    ToolFinished,
)
from harness.core.finish_check import note as finish_note
from harness.messages import AssistantMessage, Message, UserMessage

_EVENT = TypeAdapter[Event](Event)


class SessionLogger:
    """Olayları bir JSONL dosyasına, geldikçe ekler.

    Bağlam olayı her adımda konuşmanın tamamını taşır; dosyaya yalnız bir önceki bağlamdan
    farklı kısmı yazılır (bkz. `ContextAssembled`). Okuyan `read_session` birebir geri kurar.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._context: ContextAssembled | None = None  # bu kaydedicinin yazdığı son bağlam

    def write(self, event: Event) -> None:
        stored = event
        if isinstance(event, ContextAssembled):
            stored = compact_context(event, self._context)
            self._context = event
        # Her olay ayrı açılıp eklenir: oturum yarıda kesilse de o ana kadarki kayıt diskte kalır.
        with self.path.open("a", encoding="utf-8") as f:
            f.write(_EVENT.dump_json(stored).decode() + "\n")


def compact_context(event: ContextAssembled, previous: ContextAssembled | None) -> ContextAssembled:
    """Öncekiyle ortak baştaki mesajları ve aynı araç şemalarını atar; kayıt için."""
    if previous is None:
        return event
    reused = 0
    for old, new in zip(previous.messages, event.messages, strict=False):
        if old is not new and old != new:
            break
        reused += 1
    tools_reused = bool(event.tools) and event.tools == previous.tools
    if not reused and not tools_reused:
        return event
    return event.model_copy(
        update={
            "messages": event.messages[reused:],
            "reused": reused,
            "tools": () if tools_reused else event.tools,
            "tools_reused": tools_reused,
        }
    )


def expand_context(event: ContextAssembled, previous: ContextAssembled | None) -> ContextAssembled:
    """`compact_context`'in tersi: kayıttaki kısaltılmış bağlamı tam kopyaya çevirir."""
    if not event.reused and not event.tools_reused:
        return event
    if previous is None or event.reused > len(previous.messages):
        raise ValueError(f"Adım {event.step}: bağlam önceki bağlama dayanıyor ama o kayıtta yok.")
    return event.model_copy(
        update={
            "messages": previous.messages[: event.reused] + event.messages,
            "reused": 0,
            "tools": previous.tools if event.tools_reused else event.tools,
            "tools_reused": False,
        }
    )


def read_session(path: Path) -> list[Event]:
    """Kaydı geri okur; her satır doğru olay sınıfına oturur, bağlamlar tam hâline açılır."""
    events: list[Event] = []
    context: ContextAssembled | None = None
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = _EVENT.validate_json(line)
            if isinstance(event, ContextAssembled):
                event = context = expand_context(event, context)
            events.append(event)
    return events


def history_from_events(events: Sequence[Event]) -> list[Message]:
    """Kayıttan, oturuma devam etmek için gereken konuşmayı (talimat hariç) yeniden kurar.

    Yarıda kalmış adım atılır: araç istemiş ama sonuçlarının hepsini alamamış bir
    model cevabı sağlayıcıya geri gönderilemez. Böylece durdurulmuş ya da hata
    almış bir oturum da kaldığı yerden sürer.
    """
    history: list[Message] = []
    pending: list[Message] = []  # sonuçları tamamlanana kadar bekleyen adım
    waiting: set[str] = set()

    for event in events:
        if isinstance(event, TaskStarted):
            pending, waiting = [], set()
            history.append(UserMessage(text=event.task))
        elif isinstance(event, ModelReplied):
            pending, waiting = [], set()
            reply = AssistantMessage(
                text=event.text, tool_calls=event.tool_calls, usage=event.usage
            )
            if not event.tool_calls:
                history.append(reply)
            else:
                pending = [reply]
                waiting = {call.id for call in event.tool_calls}
        elif (
            isinstance(event, HarnessIntervened)
            and event.layer == "finish_check"
            and event.action == "warn"
        ):
            history.append(UserMessage(text=finish_note(event.summary)))  # modele giden metin
        elif isinstance(event, ToolFinished) and event.call.id in waiting:
            pending.append(event.result)
            waiting.discard(event.call.id)
            if not waiting:
                history.extend(pending)
                pending = []
    return history


@dataclass(frozen=True)
class SessionSummary:
    """Oturum listesinde bir satır: dosyayı baştan sona doğrulamadan hızlıca çıkarılır."""

    path: Path
    updated: datetime
    turns: int
    first_task: str
    last_task: str = ""
    model: str | None = None  # son turun modeli
    anatomy: str | None = None  # son turun anatomisi


def list_sessions(folder: Path) -> list[SessionSummary]:
    """Klasördeki oturumlar, en son kullanılan önce. Görevi olmayan kayıt listelenmez."""
    summaries: list[SessionSummary] = []
    for path in folder.glob("*.jsonl"):
        tasks: list[str] = []
        model: str | None = None
        anatomy: str | None = None
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                # Bağlam kayıtları büyük olabilir; yalnız görev satırları çözülür.
                if '"task_started"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("kind") == "task_started":
                    tasks.append(str(record.get("task", "")))
                    model = record.get("model") or model
                    shape: object = record.get("anatomy")
                    if isinstance(shape, dict):
                        anatomy = str(cast(dict[str, object], shape).get("name") or "") or None
        if tasks:
            updated = datetime.fromtimestamp(path.stat().st_mtime)
            summaries.append(
                SessionSummary(path, updated, len(tasks), tasks[0], tasks[-1], model, anatomy)
            )
    return sorted(summaries, key=lambda s: s.updated, reverse=True)
