"""Terminalde ayrıntılar akmaz; aynı Agent iki turu hatırlar, kayıt ve HTML saklanır."""

from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.core.events import ModelReplied, TaskFinished, TaskStarted, ToolFinished
from harness.core.session_log import read_session
from harness.llm.responses import ResponsesError
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, Message, ToolCall, ToolSpec, UserMessage


def install_model(monkeypatch: pytest.MonkeyPatch, model: object) -> None:
    def factory(*args: object) -> object:
        return model

    monkeypatch.setattr(cli, "ResponsesLLM", factory)


def test_chat_preserves_history_and_hides_tool_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("TOOL_RESULT_SECRET", encoding="utf-8")
    call = ToolCall(id="c1", name="read_file", arguments='{"path":"a.txt"}')
    model = ScriptedLLM(
        [
            AssistantMessage(text="INTERMEDIATE_EXPLANATION", tool_calls=(call,)),
            AssistantMessage(text="İlk cevap"),
            AssistantMessage(text="İkinci cevap"),
        ]
    )
    install_model(monkeypatch, model)
    opened: list[str] = []
    monkeypatch.setattr(cli.typer, "launch", opened.append)
    result = CliRunner().invoke(
        cli.app,
        ["chat", "--model", "test-model"],
        input="\n/inspect\noku\nöncekini hatırla\n/inspect\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert "İlk cevap" in result.output and "İkinci cevap" in result.output
    # Renk olmasa da rol başlıkları ve sıraları konuşmayı ayırır.
    assert result.output.count("Ajan") == 2
    assert "\nAjan " in result.output
    assert "> Ajan" not in result.output
    assert (
        result.output.index("Sen") < result.output.index("Ajan") < result.output.index("İlk cevap")
    )
    assert "TOOL_RESULT_SECRET" not in result.output
    assert "INTERMEDIATE_EXPLANATION" not in result.output
    assert "Henüz rapor yok" in result.output
    assert len(opened) == 1 and opened[0].startswith("file:")
    assert [m.text for m in model.received[-1] if isinstance(m, UserMessage)] == [
        "oku",
        "öncekini hatırla",
    ]
    (source,) = (tmp_path / "sessions").glob("*.jsonl")
    events = read_session(source)
    assert len([e for e in events if isinstance(e, TaskStarted)]) == 2
    assert len([e for e in events if isinstance(e, TaskFinished)]) == 2
    assert len([e for e in events if isinstance(e, ToolFinished)]) == 1
    reports = sorted((tmp_path / "sessions").glob("*.html"))
    assert len(reports) == 2
    assert any("Tur 2" in p.read_text(encoding="utf-8") for p in reports)
    assert all("TOOL_RESULT_SECRET" in p.read_text(encoding="utf-8") for p in reports)


def test_eof_without_prompt_does_not_call_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    model = ScriptedLLM([])
    install_model(monkeypatch, model)
    result = CliRunner().invoke(cli.app, ["chat"], input="")
    assert result.exit_code == 0
    assert not model.received
    assert not list((tmp_path / "sessions").glob("*.jsonl"))


class FailingModel:
    async def complete(
        self,
        history: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
    ) -> AssistantMessage:
        raise ResponsesError("MODEL_FAILED")


def test_ask_model_failure_is_recorded_and_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    install_model(monkeypatch, FailingModel())
    result = CliRunner().invoke(cli.app, ["ask", "soru"])
    assert result.exit_code == 1
    assert "MODEL_FAILED" in result.output
    (source,) = (tmp_path / "sessions").glob("*.jsonl")
    events = read_session(source)
    assert isinstance(events[-1], TaskFinished) and events[-1].reason == "error"
    assert not any(isinstance(e, ModelReplied) for e in events)
    (report,) = (tmp_path / "sessions").glob("*.html")
    assert "MODEL_FAILED" in report.read_text(encoding="utf-8")


def test_ask_success_is_quiet_and_inspect_accepts_existing_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    install_model(monkeypatch, ScriptedLLM([AssistantMessage(text="Yanıt")]))
    runner = CliRunner()
    result = runner.invoke(cli.app, ["ask", "soru", "--model", "test-model"])
    assert result.exit_code == 0, result.output
    assert "Yanıt" in result.output
    assert result.output.index("Sen") < result.output.index("soru")
    assert result.output.index("soru") < result.output.index("Ajan") < result.output.index("Yanıt")
    assert result.output.count("Yanıt") == 1
    (source,) = (tmp_path / "sessions").glob("*.jsonl")
    target = tmp_path / "report.html"
    result = runner.invoke(cli.app, ["inspect", str(source), "--output", str(target)])
    assert result.exit_code == 0 and target.is_file()
    result = runner.invoke(cli.app, ["inspect", str(source), "--output", str(source)])
    assert result.exit_code == 1
    assert read_session(source)
