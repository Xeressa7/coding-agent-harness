"""Küçük araç iyileştirmeleri: replace_all, yol gösteren hatalar, elle bağlam toplama."""

import json
from pathlib import Path

import pytest

from harness.cli.commands import ChatSession
from harness.core.anatomy import get_preset
from harness.core.pruning import STUB_PREFIX
from harness.llm.base import ModelInfo
from harness.llm.providers import Provider, get_provider
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall, ToolResult
from harness.tools.base import ToolRegistry
from harness.tools.hints import closest_line

pytestmark = pytest.mark.anyio


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="c", name=name, arguments=json.dumps(arguments))


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "sayac.js").write_text(
        "const a = eski;\nfunction f() {\n    return eski + 1;\n}\n", encoding="utf-8"
    )
    (tmp_path / "frontend" / "src" / "sayaclar.js").write_text("x\n", encoding="utf-8")
    return get_preset("files").tool_registry(tmp_path)


async def test_replace_all_changes_every_occurrence(registry: ToolRegistry, tmp_path: Path) -> None:
    path = "frontend/src/sayac.js"
    refused = await registry.call(call("edit_file", path=path, old_text="eski", new_text="yeni"))
    assert refused.is_error and "replace_all: true" in refused.content

    done = await registry.call(
        call("edit_file", path=path, old_text="eski", new_text="yeni", replace_all=True)
    )

    assert not done.is_error and done.content == f"{path}: 2 yerde metin değiştirildi."
    text = (tmp_path / path).read_text(encoding="utf-8")
    assert "eski" not in text and text.count("yeni") == 2


async def test_missing_paths_suggest_close_names_and_same_named_files(
    registry: ToolRegistry,
) -> None:
    typo = await registry.call(call("read_file", path="frontend/src/sayc.js"))
    elsewhere = await registry.call(call("read_file", path="src/sayac.js"))  # bash klasöründen
    nothing = await registry.call(call("read_file", path="yok/hic.txt"))
    folder = await registry.call(call("list_dir", path="frontent"))

    assert "Bunu mu demek istedin: `frontend/src/sayac.js`" in typo.content
    assert "`frontend/src/sayac.js`" in elsewhere.content and "köküne göredir" in elsewhere.content
    assert "list_dir ya da search ile bul" in nothing.content
    assert "`frontend`" in folder.content


def test_closest_line_points_at_whitespace_differences() -> None:
    text = "def f():\n\treturn eski + 1\n"

    assert closest_line(text, "    return eski + 1") == (
        "En yakın satır 2 (içerik aynı, girinti ya da boşluk farklı): \treturn eski + 1"
    )
    assert closest_line(text, "tamamen başka bir şey") is None


async def test_unmatched_old_text_shows_the_nearest_line(registry: ToolRegistry) -> None:
    result = await registry.call(
        call("edit_file", path="frontend/src/sayac.js", old_text="return eski+1;", new_text="x")
    )

    assert result.is_error and "En yakın satır 3: " in result.content


async def test_compact_turns_old_results_into_notes(tmp_path: Path) -> None:
    async def no_models(provider: Provider) -> list[ModelInfo]:
        return []

    (tmp_path / "a.txt").write_text("içerik " * 200, encoding="utf-8")
    replies = [
        AssistantMessage(
            tool_calls=(ToolCall(id=f"c{i}", name="read_file", arguments='{"path": "a.txt"}'),)
        )
        for i in range(7)
    ] + [AssistantMessage(text="okudum")]
    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        provider=get_provider("chatgpt"),
        model="luna",
        effort=None,
        make_llm=lambda provider, model, effort: ScriptedLLM(replies),
        load_models=no_models,
        load_prompt=lambda anatomy: "talimat",
        sessions_dir=tmp_path.parent / f"{tmp_path.name}-kayit",
        checkpoints=False,
    )
    assert "Toplanacak eski araç sonucu yok" in (await session.run("/compact")).text
    async for event in session.agent.run("oku"):
        session.log.write(event)

    reply = await session.run("/compact")

    assert reply.text.startswith("2 eski araç sonucu bağlamdan çıkarıldı")
    results = [m for m in session.agent.history if isinstance(m, ToolResult)]
    assert [m.content.startswith(STUB_PREFIX) for m in results] == [True] * 2 + [False] * 5
