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

    monkeypatch.setattr(cli, "make_llm", factory)


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
    # Kayıt projeye değil harness'ın kendi klasörüne yazılır.
    assert not (tmp_path / "sessions").exists()
    (source,) = (tmp_path / "harness-sessions").rglob("*.jsonl")
    events = read_session(source)
    assert len([e for e in events if isinstance(e, TaskStarted)]) == 2
    assert len([e for e in events if isinstance(e, TaskFinished)]) == 2
    assert len([e for e in events if isinstance(e, ToolFinished)]) == 1
    reports = sorted((tmp_path / "harness-sessions").rglob("*.html"))
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
    assert not list((tmp_path / "harness-sessions").rglob("*.jsonl"))


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
    (source,) = (tmp_path / "harness-sessions").rglob("*.jsonl")
    events = read_session(source)
    assert isinstance(events[-1], TaskFinished) and events[-1].reason == "error"
    assert not any(isinstance(e, ModelReplied) for e in events)
    (report,) = (tmp_path / "harness-sessions").rglob("*.html")
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
    (source,) = (tmp_path / "harness-sessions").rglob("*.jsonl")
    target = tmp_path / "report.html"
    result = runner.invoke(cli.app, ["inspect", str(source), "--output", str(target)])
    assert result.exit_code == 0 and target.is_file()
    result = runner.invoke(cli.app, ["inspect", str(source), "--output", str(source)])
    assert result.exit_code == 1
    assert read_session(source)


def test_print_mode_writes_only_the_answer_to_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("GIZLI_ICERIK", encoding="utf-8")
    call = ToolCall(id="c1", name="read_file", arguments='{"path":"a.txt"}')
    model = ScriptedLLM(
        [AssistantMessage(text="ARA_ACIKLAMA", tool_calls=(call,)), AssistantMessage(text="42")]
    )
    install_model(monkeypatch, model)

    result = CliRunner().invoke(cli.app, ["-p", "kaç?"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "42"
    assert "GIZLI_ICERIK" not in result.output and "ARA_ACIKLAMA" not in result.output
    # Kayıt ve rapor yine yazılır; inceleme sonradan yapılabilir.
    assert list((tmp_path / "harness-sessions").rglob("*.html"))


def test_print_mode_denies_tools_that_need_permission_unless_allowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    def write_then_answer() -> ScriptedLLM:
        call = ToolCall(id="w", name="write_file", arguments='{"path":"b.txt","content":"x"}')
        return ScriptedLLM([AssistantMessage(tool_calls=(call,)), AssistantMessage(text="bitti")])

    install_model(monkeypatch, write_then_answer())
    denied = CliRunner().invoke(cli.app, ["-p", "yaz"])
    assert denied.exit_code == 0, denied.output
    assert not (tmp_path / "b.txt").exists()
    assert "--allow write_file" in denied.stderr

    install_model(monkeypatch, write_then_answer())
    allowed = CliRunner().invoke(cli.app, ["-p", "yaz", "--allow", "write_file"])
    assert allowed.exit_code == 0, allowed.output
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "x"

    install_model(monkeypatch, write_then_answer())
    unknown = CliRunner().invoke(cli.app, ["-p", "yaz", "--allow", "uçur"])
    assert unknown.exit_code != 0


def test_print_mode_failure_exits_nonzero_with_the_error_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    install_model(monkeypatch, FailingModel())

    result = CliRunner().invoke(cli.app, ["-p", "soru"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "MODEL_FAILED" in result.stderr


def test_continue_flag_carries_the_last_sessions_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    install_model(monkeypatch, ScriptedLLM([AssistantMessage(text="Adın Ada.")]))
    assert CliRunner().invoke(cli.app, ["-p", "Benim adım Ada."]).exit_code == 0

    model = ScriptedLLM([AssistantMessage(text="Ada")])
    install_model(monkeypatch, model)
    result = CliRunner().invoke(cli.app, ["-c", "-p", "Adım ne?"])

    assert result.exit_code == 0, result.output
    users = [m.text for m in model.received[0] if isinstance(m, UserMessage)]
    assert users == ["Benim adım Ada.", "Adım ne?"]
    # Devam edilen tur aynı kayda eklenir.
    (log,) = (tmp_path / "harness-sessions").rglob("*.jsonl")
    assert sum(isinstance(e, TaskStarted) for e in read_session(log)) == 2
