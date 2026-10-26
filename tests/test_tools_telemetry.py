"""Yeni araçlar ve araç telemetrisi: her araç ne yaptığını ve sonucunu insan diliyle söyler."""

import json
from pathlib import Path

import httpx
import pytest

from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import (
    Event,
    PermissionRequested,
    ToolFinished,
    ToolStarted,
)
from harness.core.permissions import Decision, PermissionManager
from harness.environments.base import CommandResult
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall
from harness.tools.base import ToolRegistry
from harness.tools.bash import Bash
from harness.tools.delete_file import DeleteFile
from harness.tools.edit_file import EditFile
from harness.tools.fetch_url import FetchUrl, html_to_text
from harness.tools.list_dir import ListDir
from harness.tools.read_file import ReadFile
from harness.tools.search import Search
from harness.tools.update_plan import UpdatePlan
from harness.tools.write_file import WriteFile

pytestmark = pytest.mark.anyio


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=json.dumps(arguments))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Proje\nAçıklama\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "gizli.py").write_text("", encoding="utf-8")
    return tmp_path


async def test_list_dir_draws_a_tree_with_sizes_and_skips_virtualenvs(project: Path) -> None:
    registry = ToolRegistry([ListDir(root=project)])

    outcome = await registry.execute(call("list_dir", path=".", depth=3))

    lines = outcome.result.content.splitlines()
    assert lines[0] == "src/" and lines[1] == "  pkg/"
    assert lines[2].startswith("    a.py  (") and lines[3].startswith("README.md  (")
    assert ".venv" not in outcome.result.content
    assert outcome.summary == "2 klasör · 2 dosya"
    shallow = await registry.execute(call("list_dir", path=".", depth=1))
    assert "pkg" not in shallow.result.content
    outside = await registry.execute(call("list_dir", path=".."))
    assert outside.result.is_error


async def test_delete_file_removes_only_files(project: Path) -> None:
    registry = ToolRegistry([DeleteFile(root=project)])

    done = await registry.execute(call("delete_file", path="README.md"))

    assert not (project / "README.md").exists() and done.summary.startswith("silindi")
    assert (await registry.execute(call("delete_file", path="src"))).result.is_error
    assert (await registry.execute(call("delete_file", path="yok.txt"))).result.is_error
    assert (await registry.execute(call("delete_file", path="../x"))).result.is_error


async def test_update_plan_keeps_the_latest_plan_and_reports_progress() -> None:
    plan = UpdatePlan()
    registry = ToolRegistry([plan])
    steps = [
        {"step": "Dosyaları oku", "status": "completed"},
        {"step": "Hatayı düzelt", "status": "in_progress"},
        {"step": "Testleri koş", "status": "pending"},
    ]

    outcome = await registry.execute(call("update_plan", steps=steps))

    assert outcome.summary == "1/3 tamam · şimdi: Hatayı düzelt"
    assert outcome.details["total"] == 3 and outcome.details["steps"][1]["status"] == "in_progress"
    assert "[x] Dosyaları oku" in outcome.result.content
    assert [s.step for s in plan.steps] == ["Dosyaları oku", "Hatayı düzelt", "Testleri koş"]
    two_at_once = [{"step": "a", "status": "in_progress"}, {"step": "b", "status": "in_progress"}]
    refused = await registry.execute(call("update_plan", steps=two_at_once))
    assert refused.result.is_error and "en fazla bir" in refused.result.content


