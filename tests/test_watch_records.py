"""Panelin dayandığı kayıt alanları (okunan aralık, arama eşleşmesi, kök) ve betikleri."""

from pathlib import Path

import httpx
import pytest

from harness.cli.watch import WatchServer
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


def test_panel_serves_its_scripts(tmp_path: Path) -> None:
    log = SessionLogger(tmp_path / "k.jsonl")
    log.write(TaskStarted(task="t", root=str(tmp_path)))
    server = WatchServer(tmp_path)
    try:
        base = server.start()
        for name, marker in (("ledger.js", "HarnessLedger"), ("files.js", "HarnessFiles")):
            script = httpx.get(base + name)
            assert script.status_code == 200 and marker in script.text
        assert httpx.get(base + "map.js").status_code == 404  # eski harita kaldırıldı
    finally:
        server.close()
