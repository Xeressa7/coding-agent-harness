"""Ortak test ayarı: hiçbir test gerçek ~/.harness klasörüne kayıt ya da ayar yazmaz/okumaz."""

from pathlib import Path

import pytest

from harness.cli import app as cli
from harness.cli import commands, themes
from harness.core.config import Settings, load_settings, save_settings
from harness.llm import credentials, providers


@pytest.fixture(autouse=True)
def sessions_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "harness-sessions"
    monkeypatch.setattr(cli, "SESSIONS_HOME", home)
    monkeypatch.setattr(cli, "EVALS_HOME", tmp_path / "harness-evals")
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


@pytest.fixture(autouse=True)
def auth_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Kayıtlı anahtarlar ve ChatGPT girişi geçici klasörde; gerçekleri okunmaz, silinmez.

    Makinede tanımlı anahtar ortam değişkenleri de silinir: testin sonucu kimin
    bilgisayarında koştuğuna bağlı olmasın.
    """
    folder = tmp_path / "auth"
    for name in {p.api_key_env for p in providers.PROVIDERS.values() if p.api_key_env}:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(credentials, "AUTH_PATH", folder / "auth.json")
    monkeypatch.setattr(providers, "CHATGPT_CREDENTIALS", folder / "chatgpt.json")
    monkeypatch.setattr(commands, "CHATGPT_CREDENTIALS", folder / "chatgpt.json")
    return folder


@pytest.fixture(autouse=True)
def theme_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Kişisel tema dosyası geçici klasörde; makinedeki ~/.harness/theme.toml okunmaz."""
    path = tmp_path / "theme.toml"
    monkeypatch.setattr(themes, "THEME_FILE", path)
    return path
