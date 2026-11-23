"""`harness desktop`: pywebview kurulu değilse serve'a döner, varsa gerçek sunucuyu açar."""

import socket
import sys
import types
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.cli import desktop


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_available_is_false_without_pywebview(monkeypatch: pytest.MonkeyPatch) -> None:
    # sys.modules[ad] = None: Python'un "bu modül yok" için resmi testi; gerçekten
    # kurulu olup olmamasından bağımsız, geliştirici makinesine göre değişmez.
    monkeypatch.setitem(sys.modules, "webview", None)
    assert desktop.available() is False


def test_available_is_true_when_pywebview_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    assert desktop.available() is True


def test_wait_for_port_detects_a_listening_socket() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        assert desktop.wait_for_port("127.0.0.1", sock.getsockname()[1], timeout=2) is True
    finally:
        sock.close()


def test_wait_for_port_times_out_when_nothing_listens() -> None:
    port = _free_port()  # bağlanıp kapatıldı: artık hiçbir şey dinlemiyor
    assert desktop.wait_for_port("127.0.0.1", port, timeout=0.3) is False


def test_open_window_delegates_to_pywebview(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []
    fake = types.ModuleType("webview")
    fake.create_window = lambda *a: calls.append(("create_window", a))  # type: ignore[attr-defined]
    fake.start = lambda: calls.append(("start", ()))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)

    desktop.open_window("Harness", "http://x")

    assert calls == [("create_window", ("Harness", "http://x")), ("start", ())]


def test_desktop_falls_back_to_serve_when_pywebview_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(desktop, "available", lambda: False)
    calls: list[dict[str, object]] = []

    def fake_serve(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cli, "serve", fake_serve)

    result = CliRunner().invoke(cli.app, ["desktop", "--demo"])

    assert result.exit_code == 0, result.output
    # Rich konsolun satır kaydırması yüzünden tam metin değil, anahtar parçalar aranır.
    assert "pywebview kurulu değil" in result.output
    assert "tarayıcıda açılıyor" in result.output
    assert calls == [
        {
            "host": "127.0.0.1",
            "port": 8765,
            "demo": True,
            "model": None,
            "effort": None,
            "anatomy_name": None,
            "ollama": False,
        }
    ]


def test_desktop_starts_a_real_server_and_opens_a_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    port = _free_port()
    opened: list[tuple[str, str]] = []
    statuses: list[int] = []

    def fake_start() -> None:
        # Pencere "açıkken" (pywebview'in kendi bloke eden döngüsü) sunucu hâlâ ayakta;
        # kapanınca `desktop()` should_exit ile durduracak.
        statuses.append(httpx.get(f"http://127.0.0.1:{port}/").status_code)

    fake = types.ModuleType("webview")
    fake.create_window = lambda title, url: opened.append((title, url))  # type: ignore[attr-defined]
    fake.start = fake_start  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", fake)

    result = CliRunner().invoke(
        cli.app, ["desktop", "--demo", "--port", str(port), "--title", "Test Penceresi"]
    )

    assert result.exit_code == 0, result.output
    assert opened == [("Test Penceresi", f"http://127.0.0.1:{port}")]
    assert statuses == [200]
