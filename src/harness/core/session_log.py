"""Oturum kaydedici: SessionLogger (rapor Bölüm 12, FR-08).

Her model yanıtını, araç çağrısını ve sonucunu JSONL dosyasına satır satır
yazar; oturum sonradan adım adım incelenebilir. API anahtarları kayda girmez.

Kayıt, çekirdeğin ürettiği olayların kendisidir: her satır bir olay. Bu yüzden
kayıt geri okunup aynı ön yüzle yeniden oynatılabilir.
"""

from pathlib import Path

from pydantic import TypeAdapter

from harness.core.events import Event

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
