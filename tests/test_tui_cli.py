"""Tam ekran yalnız etkileşimli terminalde; sade akış açıkça seçilebilir."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.cli import tui
from harness.core.session_log import SessionLogger
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
    calls: list[str] = []

    def launch(root: Path, model: str, effort: str | None, prompt: str, log: SessionLogger) -> None:
        calls.append(model)
        assert root == tmp_path and prompt

    monkeypatch.setattr(tui, "run_tui", launch)
    result = CliRunner().invoke(cli.app, ["chat", "--model", "test-model"])
    assert result.exit_code == 0, result.output
    assert calls == ["test-model"]


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

    monkeypatch.setattr(cli, "ResponsesLLM", factory)
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
