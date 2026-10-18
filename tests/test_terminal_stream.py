"""SSE bitmeden metin görünür; tool/reasoning içerikleri terminale sızmaz."""

import asyncio
import json
from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from rich.console import Console

from harness.cli import app as cli
from harness.core import agent as agent_module
from harness.core.agent import Agent
from harness.core.events import ModelReplied, ModelTextDelta, TaskFinished
from harness.core.session_log import SessionLogger, read_session
from harness.llm.responses import ResponsesError, ResponsesLLM
from harness.messages import AssistantMessage
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile


def sse(kind: str, **data: Any) -> bytes:
    return ("data: " + json.dumps({"type": kind, **data}) + "\n\n").encode()


class FixedToken:
    async def access_token(self) -> str:
        return "test-token"


class GatedResponse(httpx.AsyncByteStream):
    def __init__(self, *, fail: bool = False) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.fail = fail

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield sse("response.reasoning_text.delta", delta="HIDDEN_REASONING")
        yield sse("response.function_call_arguments.delta", delta="HIDDEN_ARGUMENTS")
        yield sse("response.output_text.delta", delta="")
        yield sse("response.output_text.delta", delta="Mer")
        self.waiting.set()
        await self.release.wait()
        if self.fail:
            yield sse("response.failed", response={"error": {"code": "stream_failed"}})
            return
        yield sse("response.output_text.delta", delta="haba 🌱")
        yield sse(
            "response.output_item.done",
            item={
                "type": "message",
                "content": [{"type": "output_text", "text": "Merhaba 🌱"}],
            },
        )
        yield sse(
            "response.completed",
            response={
                "output": [],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_model_duration_excludes_time_spent_rendering_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # İki parça arasında tüketicide 10'ar saniye geçirilmiş gibi ölç.
    clock = iter([0.0, 1.0, 2.0, 12.0, 13.0, 23.0, 30.0, 31.0])
    monkeypatch.setattr(agent_module, "perf_counter", lambda: next(clock))
    upstream = GatedResponse()
    upstream.release.set()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=upstream))
    async with httpx.AsyncClient(transport=transport) as http:
        agent = Agent(ResponsesLLM(FixedToken(), "m", http), ToolRegistry([]), "kural")
        events = [e async for e in agent.run("soru")]
    (reply,) = [e for e in events if isinstance(e, ModelReplied)]
    assert reply.duration_seconds == 9
    assert isinstance(events[-1], TaskFinished) and events[-1].duration_seconds == 31


@pytest.mark.anyio
@pytest.mark.parametrize("terminal", [False, True])
async def test_first_text_visible_before_http_completion_and_final_not_duplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: bool,
) -> None:
    output = StringIO()
    monkeypatch.setattr(
        cli,
        "console",
        Console(
            file=output,
            width=100,
            color_system=None,
            force_terminal=terminal,
        ),
    )
    upstream = GatedResponse()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=upstream))
    log = SessionLogger(tmp_path / "stream.jsonl")
    async with httpx.AsyncClient(transport=transport) as http:
        agent = Agent(ResponsesLLM(FixedToken(), "test-model", http), ToolRegistry([]), "kural")
        task = asyncio.create_task(cli.run_turn(agent, "selam", log))
        try:
            await asyncio.wait_for(upstream.waiting.wait(), timeout=2)
            assert not task.done()
            assert "Mer" in output.getvalue(), "İlk parça tamamlanmayı beklemeden görünmeli"
            assert "Ajan" in output.getvalue()
            assert "HIDDEN_ARGUMENTS" not in output.getvalue()
            assert "HIDDEN_REASONING" not in output.getvalue()
        finally:
            upstream.release.set()
            await asyncio.wait_for(task, timeout=2)
    shown = output.getvalue()
    assert shown.count("Merhaba 🌱") == 1
    assert upstream.closed
    events = read_session(log.path)
    replies = [event for event in events if isinstance(event, ModelReplied)]
    assert len(replies) == 1 and replies[0].text == "Merhaba 🌱"
    assert replies[0].usage is not None and replies[0].usage.output_tokens == 3
    assert isinstance(events[-1], TaskFinished) and events[-1].reason == "done"
    assert [e.text for e in events if isinstance(e, ModelTextDelta)] == ["Mer", "haba 🌱"]
    (report,) = tmp_path.glob("*.html")
    html = report.read_text(encoding="utf-8")
    assert html.count("Merhaba 🌱") == 1
    assert "Kısmi model metni" not in html


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failure_or_cancellation_after_first_chunk_closes_http_and_keeps_partial_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=80, color_system=None))
    upstream = GatedResponse(fail=True)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=upstream))
    log = SessionLogger(tmp_path / "partial.jsonl")
    async with httpx.AsyncClient(transport=transport) as http:
        agent = Agent(ResponsesLLM(FixedToken(), "test-model", http), ToolRegistry([]), "kural")
        task = asyncio.create_task(cli.run_turn(agent, "soru", log))
        await asyncio.wait_for(upstream.waiting.wait(), timeout=2)
        if cancel:
            task.cancel()
        else:
            upstream.release.set()
        with pytest.raises(asyncio.CancelledError if cancel else ResponsesError):
            await asyncio.wait_for(task, timeout=2)
    assert upstream.closed
    assert "Mer" in output.getvalue() and "Yanıt tamamlanmadı." in output.getvalue()
    events = read_session(log.path)
    assert isinstance(events[-1], TaskFinished)
    assert events[-1].reason == ("cancelled" if cancel else "error")
    assert not any(isinstance(e, ModelReplied) for e in events)
    assert not any(isinstance(message, AssistantMessage) for message in agent.history)
    assert [e.text for e in events if isinstance(e, ModelTextDelta)] == ["Mer"]
    (report,) = tmp_path.glob("*.html")
    html = report.read_text(encoding="utf-8")
    assert "Kısmi model metni" in html and "<pre>Mer</pre>" in html
    assert "Tamamlandı</dd>" not in html


