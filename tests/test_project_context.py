"""Proje bağlamı katmanları: AGENTS.md talimatı ve agent-context hafızası (ctx)."""

import json
import sys
from pathlib import Path

import pytest

from harness.core import agent as agent_module
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
# `ctx init`'in AGENTS.md'ye yazdığı bölümün biçimi (agent-context: templates/AGENTS-router.md)
ROUTER = f"## Hafıza\n\nGöreve başlamadan .memory'de ara.\n\n{INDEX}"


def test_agents_md_is_read_and_claude_md_import_is_followed(tmp_path: Path) -> None:
    assert load_instructions(tmp_path) is None
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(f"Testleri koş.\n{INDEX}\n", encoding="utf-8")

    found = load_instructions(tmp_path)
    assert found is not None and found.source == "AGENTS.md"
    assert "Testleri koş." in found.text and "ESKİ HARİTA" not in found.text

    (tmp_path / "AGENTS.md").unlink()
    via_claude = load_instructions(tmp_path)
    assert via_claude is not None and via_claude.source == "CLAUDE.md"  # @ dosyası yok: satır kalır


def test_ctx_memory_section_is_split_off_and_the_rest_is_kept(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        f"# Proje\n\nTürkçe yaz.\n\n{ROUTER}\n\n## Test\n\npytest koş.\n", encoding="utf-8"
    )
    found = load_instructions(tmp_path)
    assert found is not None
    assert found.text == "# Proje\n\nTürkçe yaz.\n\n## Test\n\npytest koş."
    assert found.memory_chars == len(ROUTER)

    (tmp_path / "AGENTS.md").write_text(ROUTER, encoding="utf-8")  # yalnız `ctx init` bölümü
    only_memory = load_instructions(tmp_path)
    assert only_memory is not None and only_memory.text == ""


def test_import_outside_the_project_is_not_followed(tmp_path: Path) -> None:
    secret = tmp_path / "gizli.txt"
    secret.write_text("SIR", encoding="utf-8")
    project = tmp_path / "proje"
    project.mkdir()
    (project / "AGENTS.md").write_text("@../gizli.txt\n", encoding="utf-8")
    found = load_instructions(project)
    assert found is not None and "SIR" not in found.text


async def run(
    tmp_path: Path, anatomy_name: str, source: str = "x = 1\n"
) -> tuple[Agent, list[Event]]:
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
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


async def test_memory_rules_in_agents_md_do_not_reach_the_model_when_memory_is_off(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(f"Türkçe yaz.\n\n{ROUTER}", encoding="utf-8")

    _, events = await run(tmp_path, "files")

    seen = system_seen(events)
    assert "Türkçe yaz." in seen and ".memory" not in seen and "ESKİ HARİTA" not in seen
    [note] = [e for e in events if isinstance(e, HarnessIntervened)]
    assert note.summary.endswith(f"hafıza bölümü ({len(ROUTER)}) ayrıldı")

    (tmp_path / "AGENTS.md").write_text(ROUTER, encoding="utf-8")  # bu repodaki durum
    _, events = await run(tmp_path, "files")
    assert notes(events) == [(0, "agents_md", "skip")] and ".memory" not in system_seen(events)


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
    (tmp_path / "AGENTS.md").write_text(f"Kural.\n\n{ROUTER}", encoding="utf-8")

    _, events = await run(tmp_path, "files+memory")

    assert notes(events) == [
        (0, "agents_md", "inject"),
        (0, "memory", "inject"),
        (1, "memory", "recall"),
    ]
    seen = system_seen(events)
    assert "HAFIZA HARİTASI" in seen and "ESKİ HARİTA" not in seen  # bayat blok yerine taze
    assert "Göreve başlamadan" not in seen  # hafızanın kurallarını ctx'in kancası verir
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.summary == "a.py"  # aracın kendi anlatımı, hafıza özetiyle ezilmez
    result = finished.result.content
    assert "x = 1" in result and "[harness · hafıza]" in result and "facts/a.md" in result
    [(_, recall)] = [c for c in fake_ctx if c[0] == ["hook", "recall"]]
    assert recall["tool_name"] == "Read"
    assert str(recall["tool_input"]).endswith("a.py'}")  # ctx'e mutlak yol gider
    assert recall["session_id"] == fake_ctx[0][1]["session_id"]  # aynı not iki kez gelmesin


async def test_raw_level_stays_a_bare_model_even_with_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Türkçe yaz.", encoding="utf-8")
    assert get_preset("raw").agents_md is False
    _, events = await run(tmp_path, "raw")
    assert notes(events) == [] and "Türkçe yaz." not in system_seen(events)
    _, events = await run(tmp_path, "raw+agents_md")  # istenirse açıkça eklenir
    assert "Türkçe yaz." in system_seen(events)


async def test_unexpected_ctx_output_is_ignored_and_a_failing_ctx_is_not_a_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "context.yaml").write_text("memory_path: .memory\n", encoding="utf-8")
    real_run_ctx = project_context._run_ctx  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(project_context, "ctx_command", lambda: "ctx")
    for out in ("null", "[1]", "3", '{"hookSpecificOutput": null}', '{"hookSpecificOutput": []}'):

        def prints(*_: object, out: str = out) -> str:
            return out

        monkeypatch.setattr(project_context, "_run_ctx", prints)
        assert await project_context.CtxMemory(tmp_path, "s").index() is None, out

    script = "import sys; print('HATA: bozuk depo'); sys.exit(2)"
    with pytest.raises(project_context.MemoryUnavailable, match="bozuk depo"):
        real_run_ctx(sys.executable, ["-c", script], {}, tmp_path)