async def test_fetch_url_turns_html_into_text_and_says_when_it_cut() -> None:
    page = (
        "<html><head><title>x</title><style>p{}</style></head><body>"
        "<h1>Sürüm 2.0</h1><script>alert(1)</script><p>Yeni   özellik.</p>"
        + "<p>uzun</p>" * 300
        + "</body></html>"
    )

    def site(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/yok":
            return httpx.Response(404, text="bulunamadı")
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    registry = ToolRegistry([FetchUrl(transport=httpx.MockTransport(site))])

    outcome = await registry.execute(
        call("fetch_url", url="https://ornek.dev/notlar", max_chars=500)
    )

    text = outcome.result.content
    assert "Sürüm 2.0\nYeni özellik." in text
    assert "alert" not in text and "p{}" not in text
    assert "karakter kesildi" in text and outcome.details["truncated"] is True
    assert outcome.summary.startswith("200 · 500 karakter")
    assert (await registry.execute(call("fetch_url", url="https://ornek.dev/yok"))).result.is_error
    assert (await registry.execute(call("fetch_url", url="file:///etc/passwd"))).result.is_error


def test_html_to_text_keeps_paragraph_breaks() -> None:
    assert html_to_text("<div>bir</div><div>iki <b>üç</b></div>") == "bir\niki üç"


class FixedEnvironment:
    async def run(self, command: str, timeout_seconds: float) -> CommandResult:
        return CommandResult(stdout="geçti\n", stderr="uyarı\n", exit_code=1)


async def test_each_tool_describes_the_call_and_summarizes_the_result(project: Path) -> None:
    registry = ToolRegistry(
        [
            ReadFile(root=project),
            WriteFile(root=project),
            EditFile(root=project),
            Search(root=project),
            Bash(FixedEnvironment()),
        ]
    )
    cases = [
        (call("read_file", path="README.md", start_line=1, end_line=1), "README.md · satır 1–1"),
        (call("write_file", path="yeni.txt", content="a\nb\n"), "yeni.txt"),
        (call("edit_file", path="yeni.txt", old_text="b", new_text="c\nd"), "yeni.txt"),
        (call("search", pattern="x =", glob="*.py"), "“x =” · *.py"),
        (call("bash", command="pytest -q"), "$ pytest -q"),
    ]
    summaries: list[str] = []
    for tool_call, described in cases:
        assert registry.describe(tool_call)[1] == described
        summaries.append((await registry.execute(tool_call)).summary)

    assert summaries == [
        "1 satır / 2",
        "oluşturuldu · 2 satır",
        "satır 2 · −1 +2",
        "1 eşleşme · 1 dosya",
        "çıkış kodu 1 · 1 satır çıktı, 1 satır hata çıktısı",
    ]
    assert registry.describe(ToolCall(id="x", name="read_file", arguments="bozuk")) == ("read", "")
    assert registry.describe(ToolCall(id="x", name="yok", arguments="{}")) == (None, "")


async def test_events_carry_category_summary_and_outcome_for_the_timeline(project: Path) -> None:
    anatomy = get_preset("files")
    tools = anatomy.tool_registry(project)
    model = ScriptedLLM(
        [
            AssistantMessage(
                tool_calls=(
                    call("list_dir", path="."),
                    call("write_file", path="not.txt", content="x"),
                )
            ),
            AssistantMessage(text="bitti"),
        ]
    )
    agent = Agent(model, tools, "talimat", permissions=PermissionManager(project, tools))
    events: list[Event] = []
    stream = agent.run("işle")
    answer: bool | None = None
    try:
        while True:
            event = await stream.asend(answer)
            events.append(event)
            answer = False if isinstance(event, PermissionRequested) else None
    except StopAsyncIteration:
        pass

    asked = [e for e in events if isinstance(e, PermissionRequested)]
    assert [(e.call.name, e.summary) for e in asked] == [("write_file", "not.txt")]
    # Zaman çizelgesi izin beklemesini bu damgadan çizer.
    assert all(e.timestamp is not None for e in asked)
    started = [e for e in events if isinstance(e, ToolStarted)]
    assert [(e.call.name, e.category, e.summary) for e in started] == [
        ("list_dir", "read", ". · derinlik 2")
    ]
    finished = {e.call.name: e for e in events if isinstance(e, ToolFinished)}
    assert finished["list_dir"].outcome.endswith("dosya") and finished["list_dir"].details["files"]
    assert finished["write_file"].outcome == "izin verilmedi"
    assert finished["write_file"].category == "write"


def test_permission_follows_the_tool_category(project: Path) -> None:
    tools = get_preset("full").tool_registry(project)
    permissions = PermissionManager(project, tools)
    free = {"read_file", "list_dir", "search", "update_plan"}
    for spec in tools.specs():
        expected = Decision.ALLOW if spec.name in free else Decision.ASK
        assert permissions.policy(spec.name) is expected, spec.name
    assert permissions.decide(call("fetch_url", url="https://x.dev")) is Decision.ASK
    assert permissions.decide(call("delete_file", path="../dış.txt")) is Decision.DENY
