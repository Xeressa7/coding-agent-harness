"""Yapılandırma: model, adım limiti, token bütçesi, çalışma dizini (rapor Bölüm 6).

Değerler komut satırından ya da yapılandırma dosyasından gelir. API anahtarı
yalnız ortam değişkeninden okunur.
"""

from pathlib import Path


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
