"""Tam ekran yalnız etkileşimli terminalde; sade akış açıkça seçilebilir."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.cli import tui
from harness.cli.commands import ChatSession
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage


def test_interactive_chat_routes_to_tui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True),
        ),
    )
    calls: list[tuple[str, str, bool]] = []

    def launch(make_session: Callable[[object], ChatSession], *, resume: bool = False) -> Path:
        session = make_session(object())
        calls.append((session.model, session.anatomy.name, resume))
        assert session.root == tmp_path and session.system_prompt
        return session.log.path

    monkeypatch.setattr(tui, "run_tui", launch)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["chat", "--model", "test-model", "--anatomy", "raw"])
    assert result.exit_code == 0, result.output
    # Standart biçim: komutsuz `harness` sohbet açar, -c son oturuma devam eder.
    result = runner.invoke(cli.app, ["-m", "m2", "-c"])
    assert result.exit_code == 0, result.output
    assert calls == [("test-model", "raw", False), ("m2", "files", True)]


def test_plain_bypasses_fullscreen_even_in_interactive_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True),
            stdout=SimpleNamespace(isatty=lambda: True),
        ),
    )
    model = ScriptedLLM([AssistantMessage(text="Sade yanıt")])

    def factory(*args: object) -> ScriptedLLM:
        return model

    def forbidden(*args: object) -> None:
        raise AssertionError("--plain must not open fullscreen")

    monkeypatch.setattr(cli, "make_llm", factory)
    monkeypatch.setattr(tui, "run_tui", forbidden)
    result = CliRunner().invoke(cli.app, ["chat", "--plain"], input="selam\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Sade yanıt" in result.output
    assert len(model.received) == 1


def test_demo_tui_is_explicitly_scripted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    apps: list[tui.HarnessApp] = []

    def run(app: tui.HarnessApp, **kwargs: object) -> None:
        apps.append(app)

    monkeypatch.setattr(tui.HarnessApp, "run", run)
    result = CliRunner().invoke(cli.app, ["demo", "--tui"])
    assert result.exit_code == 0, result.output
    assert len(apps) == 1 and apps[0].initial_task == cli.DEMO_TASK
    assert "Sahte" in apps[0].model_label
