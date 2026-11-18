"""ask_user: soru ön yüze olay olarak gider, cevap sonuca girer; cevapsızsa model varsayım yazar."""

import json
from pathlib import Path

import pytest

from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import Event, HarnessIntervened, QuestionAsked, ToolFinished
from harness.core.permissions import PermissionManager
from harness.core.session_log import history_from_events
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall, ToolResult
from harness.tools.ask_user import NO_ANSWER, pick_answer

pytestmark = pytest.mark.anyio


def ask(question: str, *options: str) -> ToolCall:
    arguments = json.dumps({"question": question, "options": list(options)})
    return ToolCall(id="q1", name="ask_user", arguments=arguments)


def agent_for(tmp_path: Path, anatomy_name: str, replies: list[AssistantMessage]) -> Agent:
    anatomy = get_preset(anatomy_name)
    tools = anatomy.tool_registry(tmp_path)
    return Agent(
        ScriptedLLM(replies),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=anatomy,
        root=tmp_path,
    )


def test_numbers_pick_options_and_blank_means_no_answer() -> None:
    options = ("gizle", "00:00 göster")
    assert pick_answer(" 2 ", options) == "00:00 göster"
    assert pick_answer("3", options) == "3"  # seçenek yoksa metin olduğu gibi
    assert pick_answer("sayaç hiç görünmesin", options) == "sayaç hiç görünmesin"
    assert pick_answer("  ", options) is None


async def test_the_answer_comes_back_through_asend(tmp_path: Path) -> None:
    agent = agent_for(
        tmp_path,
        "bash",
        [
            AssistantMessage(tool_calls=(ask("Süre yokken sayaç gizlensin mi?", "evet", "hayır"),)),
            AssistantMessage(text="tamam"),
        ],
    )
    events: list[Event] = []
    stream = agent.run("sayacı düzelt")
    send: bool | str | None = None
    while True:
        try:
            event = await stream.asend(send)
        except StopAsyncIteration:
            break
        events.append(event)
        send = "evet" if isinstance(event, QuestionAsked) else None

    [asked] = [e for e in events if isinstance(e, QuestionAsked)]
    assert (asked.question, asked.options, asked.call_id) == (
        "Süre yokken sayaç gizlensin mi?",
        ("evet", "hayır"),
        "q1",
    )
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result.content == "Kullanıcının cevabı: evet"
    assert finished.details["answered"] is True and finished.category == "plan"
    history = history_from_events(events)  # devam edilen oturumda cevap geçmişte
    assert any(isinstance(m, ToolResult) and m.content.endswith("evet") for m in history)


async def test_without_an_answer_the_model_is_asked_to_assume_and_say_so(
    tmp_path: Path,
) -> None:
    agent = agent_for(
        tmp_path,
        "bash+finish_check",
        [
            AssistantMessage(tool_calls=(ask("Hangisi?"),)),
            AssistantMessage(text="Yaptım."),
            AssistantMessage(text="Varsayım: sayaç gizlenir."),
        ],
    )
    events = [event async for event in agent.run("sayacı düzelt")]  # -p gibi: cevap yok

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result.content == NO_ANSWER and not finished.result.is_error
    [reminder] = [e for e in events if isinstance(e, HarnessIntervened)]
    assert reminder.layer == "finish_check" and '"Varsayım: …" satırı yok' in reminder.summary


async def test_the_tool_is_absent_when_the_layer_is_off(tmp_path: Path) -> None:
    on = [spec.name for spec in get_preset("bash").tool_registry(tmp_path).specs()]
    off = [spec.name for spec in get_preset("bash-ask_user").tool_registry(tmp_path).specs()]
    files = [spec.name for spec in get_preset("files").tool_registry(tmp_path).specs()]

    assert "ask_user" in on and "ask_user" not in off and "ask_user" not in files
