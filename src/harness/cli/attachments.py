"""Görsel ekleri: panodan, sürükle-bırak yoldan ya da `--image` ile gelen görseller.

Her görsel içeriğinin özetiyle (`sha256`) `~/.harness/attachments/` altına yazılır;
mesaj yalnız bu yolu taşır. Aynı görsel iki kez eklense de tek dosyadır. Türü dosya
adından değil ilk baytlardan anlaşılır (uzantısı yanlış dosya da doğru gider).
"""

import hashlib
import os
import shlex
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from harness.core.config import HARNESS_HOME
from harness.messages import ImageAttachment

ATTACHMENTS_HOME = HARNESS_HOME / "attachments"
# Anthropic API görsel başına 5 MB kabul eder; sağlayıcıdan bağımsız güvenli sınır.
MAX_BYTES = 5 * 1024 * 1024
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


class AttachmentError(ValueError):
    """Görsel eklenemedi; mesaj kullanıcıya olduğu gibi gösterilir."""


def sniff(data: bytes) -> str | None:
    """İlk baytlardan görsel türü; tanınmayan biçim None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(size // 1024, 1)} KB"


def store(data: bytes, name: str, folder: Path | None = None) -> ImageAttachment:
    """Baytları ek deposuna yazar; tür tanınmazsa ya da sınır aşılırsa AttachmentError."""
    media_type = sniff(data)
    if media_type is None:
        raise AttachmentError(f"{name}: desteklenen bir görsel değil (PNG, JPEG, GIF, WebP).")
    if len(data) > MAX_BYTES:
        raise AttachmentError(
            f"{name}: {human_size(len(data))}; sınır {human_size(MAX_BYTES)}. "
            "Görseli küçültüp ya da JPEG olarak kaydedip yeniden ekle."
        )
    digest = hashlib.sha256(data).hexdigest()[:24]
    folder = folder or ATTACHMENTS_HOME  # çağrı anında okunur (testler başka klasör verir)
    path = folder / f"{digest}.{_EXTENSIONS[media_type]}"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return ImageAttachment(path=str(path), media_type=media_type, name=name, size=len(data))


def from_file(path: Path, folder: Path | None = None) -> ImageAttachment:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise AttachmentError(f"{path}: okunamadı ({exc.strerror or exc}).") from exc
    return store(data, path.name, folder)


def split_pasted_paths(text: str, *, windows: bool) -> list[str]:
    """Yapıştırılan metni yollara böler. POSIX kipi `\\` işaretini kaçış sayar; Windows
    yolları (`C:\\Users\\…`) bozulmasın diye Windows'ta tırnaklar elle soyulur."""
    if not windows:
        return shlex.split(text)
    parts = shlex.split(text, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def dropped_paths(text: str) -> list[Path] | None:
    """Terminale sürüklenen dosyalar yol olarak yapıştırılır ('…' tırnaklı, file:// ya da
    kaçışlı boşluklu). Yapıştırılanın tamamı var olan görsel dosyalarıysa onların yolları;
    değilse None (metin metin olarak yapıştırılır)."""
    text = text.strip()
    if not text:
        return None
    try:
        parts = split_pasted_paths(text, windows=os.name == "nt")
    except ValueError:
        return None
    paths: list[Path] = []
    for part in parts:
        if part.startswith("file:"):
            # url2pathname yüzde kaçışlarını çözer; Windows'ta /C:/… → C:\…
            part = url2pathname(urlparse(part).path)
        path = Path(part).expanduser()
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            return None
        if not path.is_file():
            return None
        paths.append(path)
    return paths or None
