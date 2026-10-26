"""Sohbet içi /komutlar: model/effort değişir ama geçmiş kalır; oturumlar listelenir ve sürer."""

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.cli.commands import ChatSession, is_command
from harness.core.anatomy import get_preset
from harness.core.config import project_sessions_dir
from harness.core.events import ModelReplied, TaskFinished, TaskStarted, ToolFinished
from harness.core.permissions import Decision
from harness.core.session_log import (
    SessionLogger,
    history_from_events,
    list_sessions,
    read_session,
)
from harness.llm.responses import Effort, ModelInfo
from harness.llm.scripted import ScriptedLLM
from harness.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)

MODELS = [
    ModelInfo(
        slug="luna",
        display_name="Luna",
        description="hızlı",
        default_effort="medium",
        efforts=[Effort(name="low", description="az"), Effort(name="medium", description="orta")],
    ),
    ModelInfo(
        slug="sol",
        display_name="Sol",
        description="güçlü",
        default_effort="high",
        efforts=[Effort(name="medium", description="orta"), Effort(name="high", description="çok")],
    ),
]


pytestmark = pytest.mark.anyio


class EchoModel:
    """Her soruya hangi modelle/effort'la çağrıldığını söyler; gördüğü geçmişi saklar."""

    def __init__(self, name: str, effort: str | None, seen: list[tuple[Message, ...]]) -> None:
        self.name, self.effort, self.seen = name, effort, seen

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        self.seen.append(tuple(history))
        return AssistantMessage(text=f"{self.name}/{self.effort}")


def make_session(tmp_path: Path) -> tuple[ChatSession, list[tuple[str, str | None]]]:
    built: list[tuple[str, str | None]] = []
    seen: list[tuple[Message, ...]] = []

    def make_llm(name: str, effort: str | None) -> EchoModel:
        built.append((name, effort))
        return EchoModel(name, effort, seen)

    async def load_models() -> list[ModelInfo]:
        return MODELS

    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        system_prompt="talimat",
        model="luna",
        effort=None,
        make_llm=make_llm,
        load_models=load_models,
        sessions_dir=tmp_path / "logs",
    )
    return session, built


async def turn(session: ChatSession, question: str) -> str:
    """Ön yüzün yaptığı gibi: olaylar oturumun o anki kaydına yazılır."""
    text = ""
    async for event in session.agent.run(question):
        session.log.write(event)
        if isinstance(event, ModelReplied):
            text = event.text
    return text


def test_project_sessions_dir_is_readable_and_separates_same_named_projects(
    tmp_path: Path,
) -> None:
    first = project_sessions_dir(tmp_path / "a" / "proje", home=tmp_path / "home")
    second = project_sessions_dir(tmp_path / "b" / "proje", home=tmp_path / "home")
    assert first != second
    assert first.parent == tmp_path / "home"
    assert first.name.endswith("a-proje")


def test_is_command() -> None:
    assert is_command("/model") and not is_command("model nedir?")


async def test_model_switch_keeps_history_and_next_turn_records_new_model(tmp_path: Path) -> None:
    session, built = make_session(tmp_path)
    assert await turn(session, "ilk") == "luna/None"

    listing = await session.run("/model")
    assert "● 1" in listing.text and "Sol" in listing.text

    await session.run("/effort low")
    assert session.effort == "low"
    # Sol'da "low" yok: model değişince effort modelin varsayılanına döner.
    reply = await session.run("/model 2")
    assert "sol" in reply.text and session.model == "sol" and session.effort is None
    assert await turn(session, "ikinci") == "sol/None"
    # Yeni model eski konuşmayı görür.
    users = [m.text for m in session.agent.history if isinstance(m, UserMessage)]
    assert users == ["ilk", "ikinci"]

    assert "high" in (await session.run("/effort")).text
    await session.run("/effort high")
    assert session.label == "sol · effort: high"
    assert built[-1] == ("sol", "high")
    started = [e for e in read_session(session.log.path) if isinstance(e, TaskStarted)]
    assert [(e.model, e.effort) for e in started] == [("luna", None), ("sol", None)]


async def test_bad_choices_change_nothing(tmp_path: Path) -> None:
    session, built = make_session(tmp_path)
    assert "yok" in (await session.run("/model gpt-99")).text
    assert "yok" in (await session.run("/effort high")).text  # luna'da high yok
    assert "yok" in (await session.run("/model 9")).text
    assert "Bilinmeyen" in (await session.run("/uçur")).text
    assert session.label == "luna · effort: varsayılan" and len(built) == 1


async def test_model_list_failure_is_reported_not_raised(tmp_path: Path) -> None:
    async def offline() -> list[ModelInfo]:
        raise RuntimeError("ağ yok")

    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        system_prompt="talimat",
        model="luna",
        effort=None,
        make_llm=lambda name, effort: ScriptedLLM([]),
        load_models=offline,
        sessions_dir=tmp_path / "logs",
    )
    assert "ağ yok" in (await session.run("/model")).text


