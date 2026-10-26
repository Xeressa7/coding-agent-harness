"""Ortak test ayarı: hiçbir test gerçek ~/.harness klasörüne kayıt yazmaz."""

from pathlib import Path

import pytest

from harness.cli import app as cli


@pytest.fixture(autouse=True)
def sessions_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "harness-sessions"
    monkeypatch.setattr(cli, "SESSIONS_HOME", home)
    return home
