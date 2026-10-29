"""Sohbet içi /komutlar: model/effort değişir ama geçmiş kalır; oturumlar listelenir ve sürer."""

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.cli.commands import ChatSession, is_command, usage_text
from harness.core.anatomy import get_preset
from harness.core.config import project_sessions_dir
from harness.core.events import (
    HarnessIntervened,
    ModelReplied,
    TaskFinished,
    TaskStarted,
    ToolFinished,
)
from harness.core.permissions import Decision
from harness.core.session_log import (
    SessionLogger,
    history_from_events,
    list_sessions,
    read_session,
)
from harness.llm.base import Effort, ModelInfo
from harness.llm.providers import Provider, get_provider
from harness.llm.scripted import ScriptedLLM
from harness.messages import (
    AssistantMessage,
    Message,
    TokenUsage,
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

    def make_llm(provider: Provider, name: str, effort: str | None) -> EchoModel:
        built.append((name, effort))
        return EchoModel(name, effort, seen)

    async def load_models(provider: Provider) -> list[ModelInfo]:
        return MODELS

    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        provider=get_provider("chatgpt"),
        model="luna",
        effort=None,
        make_llm=make_llm,
        load_models=load_models,
        load_prompt=lambda anatomy: f"talimat:{anatomy.name}",
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

    listing = (await session.run("/model")).picker
    assert listing is not None
    current = [c.value for c in listing.choices if c.current]
    assert current == ["chatgpt/luna"] and "chatgpt/sol" in [c.value for c in listing.choices]

    await session.run("/effort low")
    assert session.effort == "low"
    # Sol'da "low" yok: model değişince effort modelin varsayılanına döner.
    reply = await session.run("/model sol")
    assert "sol" in reply.text and session.model == "sol" and session.effort is None
    assert reply.picker is None  # adı yazılarak seçilen model effort sormaz
    assert await turn(session, "ikinci") == "sol/None"
    # Yeni model eski konuşmayı görür.
    users = [m.text for m in session.agent.history if isinstance(m, UserMessage)]
    assert users == ["ilk", "ikinci"]

    efforts = (await session.run("/effort")).picker
    assert efforts is not None and "high" in [c.value for c in efforts.choices]
    await session.run("/effort high")
    assert session.label == "sol · effort: high · chatgpt"
    assert built[-1] == ("sol", "high")
    started = [e for e in read_session(session.log.path) if isinstance(e, TaskStarted)]
    assert [(e.model, e.effort) for e in started] == [("luna", None), ("sol", None)]


async def test_bad_choices_change_nothing(tmp_path: Path) -> None:
    session, built = make_session(tmp_path)
    assert "yok" in (await session.run("/model gpt-99")).text
    assert "yok" in (await session.run("/effort high")).text  # luna'da high yok
    assert "yok" in (await session.run("/model 9")).text
    assert "Bilinmeyen" in (await session.run("/uçur")).text
    assert session.label == "luna · effort: varsayılan · chatgpt" and len(built) == 1


async def test_model_list_failure_is_reported_not_raised(tmp_path: Path) -> None:
    async def offline(provider: Provider) -> list[ModelInfo]:
        raise RuntimeError("ağ yok")

    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        provider=get_provider("chatgpt"),
        model="luna",
        effort=None,
        make_llm=lambda provider, name, effort: ScriptedLLM([]),
        load_models=offline,
        load_prompt=lambda anatomy: "talimat",
        sessions_dir=tmp_path / "logs",
    )
    assert "ağ yok" in (await session.run("/model sol")).text
    assert "ağ yok" in (await session.run("/effort")).text
    picker = (await session.run("/model")).picker
    assert picker is not None and "ağ yok" in picker.choices[0].detail


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

    listing = (await session.run("/sessions")).picker  # takma ad: /resume
    assert listing is not None and listing.kind == "resume"
    details = [c.detail for c in listing.choices]
    assert "yeni görev" in details[0] and "eski görev" in details[1]
    assert [c.current for c in listing.choices] == [True, False]

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

    def factory(provider: object, model: str, effort: str | None, http: object) -> ScriptedLLM:
        used.append(model)
        return ScriptedLLM([AssistantMessage(text=f"cevap-{model}")])

    async def models(provider: object, http: object) -> list[ModelInfo]:
        return MODELS

    monkeypatch.setattr(cli, "make_llm", factory)
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


