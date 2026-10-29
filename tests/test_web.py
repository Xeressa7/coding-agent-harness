"""Web ön yüzü: tek sayfa + WebSocket'in CLI/TUI ile aynı çekirdeği sürdüğünü doğrular."""

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from harness.cli.web import create_app
from harness.core.anatomy import get_preset
from harness.core.events import TaskStarted
from harness.core.session_log import read_session
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall


def write_call(call_id: str, path: str, content: str) -> AssistantMessage:
    arguments = json.dumps({"path": path, "content": content})
    return AssistantMessage(
        tool_calls=(ToolCall(id=call_id, name="write_file", arguments=arguments),)
    )


def recv(ws: WebSocketTestSession) -> dict[str, Any]:
    text: str = ws.receive_text()
    return json.loads(text)


def test_index_page_is_served(tmp_path: Path) -> None:
    app = create_app(get_preset("raw"), tmp_path, "kural", lambda: ScriptedLLM([]))
    client = TestClient(app)

    # starlette.testclient bu httpx sürümüyle tip ipucu veremiyor (bilinen uyumluluk boşluğu).
    response: httpx.Response = client.get("/")  # type: ignore[reportUnknownVariableType]

    assert response.status_code == 200  # type: ignore[reportUnknownMemberType]
    assert "Harness" in response.text  # type: ignore[reportUnknownMemberType]
    assert "Birlikte ne yapalım?" in response.text  # type: ignore[reportUnknownMemberType]


def test_events_stream_and_task_finishes(tmp_path: Path) -> None:
    model = ScriptedLLM([AssistantMessage(text="merhaba")])
    app = create_app(get_preset("raw"), tmp_path, "kural", lambda: model)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"task": "selam"})
        kinds: list[str] = []
        while True:
            event = recv(ws)
            kinds.append(event["kind"])
            if event["kind"] == "task_finished":
                assert event["reason"] == "done"
                break

    assert kinds[0] == "task_started"
    assert "model_replied" in kinds


def test_allow_choice_lets_the_tool_run(tmp_path: Path) -> None:
    model = ScriptedLLM([write_call("c1", "a.txt", "merhaba"), AssistantMessage(text="tamam")])
    app = create_app(get_preset("files"), tmp_path, "kural", lambda: model)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"task": "yaz"})
        while True:
            event = recv(ws)
            if event["kind"] == "permission_requested":
                ws.send_json({"choice": "allow"})
            if event["kind"] == "task_finished":
                break

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "merhaba"


def test_deny_choice_skips_the_tool(tmp_path: Path) -> None:
    model = ScriptedLLM([write_call("c1", "a.txt", "x"), AssistantMessage(text="tamam")])
    app = create_app(get_preset("files"), tmp_path, "kural", lambda: model)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"task": "yaz"})
        while True:
            event = recv(ws)
            if event["kind"] == "permission_requested":
                ws.send_json({"choice": "deny"})
            if event["kind"] == "task_finished":
                break

    assert not (tmp_path / "a.txt").exists()


def test_always_choice_grants_for_the_rest_of_the_connection(tmp_path: Path) -> None:
    model = ScriptedLLM(
        [
            write_call("c1", "a.txt", "bir"),
            AssistantMessage(text="tamam"),
            write_call("c2", "b.txt", "iki"),
            AssistantMessage(text="tamam"),
        ]
    )
    app = create_app(get_preset("files"), tmp_path, "kural", lambda: model)
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"task": "ilk görev"})
        permission_requests = 0
        while True:
            event = recv(ws)
            if event["kind"] == "permission_requested":
                permission_requests += 1
                ws.send_json({"choice": "always"})
            if event["kind"] == "task_finished":
                break

        ws.send_json({"task": "ikinci görev"})
        while True:
            event = recv(ws)
            if event["kind"] == "permission_requested":
                permission_requests += 1
                ws.send_json({"choice": "allow"})
            if event["kind"] == "task_finished":
                break

    assert permission_requests == 1
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "bir"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "iki"


def test_each_connection_gets_its_own_agent_and_history(tmp_path: Path) -> None:
    model_a = ScriptedLLM([AssistantMessage(text="birinci bağlantı")])
    model_b = ScriptedLLM([AssistantMessage(text="ikinci bağlantı")])
    calls = iter([model_a, model_b])
    app = create_app(get_preset("raw"), tmp_path, "kural", lambda: next(calls))
    client = TestClient(app)

    def last_reply_text(ws: WebSocketTestSession) -> str:
        text = ""
        while True:
            event = recv(ws)
            if event["kind"] == "model_replied":
                text = event["text"]
            if event["kind"] == "task_finished":
                return text

    with client.websocket_connect("/ws") as ws1:
        ws1.send_json({"task": "x"})
        assert last_reply_text(ws1) == "birinci bağlantı"

    with client.websocket_connect("/ws") as ws2:
        ws2.send_json({"task": "x"})
        assert last_reply_text(ws2) == "ikinci bağlantı"


def test_each_connection_writes_its_own_session_log(tmp_path: Path) -> None:
    # Tarayıcı sohbeti de kayda geçer: canlı panel, harita ve inspect onu görür.
    project, logs = tmp_path / "proje", tmp_path / "kayit"
    project.mkdir()
    app = create_app(
        get_preset("files"),
        project,
        "kural",
        lambda: ScriptedLLM([AssistantMessage(text="merhaba")]),
        log_dir=logs,
        provider="opencode-go",
        model="glm-5.3",
    )
    client = TestClient(app)
    for _ in range(2):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"task": "selam"})
            while recv(ws)["kind"] != "task_finished":
                pass

    files = sorted(logs.glob("*.jsonl"))
    assert len(files) == 2  # her bağlantı ayrı oturum
    events = read_session(files[0])
    first = events[0]
    assert isinstance(first, TaskStarted)
    assert (first.provider, first.model, first.root) == ("opencode-go", "glm-5.3", str(project))
    assert events[-1].kind == "task_finished"
