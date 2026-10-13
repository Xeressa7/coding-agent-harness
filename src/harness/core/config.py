"""Yapılandırma: model, adım limiti, token bütçesi, çalışma dizini (rapor Bölüm 6).

Değerler komut satırından ya da yapılandırma dosyasından gelir. API anahtarı
yalnız ortam değişkeninden okunur.
"""

from pathlib import Path

from pydantic import BaseModel

DEFAULT_SETTINGS_PATH = Path.home() / ".harness" / "settings.json"


class Settings(BaseModel):
    """Kullanıcının kalıcı seçimleri; `harness model` ile değişir."""

    model: str = "gpt-6-luna"
    effort: str | None = None  # None: modelin kendi varsayılanı


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
