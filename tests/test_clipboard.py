"""Sistem panosu: terminal OSC 52'yi yok saysa da kopyala/yapıştır çalışsın."""

import subprocess
from typing import Any

import pytest

from harness.cli import clipboard


class FakeRun:
    """subprocess.run yerine: hangi komutların çağrıldığını kaydeder, cevabı sahtedir."""

    def __init__(self, stdout: bytes = b"", fail: set[str] | None = None) -> None:
        self.stdout = stdout
        self.fail = fail or set()
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(command)
        if command[0] in self.fail:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout)


def found(name: str) -> str | None:
    return f"/usr/bin/{name}"


def missing(name: str) -> str | None:
    return None


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", found)


def test_wayland_prefers_wl_copy_and_falls_back_to_xclip(
    linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    run = FakeRun(fail={"wl-copy"})
    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.copy_text("merhaba")
    assert [c[0] for c in run.calls] == ["wl-copy", "xclip"]


def test_x11_skips_wayland_tools(linux: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    run = FakeRun(stdout=b"satir\r\nikinci")
    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.paste_text() == "satir\nikinci"
    assert run.calls[0][:3] == ["xclip", "-selection", "clipboard"]


def test_no_tool_means_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", missing)
    assert not clipboard.copy_text("x")
    assert clipboard.paste_text() is None and clipboard.image_type() is None


def test_image_type_reads_the_offered_targets(linux: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(
        clipboard.subprocess, "run", FakeRun(stdout=b"TARGETS\nimage/png\nUTF8_STRING\n")
    )
    assert clipboard.image_type() == "image/png"
