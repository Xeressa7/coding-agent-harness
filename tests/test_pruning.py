"""Bağlam budama: eşik aşınca eski araç sonuçları bir kerede nota döner, histerezisle seyrek."""

from pathlib import Path

import pytest

from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import ContextAssembled, Event, HarnessIntervened
from harness.core.permissions import PermissionManager
from harness.core.pruning import STUB_PREFIX, ContextPruner, prune
from harness.llm.scripted import ScriptedLLM
from harness.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    TokenUsage,
    ToolCall,
    ToolResult,
    UserMessage,
)

pytestmark = pytest.mark.anyio
BIG = "x" * 2_000


def conversation(steps: int, size: int = 2_000) -> list[Message]:
    history: list[Message] = [SystemMessage(text="talimat"), UserMessage(text="görev")]
    for i in range(1, steps + 1):
        call = ToolCall(id=f"c{i}", name="read_file", arguments=f'{{"path": "f{i}.py"}}')
        history += [
            AssistantMessage(tool_calls=(call,)),
            ToolResult(tool_call_id=f"c{i}", content="y" * size),
        ]
    return history


def contents(history: list[Message]) -> list[str]:
    return [m.content for m in history if isinstance(m, ToolResult)]


def test_prune_keeps_the_last_steps_and_stubs_the_rest() -> None:
    history = conversation(8)
    pruned, count, removed = prune(history, keep_steps=5)

    assert count == 3
    results = contents(pruned)
    assert all(r.startswith(STUB_PREFIX) for r in results[:3])
    assert results[3:] == ["y" * 2_000] * 5
    assert removed == sum(2_000 - len(r) for r in results[:3])
    assert [type(m) for m in pruned] == [type(m) for m in history]  # çağrılar ve sıra yerinde
    assert contents(history)[0] == "y" * 2_000  # girdi listesi değişmez


def test_prune_is_idempotent_and_skips_short_results() -> None:
    once, first, _ = prune(conversation(8), keep_steps=5)
    twice, second, _ = prune(once, keep_steps=5)
    assert first == 3 and second == 0 and twice == once

    short = conversation(8, size=20)
    assert prune(short, keep_steps=5)[1] == 0  # not sonuçtan uzun olurdu


def test_pruner_waits_for_the_threshold() -> None:
    pruner = ContextPruner(threshold=60_000, keep_steps=5, regrow=20_000)
    assert pruner.check(conversation(8), input_tokens=59_999) is None
    checked = pruner.check(conversation(8), input_tokens=60_000)
    assert checked is not None
    _, report = checked
    assert (report.results, report.tokens_before) == (3, 60_000)
    assert report.tokens_after == 60_000 - round(report.chars / 3.5)
    assert "3 eski araç sonucu çıkarıldı" in report.summary()


def test_hysteresis_prevents_pruning_on_every_step() -> None:
    pruner = ContextPruner(threshold=60_000, keep_steps=5, regrow=20_000)
    history = conversation(8)
    checked = pruner.check(history, input_tokens=70_000)
    assert checked is not None
    history, report = checked
    history = [*history, *conversation(1)[2:]]  # bir adım daha; bağlam eşiğin üstünde kalıyor

    assert pruner.check(history, input_tokens=report.tokens_after + 19_999) is None
    again = pruner.check(history, input_tokens=report.tokens_after + 20_000)
    assert again is not None and again[1].results == 1


def test_nothing_to_prune_rearms_later() -> None:
    pruner = ContextPruner(threshold=60_000, keep_steps=5, regrow=20_000)
    few = conversation(3)  # hepsi korunan son adımlar
    assert pruner.check(few, input_tokens=65_000) is None
    assert pruner.check(conversation(8), input_tokens=80_000) is None  # 65k + 20k'ya kadar bekler
    assert pruner.check(conversation(8), input_tokens=85_000) is not None


def test_estimates_tokens_when_the_provider_reports_none() -> None:
    pruner = ContextPruner(threshold=1_000, keep_steps=5, regrow=20_000)
    checked = pruner.check(conversation(8), input_tokens=None)  # ~16k karakter ≈ 4,6k token
    assert checked is not None and checked[1].tokens_before > 1_000


async def run_agent(tmp_path: Path, anatomy_name: str) -> list[Event]:
    for i in range(1, 9):
        (tmp_path / f"f{i}.txt").write_text(BIG, encoding="utf-8")
    anatomy = get_preset(anatomy_name)
    tools = anatomy.tool_registry(tmp_path)
    replies = [
        AssistantMessage(
            tool_calls=(
                ToolCall(id=f"c{i}", name="read_file", arguments=f'{{"path": "f{i}.txt"}}'),
            ),
            usage=TokenUsage(input_tokens=10_000 * i),
        )
        for i in range(1, 9)
    ] + [AssistantMessage(text="bitti", usage=TokenUsage(input_tokens=90_000))]
    agent = Agent(
        ScriptedLLM(replies),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=anatomy,
        root=tmp_path,
    )
    return [event async for event in agent.run("oku")]


async def test_agent_prunes_once_past_the_threshold(tmp_path: Path) -> None:
    events = await run_agent(tmp_path, "files+context_prune")

    notes = [e for e in events if isinstance(e, HarnessIntervened) and e.layer == "context_prune"]
    # 6. çağrı 60k bildirdi → 7. adımda budama; 70k'da histerezis bekletir, 80k'da (+20k) yeniden
    assert [(n.step, n.action) for n in notes] == [(7, "prune"), (9, "prune")]
    seen = [e for e in events if isinstance(e, ContextAssembled)]
    before, after = seen[5].messages, seen[6].messages
    assert not any(STUB_PREFIX in m.content for m in before if isinstance(m, ToolResult))
    stubbed = [m for m in after if isinstance(m, ToolResult) and m.content.startswith(STUB_PREFIX)]
    assert len(stubbed) == 1  # 6 adımın son 5'i korunur
    assert after[: len(after)] == seen[7].messages[: len(after)]  # sonraki adımda ön ek sabit


async def test_layer_off_never_prunes(tmp_path: Path) -> None:
    events = await run_agent(tmp_path, "files")
    assert not [
        e for e in events if isinstance(e, HarnessIntervened) and e.layer == "context_prune"
    ]