async def test_new_starts_empty_history_in_a_new_log(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    await turn(session, "ilk")
    old_log = session.log.path
    reply = await session.run("/new")
    assert reply.reset and not reply.transcript
    assert session.log.path != old_log
    assert len(session.agent.history) == 1  # yalnız talimat


async def test_session_grants_survive_model_switch_but_not_a_new_or_resumed_session(
    tmp_path: Path,
) -> None:
    session, built = make_session(tmp_path)
    write = ToolCall(id="c1", name="write_file", arguments='{"path": "a.txt", "content": "x"}')
    await turn(session, "merhaba")  # /resume'un bulacağı bir kayıt olsun
    assert session.agent.permissions.decide(write) is Decision.ASK

    session.agent.permissions.grant("write_file")
    await session.run("/model sol")
    assert built[-1] == ("sol", None)  # ajan gerçekten yeniden kuruldu
    assert session.agent.permissions.decide(write) is Decision.ALLOW

    await session.run("/new")
    assert session.agent.permissions.decide(write) is Decision.ASK

    session.agent.permissions.grant("write_file")
    reply = await session.run("/resume 1")
    assert reply.reset
    assert session.agent.permissions.decide(write) is Decision.ASK


async def test_resume_continues_an_old_session_in_its_own_log(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    await turn(session, "eski görev")
    old_log = session.log.path
    await session.run("/new")
    await asyncio.sleep(0.01)  # dosya zamanları sıralansın
    await turn(session, "yeni görev")
    os.utime(old_log, (1, 1))  # eski oturum gerçekten daha eski

    table = (await session.run("/sessions")).text
    assert table.index("yeni görev") < table.index("eski görev")
    assert "(bu oturum)" in table

    reply = await session.run("/resume 2")
    assert reply.reset and reply.transcript == (("Sen", "eski görev"), ("Ajan", "luna/None"))
    assert session.log.path == old_log
    await turn(session, "devam")
    users = [m.text for m in session.agent.history if isinstance(m, UserMessage)]
    assert users == ["eski görev", "devam"]
    tasks = [e.task for e in read_session(old_log) if isinstance(e, TaskStarted)]
    assert tasks == ["eski görev", "devam"]
    assert "yok" in (await session.run("/resume 7")).text


def test_history_from_events_drops_the_unfinished_tool_step() -> None:
    done = ToolCall(id="c1", name="read_file", arguments="{}")
    cut = ToolCall(id="c2", name="read_file", arguments="{}")
    other = ToolCall(id="c3", name="read_file", arguments="{}")
    result = ToolResult(tool_call_id="c1", content="içerik")
    events = [
        TaskStarted(task="oku"),
        ModelReplied(step=1, text="", tool_calls=(done,)),
        ToolFinished(call=done, result=result),
        ModelReplied(step=2, text="okudum"),
        TaskFinished(reason="done", steps=2),
        TaskStarted(task="iki dosya oku"),
        ModelReplied(step=1, text="", tool_calls=(cut, other)),
        ToolFinished(call=cut, result=ToolResult(tool_call_id="c2", content="yarım")),
        TaskFinished(reason="cancelled", steps=1),
    ]
    history = history_from_events(events)
    assert history == [
        UserMessage(text="oku"),
        AssistantMessage(tool_calls=(done,)),
        result,
        AssistantMessage(text="okudum"),
        UserMessage(text="iki dosya oku"),
    ]


def test_list_sessions_skips_logs_without_tasks(tmp_path: Path) -> None:
    log = SessionLogger(tmp_path / "a.jsonl")
    log.write(TaskStarted(task="birinci"))
    log.write(TaskStarted(task="ikinci"))
    (tmp_path / "bos.jsonl").write_text("", encoding="utf-8")
    (summary,) = list_sessions(tmp_path)
    assert summary.turns == 2 and summary.first_task == "birinci"


def test_plain_chat_model_command_switches_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    used: list[str] = []

    def factory(tokens: object, model: str, http: object, effort: str | None) -> ScriptedLLM:
        used.append(model)
        return ScriptedLLM([AssistantMessage(text=f"cevap-{model}")])

    async def models(tokens: object, http: object) -> list[ModelInfo]:
        return MODELS

    monkeypatch.setattr(cli, "ResponsesLLM", factory)
    monkeypatch.setattr(cli, "list_models", models)
    result = CliRunner().invoke(
        cli.app,
        ["chat", "--model", "luna"],
        input="/help\n/model sol\nselam\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert "/resume" in result.output  # yardım tablosu
    assert "cevap-sol" in result.output
    assert used == ["luna", "sol"]