@pytest.mark.anyio
async def test_streamed_tool_turn_hides_raw_arguments_results_and_reasoning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=100, color_system=None))
    (tmp_path / "secret-file.txt").write_text("HIDDEN_RESULT", encoding="utf-8")
    function = {
        "type": "function_call",
        "call_id": "c1",
        "name": "read_file",
        "arguments": '{"path":"secret-file.txt"}',
    }
    first = b"".join(
        [
            sse("response.output_text.delta", delta="İnceliyorum."),
            sse(
                "response.output_item.done",
                item={
                    "type": "message",
                    "content": [{"type": "output_text", "text": "İnceliyorum."}],
                },
            ),
            sse("response.function_call_arguments.delta", delta="HIDDEN_ARGUMENTS"),
            sse("response.output_item.done", item=function),
            sse("response.completed", response={"output": []}),
        ]
    )
    final = b"".join(
        [
            sse("response.reasoning_summary_text.delta", delta="HIDDEN_REASONING"),
            sse("response.output_text.delta", delta="Son "),
            sse("response.output_text.delta", delta="yanıt."),
            sse(
                "response.completed",
                response={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "Son yanıt."}],
                        }
                    ]
                },
            ),
        ]
    )
    third = sse(
        "response.completed",
        response={
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Hatırlıyorum."}]}
            ]
        },
    )
    bodies = iter([first, final, third])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=next(bodies))

    log = SessionLogger(tmp_path / "tools.jsonl")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        agent = Agent(
            ResponsesLLM(FixedToken(), "m", http), ToolRegistry([ReadFile(root=tmp_path)]), "kural"
        )
        await cli.run_turn(agent, "incele", log)
        await cli.run_turn(agent, "hatırla", log)
    shown = output.getvalue()
    for secret in ["secret-file.txt", "HIDDEN_RESULT", "HIDDEN_ARGUMENTS", "HIDDEN_REASONING"]:
        assert secret not in shown
    assert shown.count("İnceliyorum.") == 1
    assert shown.count("Son yanıt.") == 1 and shown.count("Hatırlıyorum.") == 1
    history = json.loads(seen[-1].content)["input"]
    assert any(item.get("content") == "hatırla" for item in history)
    assert any(item.get("output", "").endswith("HIDDEN_RESULT") for item in history)
    assert any(item.get("content") == "Son yanıt." for item in history)
    assert len([e for e in read_session(log.path) if isinstance(e, ModelReplied)]) == 3


@pytest.mark.anyio
async def test_consumer_closing_agent_stream_closes_http_before_completion() -> None:
    upstream = GatedResponse()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=upstream))
    async with httpx.AsyncClient(transport=transport) as http:
        agent = Agent(ResponsesLLM(FixedToken(), "m", http), ToolRegistry([]), "kural")
        events = agent.run("soru")
        async for event in events:
            if isinstance(event, ModelTextDelta):
                break
        await events.aclose()
    assert upstream.closed
    assert not any(isinstance(message, AssistantMessage) for message in agent.history)


@pytest.mark.anyio
async def test_tool_call_is_not_executed_if_http_ends_before_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=80, color_system=None))
    body = sse("response.output_text.delta", delta="Kısmi yanıt")
    body += sse(
        "response.output_item.done",
        item={
            "type": "function_call",
            "call_id": "c1",
            "name": "read_file",
            "arguments": '{"path":"must-not-read.txt"}',
        },
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    log = SessionLogger(tmp_path / "cut.jsonl")
    async with httpx.AsyncClient(transport=transport) as http:
        agent = Agent(
            ResponsesLLM(FixedToken(), "m", http), ToolRegistry([ReadFile(root=tmp_path)]), "kural"
        )
        with pytest.raises(ResponsesError, match="tamamlanmadan"):
            await cli.run_turn(agent, "soru", log)
    events = read_session(log.path)
    assert not any(e.kind in {"tool_started", "tool_finished", "model_replied"} for e in events)
    assert "Kısmi yanıt" in output.getvalue() and "Yanıt tamamlanmadı." in output.getvalue()
    assert "must-not-read" not in output.getvalue()


@pytest.mark.anyio
@pytest.mark.parametrize("width", [24, 100])
async def test_markup_is_literal_and_missing_final_delta_is_rendered_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
) -> None:
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=width, color_system=None))
    body = b"".join(
        [
            sse("response.output_text.delta", delta="[bold]"),
            sse("response.output_text.delta", delta="Türkçe[/bold]"),
            sse(
                "response.completed",
                response={
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "[bold]Türkçe[/bold] tamam."}
                            ],
                        }
                    ]
                },
            ),
        ]
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    async with httpx.AsyncClient(transport=transport) as http:
        agent = Agent(ResponsesLLM(FixedToken(), "m", http), ToolRegistry([]), "kural")
        await cli.run_turn(agent, "soru", SessionLogger(tmp_path / "literal.jsonl"))
    assert output.getvalue().count("[bold]Türkçe[/bold] tamam.") == 1
    assert output.getvalue().count("Ajan") == 1
