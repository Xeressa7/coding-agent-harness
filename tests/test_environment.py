"""Yerel ortam: komut gerçekten çalışır; zaman aşımı ve iptal süreç ağacını öldürür."""

import asyncio
from pathlib import Path
from time import perf_counter

import pytest

from harness.environments.local import LocalEnvironment, find_bash

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(find_bash() is None, reason="bash yok"),
]


async def test_runs_in_the_working_directory_with_utf8_output(tmp_path: Path) -> None:
    (tmp_path / "gemi.txt").write_text("x", "utf-8")
    env = LocalEnvironment(tmp_path)

    result = await env.run("ls; echo 'şığ' >&2; exit 3", timeout_seconds=30)

    assert result.stdout.strip() == "gemi.txt"
    assert result.stderr.strip() == "şığ"
    assert result.exit_code == 3 and not result.timed_out


async def test_python_child_prints_turkish_correctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kullanıcının terminalinde bu ayarlar olmayabilir; ortam onları kendisi vermeli.
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    (tmp_path / "yaz.py").write_text("print('dalga ğüşıöç')\n", "utf-8")

    result = await LocalEnvironment(tmp_path).run("python yaz.py", timeout_seconds=30)

    assert result.stdout.strip() == "dalga ğüşıöç", result.stderr


async def test_timeout_kills_the_whole_tree_and_keeps_partial_output(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    started = perf_counter()

    result = await env.run("echo başladı; sleep 2 && echo geç > out.txt", timeout_seconds=0.5)

    assert result.timed_out and result.exit_code is None
    assert "başladı" in result.stdout
    assert perf_counter() - started < 4  # torun süreç boruyu tutsaydı 5 sn beklerdik
    await asyncio.sleep(2.5)
    assert not (tmp_path / "out.txt").exists()  # sleep'ten sonraki adım hiç çalışmadı


async def test_cancelling_the_task_kills_the_command(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    task = asyncio.ensure_future(env.run("sleep 2 && echo geç > out.txt", timeout_seconds=30))
    await asyncio.sleep(0.5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.5)
    assert not (tmp_path / "out.txt").exists()


async def test_missing_bash_is_reported(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path, bash=tmp_path / "yok" / "bash.exe")

    with pytest.raises(FileNotFoundError):
        await env.run("echo x", timeout_seconds=5)
