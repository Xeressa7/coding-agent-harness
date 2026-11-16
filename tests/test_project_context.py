"""Proje bağlamı katmanları: AGENTS.md talimatı ve agent-context hafızası (ctx)."""

import json
from pathlib import Path

import pytest

from harness.core import project_context
from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import ContextAssembled, Event, HarnessIntervened, ToolFinished
from harness.core.permissions import PermissionManager
from harness.core.project_context import load_instructions
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, SystemMessage, ToolCall

pytestmark = pytest.mark.anyio

INDEX = "<!-- ctx:index:basla -->\nESKİ HARİTA\n<!-- ctx:index:bitir -->"  # ctx'in AGENTS.md bloğu


def test_agents_md_is_read_and_claude_md_import_is_followed(tmp_path: Path) -> None:
    assert load_instructions(tmp_path) is None
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(f"Testleri koş.\n{INDEX}\n", encoding="utf-8")

    found = load_instructions(tmp_path)
    assert found is not None and found.source == "AGENTS.md"
    assert "Testleri koş." in found.text and "ESKİ HARİTA" in found.text
    dropped = load_instructions(tmp_path, drop_memory_index=True)
    assert dropped is not None and "ESKİ HARİTA" not in dropped.text  # taze harita gelecek

    (tmp_path / "AGENTS.md").unlink()
    via_claude = load_instructions(tmp_path)
    assert via_claude is not None and via_claude.source == "CLAUDE.md"  # @ dosyası yok: satır kalır


def test_import_outside_the_project_is_not_followed(tmp_path: Path) -> None:
    secret = tmp_path / "gizli.txt"
    secret.write_text("SIR", encoding="utf-8")
    project = tmp_path / "proje"
    project.mkdir()
    (project / "AGENTS.md").write_text("@../gizli.txt\n", encoding="utf-8")
    found = load_instructions(project)
    assert found is not None and "SIR" not in found.text


async def run(tmp_path: Path, anatomy_name: str) -> tuple[Agent, list[Event]]:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    anatomy = get_preset(anatomy_name)
    tools = anatomy.tool_registry(tmp_path)
    call = ToolCall(id="c1", name="read_file", arguments='{"path": "a.py"}')
    agent = Agent(
        ScriptedLLM([AssistantMessage(tool_calls=(call,)), AssistantMessage(text="bitti")]),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=anatomy,
        root=tmp_path,
    )
    return agent, [event async for event in agent.run("bak")]


def notes(events: list[Event]) -> list[tuple[int, str, str]]:
    return [(e.step, e.layer, e.action) for e in events if isinstance(e, HarnessIntervened)]


def system_seen(events: list[Event]) -> str:
    first = next(e for e in events if isinstance(e, ContextAssembled))
    assert isinstance(first.messages[0], SystemMessage)
    return first.messages[0].text


async def test_agents_md_layer_adds_the_file_to_the_system_prompt_and_records_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("Türkçe yaz.", encoding="utf-8")

    _, events = await run(tmp_path, "files")
    assert notes(events) == [(0, "agents_md", "inject")]
    assert system_seen(events).startswith("talimat") and "Türkçe yaz." in system_seen(events)

    _, events = await run(tmp_path, "files-agents_md")
    assert notes(events) == [] and "Türkçe yaz." not in system_seen(events)


async def test_project_context_is_added_once_per_agent(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Türkçe yaz.", encoding="utf-8")
    agent, _ = await run(tmp_path, "files")
    agent._llm = ScriptedLLM([AssistantMessage(text="tamam")])  # type: ignore[attr-defined]
    second = [e async for e in agent.run("devam")]
    assert notes(second) == []
    assert system_seen(second).count("Türkçe yaz.") == 1


@pytest.fixture
def fake_ctx(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, object]]]:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run_ctx(command: str, args: list[str], payload: dict[str, object], cwd: Path) -> str:
        calls.append((args, payload))
        if args == ["hook", "index"]:
            text = "HAFIZA HARİTASI\n- `.memory/facts/a.md` — a.py nasıl çalışır"
        else:
            text = "HAFIZA — `a.py` dosyasini anan not(lar):\n- `.memory/facts/a.md` — a.py"
        return json.dumps({"hookSpecificOutput": {"additionalContext": text}})

    monkeypatch.setattr(project_context, "ctx_command", lambda: "ctx")
    monkeypatch.setattr(project_context, "_run_ctx", run_ctx)
    return calls


async def test_memory_layer_injects_the_map_and_recalls_notes_for_touched_files(
    tmp_path: Path, fake_ctx: list[tuple[list[str], dict[str, object]]]
) -> None:
    (tmp_path / "context.yaml").write_text("memory_path: .memory\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(f"Kural.\n{INDEX}", encoding="utf-8")

    _, events = await run(tmp_path, "files+memory")

    assert notes(events) == [
        (0, "agents_md", "inject"),
        (0, "memory", "inject"),
        (1, "memory", "recall"),
    ]
    seen = system_seen(events)
    assert "HAFIZA HARİTASI" in seen and "ESKİ HARİTA" not in seen  # bayat blok yerine taze
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary == "a.py"  # aracın kendi anlatımı, hafıza özetiyle ezilmez
    result = finished.result.content
    assert "x = 1" in result and "[harness · hafıza]" in result and "facts/a.md" in result
    [(_, recall)] = [c for c in fake_ctx if c[0] == ["hook", "recall"]]
    assert recall["tool_name"] == "Read"
    assert str(recall["tool_input"]).endswith("a.py'}")  # ctx'e mutlak yol gider
    assert recall["session_id"] == fake_ctx[0][1]["session_id"]  # aynı not iki kez gelmesin


async def test_memory_layer_says_why_it_did_nothing(
    tmp_path: Path, fake_ctx: list[tuple[list[str], dict[str, object]]]
) -> None:
    _, events = await run(tmp_path, "files+memory")  # context.yaml yok

    [note] = [e for e in events if isinstance(e, HarnessIntervened)]
    assert (note.layer, note.action) == ("memory", "skip") and "context.yaml" in note.summary
    assert fake_ctx == []  # ctx hiç çağrılmadı, dosya başına da denenmedi
