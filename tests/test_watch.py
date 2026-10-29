"""Canlı panel: kayıt klasörünü yerelde sunar, kaydı baştan oynatıp yeni satırları akıtır."""

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from harness.cli.commands import ChatSession
from harness.cli.watch import WatchServer, list_logs
from harness.core.anatomy import get_preset
from harness.core.events import TaskFinished, TaskStarted
from harness.core.session_log import SessionLogger
from harness.llm.base import ModelInfo
from harness.llm.providers import get_provider
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage


@pytest.fixture
def panel(tmp_path: Path) -> Iterator[tuple[WatchServer, Path]]:
    folder = tmp_path / "kayitlar"
    log = SessionLogger(folder / "proje" / "20261029-1.jsonl")
    log.write(TaskStarted(task="ilk görev", model="glm-5.3"))
    log.write(TaskFinished(reason="done", steps=1))
    server = WatchServer(folder)
    server.start()
    yield server, log.path
    server.close()


def events_from(lines: Iterator[str], count: int) -> list[tuple[int, dict[str, object]]]:
    """SSE satırlarından `count` olay okur: (satır numarası, olay)."""
    found: list[tuple[int, dict[str, object]]] = []
    event_id = 0
    for line in lines:
        if line.startswith("id: "):
            event_id = int(line[4:])
        elif line.startswith("data: "):
            found.append((event_id, json.loads(line[6:])))
            if len(found) == count:
                break
    return found


def test_lists_logs_newest_first_with_their_first_task(panel: tuple[WatchServer, Path]) -> None:
    server, log = panel
    (log.parent / "results.jsonl").write_text("{}\n", encoding="utf-8")  # deney özeti listelenmez
    response = httpx.get(server.start() + "api/sessions")
    assert response.status_code == 200
    (item,) = response.json()
    assert item["path"] == "proje/20261029-1.jsonl" and item["task"] == "ilk görev"
    assert list_logs(server.folder) == response.json()
    assert "Harness canlı panel" in httpx.get(server.start()).text


def test_stream_replays_then_follows_new_lines_and_resumes_after_an_id(
    panel: tuple[WatchServer, Path],
) -> None:
    server, log = panel
    url = server.start() + "api/stream?path=proje/20261029-1.jsonl"
    with httpx.Client(timeout=5) as client, client.stream("GET", url) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines()
        replayed = events_from(lines, 2)
        assert [(i, e["kind"]) for i, e in replayed] == [(1, "task_started"), (2, "task_finished")]
        SessionLogger(log).write(TaskStarted(task="ikinci tur"))
        ((number, event),) = events_from(lines, 1)
        assert (number, event["task"]) == (3, "ikinci tur")

    # Kopan tarayıcı kaldığı satırı söyler; yalnız sonrası gelir.
    headers = {"Last-Event-ID": "2"}
    with httpx.Client(timeout=5) as client, client.stream("GET", url, headers=headers) as response:
        ((number, event),) = events_from(response.iter_lines(), 1)
        assert (number, event["task"]) == (3, "ikinci tur")


def test_only_logs_inside_the_folder_are_served(
    panel: tuple[WatchServer, Path], tmp_path: Path
) -> None:
    server, _ = panel
    (tmp_path / "gizli.jsonl").write_text("{}\n", encoding="utf-8")
    base = server.start() + "api/stream?path="
    for path in ["../gizli.jsonl", "proje/yok.jsonl", "", "proje"]:
        assert httpx.get(base + path).status_code == 404
    assert httpx.get(server.start() + "baska").status_code == 404


def test_url_for_a_log_opens_that_log(panel: tuple[WatchServer, Path], tmp_path: Path) -> None:
    server, log = panel
    assert server.url_for(log).endswith("/?path=proje/20261029-1.jsonl")
    assert server.url_for(tmp_path / "dışarıda.jsonl") == server.start()
    assert server.url_for(None) == server.start()


@pytest.mark.anyio
async def test_watch_command_starts_the_panel_once_and_opens_the_browser(tmp_path: Path) -> None:
    opened: list[str] = []
    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        provider=get_provider("chatgpt"),
        model="luna",
        effort=None,
        make_llm=lambda provider, model, effort: ScriptedLLM([AssistantMessage(text="ok")]),
        load_models=lambda provider: _no_models(),
        load_prompt=lambda anatomy: "talimat",
        sessions_dir=tmp_path / "logs",
        open_url=opened.append,
    )
    try:
        first = (await session.run("/watch")).text
        await session.run("/watch")
        assert "Canlı panel: http://127.0.0.1:" in first and opened[0] in first
        assert opened[0] == opened[1]  # aynı sunucu, ikinci kez kurulmaz
        assert httpx.get(opened[0] + "api/sessions").json() == []
    finally:
        session.close()


async def _no_models() -> list[ModelInfo]:
    return []
