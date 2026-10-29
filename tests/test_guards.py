"""Takılma uyarısı: ardışık özdeş araç çağrıları önce uyarılır, sonra durdurulur."""

from pathlib import Path

import pytest

from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import Event, HarnessIntervened, ToolFinished
from harness.core.guards import RepeatGuard
from harness.core.permissions import PermissionManager
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall

pytestmark = pytest.mark.anyio


def read(call_id: str, arguments: str = '{"path": "a.txt"}') -> AssistantMessage:
    return AssistantMessage(
        tool_calls=(ToolCall(id=call_id, name="read_file", arguments=arguments),)
    )


def test_counts_only_consecutive_identical_calls() -> None:
    guard = RepeatGuard(warn_after=3, block_after=5)
    same = ToolCall(id="1", name="read_file", arguments='{"path": "a", "limit": 5}')
    reordered = ToolCall(id="2", name="read_file", arguments='{"limit": 5, "path": "a"}')
    other = ToolCall(id="3", name="read_file", arguments='{"path": "b"}')

    assert guard.check(same) is None
    assert guard.check(reordered) is None  # argüman sırası farklı ama aynı çağrı
    warned = guard.check(same)
    assert warned is not None and (warned.action, warned.repeats) == ("warn", 3)
    assert guard.check(other) is None  # farklı çağrı sayacı sıfırlar
    for _ in range(4):
        guard.check(other)
    blocked = guard.check(other)
    assert blocked is not None and (blocked.action, blocked.repeats) == ("block", 6)


async def run(tmp_path: Path, anatomy_name: str) -> list[Event]:
    (tmp_path / "a.txt").write_text("aynı içerik", encoding="utf-8")
    anatomy = get_preset(anatomy_name)
    tools = anatomy.tool_registry(tmp_path)
    replies = [read(f"c{i}") for i in range(1, 6)] + [AssistantMessage(text="bitti")]
    agent = Agent(
        ScriptedLLM(replies),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=anatomy,
        root=tmp_path,
    )
    return [event async for event in agent.run("oku")]


async def test_agent_warns_on_the_third_and_blocks_the_fifth_repeat(tmp_path: Path) -> None:
    events = await run(tmp_path, "files+loop_guard")

    notes = [e for e in events if isinstance(e, HarnessIntervened)]
    assert [(n.layer, n.action, n.call_id) for n in notes] == [
        ("loop_guard", "warn", "c3"),
        ("loop_guard", "warn", "c4"),
        ("loop_guard", "block", "c5"),
    ]
    results = {e.call.id: e for e in events if isinstance(e, ToolFinished)}
    assert "[harness]" not in results["c2"].result.content
    output, note = results["c3"].result.content.split("[harness]")
    assert "aynı içerik" in output  # çağrı çalıştı, sonucun sonuna not eklendi
    assert "3. kez" in note and not results["c3"].result.is_error
    assert results["c5"].result.is_error and "çalıştırılmadı" in results["c5"].result.content
    assert results["c5"].outcome == "takılma: çalıştırılmadı"


async def test_without_the_layer_nothing_intervenes(tmp_path: Path) -> None:
    events = await run(tmp_path, "files")
    assert not any(isinstance(e, HarnessIntervened) for e in events)
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert len(finished) == 5 and not any(e.result.is_error for e in finished)


def test_layers_are_switched_on_and_off_after_the_preset_name() -> None:
    anatomy = get_preset("bash+loop_guard")
    assert anatomy.loop_guard and anatomy.name == "bash+loop_guard"
    assert "bash" in anatomy.tools
    assert not get_preset("bash+loop_guard-loop_guard").loop_guard
    assert get_preset("files") is get_preset("files")  # ek yoksa preset'in kendisi
    with pytest.raises(ValueError, match="katman yok"):
        get_preset("files+uydurma")
    with pytest.raises(ValueError, match="anatomi yok"):
        get_preset("uydurma+loop_guard")


async def test_guard_note_reaches_the_model_in_the_next_context(tmp_path: Path) -> None:
    # Not, araç sonucunun parçası olarak geçmişe girer; ayrı bir mesaj değildir.
    events = await run(tmp_path, "files+loop_guard")
    contexts = [e for e in events if e.kind == "context_assembled"]
    last = contexts[-1].messages
    assert sum("[harness]" in getattr(m, "content", "") for m in last) == 3