async def test_anatomy_switch_changes_tools_prompt_and_resets_grants(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    session.permissions.grant("write_file")

    reply = await session.run("/anatomy raw")

    assert "raw" in reply.text and session.tools.specs() == []
    assert session.agent.history[0].text == "talimat:raw"  # type: ignore[union-attr]
    assert "araç yok" in (await session.run("/tools")).text
    await session.run("/anatomy bash")
    assert "bash" in [s.name for s in session.tools.specs()]
    assert (
        session.permissions.decide(
            ToolCall(id="w", name="write_file", arguments='{"path": "a", "content": ""}')
        )
        is Decision.ASK
    )
    assert "yok" in (await session.run("/anatomy uçuş")).text


async def test_anatomy_picker_toggles_layers_and_keeps_them_across_presets(
    tmp_path: Path,
) -> None:
    session, _ = make_session(tmp_path)
    picker = (await session.run("/anatomy")).picker
    assert picker is not None
    toggle = next(c for c in picker.choices if c.label.startswith("loop_guard"))
    assert toggle.label.endswith("kapalı") and toggle.value == "files+loop_guard"

    await session.choose("anatomy", toggle.value)
    assert session.anatomy.loop_guard and session.anatomy.name == "files+loop_guard"
    picker = (await session.run("/anatomy")).picker
    assert picker is not None
    bash = next(c for c in picker.choices if c.label == "bash")
    assert bash.value == "bash+loop_guard"  # paket değişince açık katman korunur
    off = next(c for c in picker.choices if c.label.startswith("loop_guard"))
    assert off.current and off.value == "files"

    assert "katman yok" in (await session.run("/anatomy files+uydurma")).text


async def test_tools_status_and_usage_describe_the_session(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    assert "henüz tur yok" in (await session.run("/usage")).text

    tools = (await session.run("/tools")).text
    assert "| `read_file` |" in tools and "sormadan" in tools and "sorar" in tools
    await turn(session, "selam")
    status = (await session.run("/status")).text
    assert "chatgpt" in status and "`luna`" in status and str(tmp_path) in status
    usage = (await session.run("/cost")).text  # takma ad
    assert "Tur / model çağrısı | 1 / 1" in usage
    assert "Girdi tokenı | —" in usage  # sahte model sayım vermez; 0 yazılmaz


async def test_aliases_and_effort_back_to_default(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    await session.run("/effort low")
    assert session.effort == "low"
    await session.run("/effort varsayılan")
    assert session.effort is None
    assert (await session.run("/quit")).exit
    cleared = await session.run("/clear")
    assert cleared.reset


async def test_resume_latest_without_sessions_is_a_fresh_start(tmp_path: Path) -> None:
    session, _ = make_session(tmp_path)
    reply = session.resume_latest()
    assert not reply.reset and "yeni oturum" in reply.text


def test_usage_shows_the_cache_share_and_harness_interventions() -> None:
    def reply(step: int, cached: int | None) -> ModelReplied:
        usage = TokenUsage(input_tokens=1000, output_tokens=10, cached_tokens=cached)
        return ModelReplied(step=step, text="", usage=usage)

    guard = HarnessIntervened(step=2, layer="loop_guard", action="warn", summary="read_file 3. kez")
    text = usage_text([TaskStarted(task="t"), reply(1, 0), reply(2, 750), guard])
    assert "| önbellekten | 750 (%38) |" in text.replace("  ", "")
    assert "| Harness müdahalesi | 1 (loop_guard ×1) |" in text
    # Bir çağrı sayım vermediyse yüzde uydurulmaz.
    partial = usage_text([TaskStarted(task="t"), reply(1, None), reply(2, 750)])
    assert "en az 750 |" in partial and "%" not in partial
