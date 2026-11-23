"""Görsel ekleri: adaptörlerin ortak yardımcısı (dosyayı okuyup base64'e çevirir).

Her API görseli kendi biçimiyle ister; bu modül yalnız ortak kısmı yapar: diskteki
dosyayı okur, okunamıyorsa modele söylenecek notu hazırlar.
"""

import base64
from pathlib import Path

from harness.messages import ImageAttachment


def image_base64(image: ImageAttachment) -> str | None:
    """Görselin base64 metni; dosya yoksa ya da okunamıyorsa None."""
    try:
        return base64.b64encode(Path(image.path).read_bytes()).decode("ascii")
    except OSError:
        return None


def data_url(image: ImageAttachment) -> str | None:
    data = image_base64(image)
    return f"data:{image.media_type};base64,{data}" if data is not None else None


def missing_note(image: ImageAttachment) -> str:
    return f"[Görsel eklenmişti ama dosyası artık yok: {image.name or Path(image.path).name}]"
