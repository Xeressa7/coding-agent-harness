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

from pydantic import TypeAdapter

from harness.core.events import Event, ModelReplied, TaskStarted, ToolFinished
from harness.messages import AssistantMessage, Message, UserMessage

_EVENT = TypeAdapter[Event](Event)


class SessionLogger:
    """Olayları bir JSONL dosyasına, geldikçe ekler."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def write(self, event: Event) -> None:
        # Her olay ayrı açılıp eklenir: oturum yarıda kesilse de o ana kadarki kayıt diskte kalır.
        with self.path.open("a", encoding="utf-8") as f:
            f.write(_EVENT.dump_json(event).decode() + "\n")


def read_session(path: Path) -> list[Event]:
    """Kaydı geri okur; her satır doğru olay sınıfına oturur."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_EVENT.validate_json(line) for line in lines if line.strip()]


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


def list_sessions(folder: Path) -> list[SessionSummary]:
    """Klasördeki oturumlar, en son kullanılan önce. Görevi olmayan kayıt listelenmez."""
    summaries: list[SessionSummary] = []
    for path in folder.glob("*.jsonl"):
        tasks: list[str] = []
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
        if tasks:
            updated = datetime.fromtimestamp(path.stat().st_mtime)
            summaries.append(SessionSummary(path, updated, len(tasks), tasks[0]))
    return sorted(summaries, key=lambda s: s.updated, reverse=True)
