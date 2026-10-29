"""Gezinti haritası: kayıt alanları, proje ağacı uç noktası ve haritanın JS hesabı (compile)."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from harness.cli.watch import WatchServer, project_tree
from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import TaskStarted
from harness.core.permissions import PermissionManager
from harness.core.session_log import SessionLogger
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage
from harness.tools.base import ToolError
from harness.tools.read_file import ReadFile, ReadFileParams
from harness.tools.search import Search, SearchParams

MAP_JS = Path(__file__).resolve().parent.parent / "src" / "harness" / "cli" / "watch_map.js"


@pytest.mark.anyio
async def test_read_file_records_the_range_it_really_showed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("\n".join(f"satır {i}" for i in range(1, 11)), encoding="utf-8")
    tool = ReadFile(root=tmp_path)
    part = await tool.run(ReadFileParams(path="a.py", start_line=8, end_line=40))
    assert (part.details["start"], part.details["end"], part.details["total_lines"]) == (8, 10, 10)
    whole = await tool.run(ReadFileParams(path="a.py"))
    assert (whole.details["start"], whole.details["end"]) == (1, 10)


@pytest.mark.anyio
async def test_search_records_matched_files_and_explains_a_bad_regex(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("eval()\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("x = 1\neval(x)\n", encoding="utf-8")
    tool = Search(root=tmp_path)
    found = await tool.run(SearchParams(pattern="eval"))
    assert found.details["paths"] == ["a.py", "b.py"]
    with pytest.raises(ToolError, match="glob='\\*.md'"):
        await tool.run(SearchParams(pattern="*", glob="*.md"))


@pytest.mark.anyio
async def test_every_run_records_where_it_ran(tmp_path: Path) -> None:
    tools = get_preset("files").tool_registry(tmp_path)
    agent = Agent(
        ScriptedLLM([AssistantMessage(text="tamam")]),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        root=tmp_path,
    )
    first = [e async for e in agent.run("selam")][0]
    assert isinstance(first, TaskStarted) and first.root == str(tmp_path)


def test_project_tree_lists_todays_folder_without_hidden_and_tool_dirs(tmp_path: Path) -> None:
    project = tmp_path / "proje"
    for folder in ["src/pkg", ".git/objects", ".venv/lib", "node_modules/x", "docs"]:
        (project / folder).mkdir(parents=True)
    (project / "src" / "pkg" / "a.py").write_text("", encoding="utf-8")
    (project / "README.md").write_text("", encoding="utf-8")
    log = SessionLogger(tmp_path / "kayit" / "k.jsonl")
    log.write(TaskStarted(task="t", root=str(project)))

    tree = project_tree(log.path)
    assert tree["name"] == "proje" and tree["complete"]
    assert tree["entries"] == ["docs/", "src/", "README.md", "src/pkg/", "src/pkg/a.py"]
    assert project_tree(log.path, limit=2)["complete"] is False

    gone = SessionLogger(tmp_path / "kayit" / "silinmis.jsonl")
    gone.write(TaskStarted(task="t", root=str(tmp_path / "yok")))
    assert project_tree(gone.path)["entries"] == []

    server = WatchServer(tmp_path / "kayit")
    try:
        base = server.start()
        assert httpx.get(base + "api/tree?path=k.jsonl").json()["entries"] == tree["entries"]
        assert httpx.get(base + "api/tree?path=../k.jsonl").status_code == 404
        script = httpx.get(base + "map.js")
        assert script.status_code == 200 and "HarnessMap" in script.text
    finally:
        server.close()


# ---------------------------------------------------------------- haritanın JS hesabı


def event(kind: str, second: float, **fields: object) -> dict[str, object]:
    stamp = f"2026-10-29T10:00:{second:06.3f}+00:00" if second < 60 else None
    return {"kind": kind, "timestamp": stamp, **fields}


def call(call_id: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {"id": call_id, "name": name, "arguments": json.dumps(arguments)}


def finished(
    second: float, call_id: str, name: str, args: dict[str, object], **extra: object
) -> dict[str, object]:
    result = {"tool_call_id": call_id, "content": "", "is_error": extra.pop("error", False)}
    return event("tool_finished", second, call=call(call_id, name, args), result=result, **extra)


@pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")
def test_map_compile_turns_the_log_into_ordered_visits() -> None:
    root = "C:\\Users\\ben\\proje"
    events = [
        event("task_started", 0, task="eval nerede?", root=root),
        event(
            "context_assembled",
            0.1,
            step=1,
            messages=[{"role": "system", "text": "talimat"}],
            tools=[],
        ),
        event("model_started", 0.2, step=1),
        event("model_replied", 1.0, step=1, text="", tool_calls=[], reasoning="önce arayayım"),
        finished(
            1.2,
            "s1",
            "search",
            {"pattern": "eval", "glob": "**/*"},
            details={"paths": ["src\\a.py", "b.py"]},
        ),
        event("model_started", 31.2, step=2),  # 30 sn bekleme: çubukta sıkıştırılır
        finished(
            32.0,
            "r1",
            "read_file",
            {"path": f"{root}\\src\\a.py"},
            details={"path": f"{root}\\src\\a.py", "start": 5, "end": 20, "total_lines": 40},
        ),
        finished(
            32.5,
            "r2",
            "read_file",
            {"path": "./src/a.py", "start_line": 30},
            details={"lines": 11, "total_lines": 40},
        ),
        finished(
            33.0,
            "e1",
            "edit_file",
            {"path": "src/a.py"},
            details={"path": "src/a.py", "line": 7, "total_lines": 40},
        ),
        finished(33.1, "x1", "read_file", {"path": "yok.py"}, error=True),
        event("task_finished", 34.0, reason="step_limit", steps=2),
    ]
    script = (
        "globalThis.window = globalThis;"
        f"require({json.dumps(str(MAP_JS))});"
        "let data = '';"
        "process.stdin.on('data', (c) => data += c);"
        "process.stdin.on('end', () => {"
        "  const out = window.HarnessMap.compile(JSON.parse(data));"
        "  process.stdout.write(JSON.stringify(out));"
        "});"
    )
    done = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(events),
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    visits = out["visits"]
    assert [v["kind"] for v in visits] == [
        "system",
        "prompt",
        "search",
        "read",
        "read",
        "edit",
        "read",
    ]
    search, first, second, edit, missing = visits[2:]
    assert search["hits"] == ["src/a.py", "b.py"]  # ters bölü ve kök temizlenir
    assert first["node"] == second["node"] == edit["node"] == "file:src/a.py"
    assert first["range"] == [5, 20, 40] and second["range"] == [30, 40, 40]
    assert edit["tick"] == [7, 40] and missing["error"] and "range" not in missing
    assert out["root"] == root
    # 30 sn'lik bekleme 1,5 sn'ye sıkışır: koşu ~5,5 sn sürer gibi görünür.
    assert 5 < out["end"] < 6
    assert [m["type"] for m in out["marks"]] == ["turn", "error", "stop"]
