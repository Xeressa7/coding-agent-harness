"""Ortak test ayarı: hiçbir test gerçek ~/.harness klasörüne kayıt ya da ayar yazmaz/okumaz."""

from pathlib import Path

import pytest

from harness.cli import app as cli
from harness.core.config import Settings, load_settings, save_settings


@pytest.fixture(autouse=True)
def sessions_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "harness-sessions"
    monkeypatch.setattr(cli, "SESSIONS_HOME", home)
    return home


@pytest.fixture(autouse=True)
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLI kalıcı seçimleri geçici bir dosyadan okur ve oraya yazar."""
    path = tmp_path / "settings.json"

    def save(settings: Settings) -> None:
        save_settings(settings, path)

    monkeypatch.setattr(cli, "load_settings", lambda: load_settings(path))
    monkeypatch.setattr(cli, "save_settings", save)
    return path