async def test_a_failing_recall_turns_the_memory_layer_off_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "context.yaml").write_text("memory_path: .memory\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    calls: list[list[str]] = []

    def run_ctx(command: str, args: list[str], payload: dict[str, object], cwd: Path) -> str:
        calls.append(args)
        if args == ["hook", "index"]:
            return json.dumps({"hookSpecificOutput": {"additionalContext": "HARİTA"}})
        raise project_context.MemoryUnavailable("ctx hook recall çalışmadı: zaman aşımı")

    monkeypatch.setattr(project_context, "ctx_command", lambda: "ctx")
    monkeypatch.setattr(project_context, "_run_ctx", run_ctx)
    anatomy = get_preset("files+memory")
    tools = anatomy.tool_registry(tmp_path)
    read = '{"path": "a.py"}'
    agent = Agent(
        ScriptedLLM(
            [
                AssistantMessage(tool_calls=(ToolCall(id="c1", name="read_file", arguments=read),)),
                AssistantMessage(tool_calls=(ToolCall(id="c2", name="read_file", arguments=read),)),
                AssistantMessage(text="bitti"),
            ]
        ),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=anatomy,
        root=tmp_path,
    )
    events = [event async for event in agent.run("bak")]

    assert calls.count(["hook", "recall"]) == 1  # ikinci okumada yeniden beklenmedi
    assert notes(events) == [(0, "memory", "inject"), (1, "memory", "skip")]
    skip = [e for e in events if isinstance(e, HarnessIntervened)][-1]
    assert "zaman aşımı" in skip.summary and skip.call_id == "c1"


async def test_harness_notes_are_added_after_the_output_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_ctx: list[tuple[list[str], dict[str, object]]],
) -> None:
    (tmp_path / "context.yaml").write_text("memory_path: .memory\n", encoding="utf-8")
    real = agent_module.limit_output

    def tiny_limit(text: str) -> str | None:
        return real(text, limit=60)

    monkeypatch.setattr(agent_module, "limit_output", tiny_limit)
    source = "".join(f"satir_{i} = {i}\n" for i in range(40))

    _, events = await run(tmp_path, "files+memory", source)

    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.full_content is not None  # kesildi
    assert "[harness" not in finished.full_content  # tam metin yalnız aracın çıktısı
    assert finished.result.content.rstrip().endswith("a.py")  # not kesilmeden sonda
    assert "[harness · hafıza]" in finished.result.content


async def test_memory_layer_says_why_it_did_nothing(
    tmp_path: Path, fake_ctx: list[tuple[list[str], dict[str, object]]]
) -> None:
    _, events = await run(tmp_path, "files+memory")  # context.yaml yok

    [note] = [e for e in events if isinstance(e, HarnessIntervened)]
    assert (note.layer, note.action) == ("memory", "skip") and "context.yaml" in note.summary
    assert fake_ctx == []  # ctx hiç çağrılmadı, dosya başına da denenmedi
