"""Yapılandırma: model, adım limiti, token bütçesi, çalışma dizini (rapor Bölüm 6).

Değerler komut satırından ya da yapılandırma dosyasından gelir. API anahtarları
ayrı dosyadadır (llm/credentials.py); bu dosyada sır yoktur.
"""

import re
from pathlib import Path

from pydantic import BaseModel

HARNESS_HOME = Path.home() / ".harness"
DEFAULT_SETTINGS_PATH = HARNESS_HOME / "settings.json"
# Oturum kayıtları ajanın çalıştığı projeye değil buraya yazılır: harness misafirdir.
DEFAULT_SESSIONS_HOME = HARNESS_HOME / "sessions"


class Settings(BaseModel):
    """Kullanıcının kalıcı seçimleri; sohbette /model, /effort, /anatomy, /theme ile değişir."""

    provider: str = "chatgpt"
    model: str = "gpt-6-luna"
    effort: str | None = None  # None: modelin kendi varsayılanı
    anatomy: str | None = None  # None: varsayılan anatomi
    theme: str | None = None  # None: varsayılan renk teması (cli/themes.py)


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    if not path.exists():
        return Settings()
    return Settings.model_validate_json(path.read_text(encoding="utf-8"))


def save_settings(settings: Settings, path: Path = DEFAULT_SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def find_project_root(start: Path, home: Path | None = None) -> Path:
    """Ajanın çalışacağı klasör: start'tan yukarı çıkarak bulunan ilk git deposunun kökü.

    Kullanıcı projenin bir alt klasöründeyken de ajan bütün projeyi görür (git'in
    kendisi de böyle davranır). Git deposu yoksa start'ın kendisi kullanılır.
    Ev klasörü depo olsa bile proje sayılmaz: ajan kullanıcının bütün dosyalarını
    görmesin.
    """
    start = start.resolve()
    home = (home or Path.home()).resolve()
    for folder in (start, *start.parents):
        if folder == home:
            break
        if (folder / ".git").exists():
            return folder
    return start


def project_sessions_dir(root: Path, home: Path = DEFAULT_SESSIONS_HOME) -> Path:
    r"""Bir projenin kayıt klasörü: proje yolu tek klasör adına çevrilir.

    C:\Users\ali\proje -> C--Users-ali-proje. Ad okunabilir kalır ve aynı adlı iki
    proje (farklı yerlerde) karışmaz.
    """
    return home / re.sub(r"[^A-Za-z0-9_.-]", "-", str(root.resolve())).strip("-")
