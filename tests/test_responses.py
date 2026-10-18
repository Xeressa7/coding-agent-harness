"""Responses adaptörü: doğru istek gövdesi, akıştan doğru mesaj, hatada anlaşılır hata."""

import json
from typing import Any

import httpx
import pytest

from harness.core.agent import Agent
from harness.core.events import TaskFinished
from harness.llm.base import LLMClient
from harness.llm.responses import ResponsesError, ResponsesLLM, build_request
from harness.messages import (
    AssistantMessage,
    SystemMessage,
    TokenUsage,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)
from harness.tools.base import ToolRegistry

pytestmark = pytest.mark.anyio


class FixedToken:
    async def access_token(self) -> str:
        return "test-token"


def stream(*events: dict[str, Any]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def completed(*output: dict[str, Any]) -> dict[str, Any]:
    return {"type": "response.completed", "response": {"output": list(output)}}


def text_item(text: str) -> dict[str, Any]:
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def fake_api(body: str, status: int = 200, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_system_message_goes_to_instructions_not_input() -> None:
    body = build_request("m", [SystemMessage(text="kural"), UserMessage(text="soru")])

    assert body["instructions"] == "kural"
    assert body["input"] == [{"role": "user", "content": "soru"}]
    # ChatGPT planıyla kullanımın iki zorunlu ayarı.
    assert body["store"] is False
    assert body["stream"] is True


def test_tool_call_and_result_become_function_items() -> None:
    call = ToolCall(id="c1", name="read_file", arguments='{"path": "a"}')
    body = build_request(
        "m",
        [AssistantMessage(tool_calls=(call,)), ToolResult(tool_call_id="c1", content="içerik")],
    )

    assert body["input"] == [
        {
            "type": "function_call",
            "call_id": "c1",
            "namespace": "harness",
            "name": "read_file",
            "arguments": '{"path": "a"}',
        },
        {"type": "function_call_output", "call_id": "c1", "output": "içerik"},
    ]


def test_tools_are_offered_inside_a_namespace() -> None:
    spec = ToolSpec(name="read_file", description="okur", parameters={"type": "object"})

    body = build_request("m", [UserMessage(text="oku")], [spec])

    (namespace,) = body["tools"]
    assert namespace["type"] == "namespace"
    assert namespace["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "description": "okur",
            "parameters": {"type": "object"},
        }
    ]


def test_no_tools_means_no_tools_field() -> None:
    assert "tools" not in build_request("m", [UserMessage(text="selam")])


async def test_completed_usage_is_normalized_without_subsets_added() -> None:
    body = completed(text_item("ok"))
    body["response"]["usage"] = {
        "input_tokens": 120,
        "output_tokens": 30,
        "input_tokens_details": {"cached_tokens": 100},
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    async with fake_api(stream(body)) as http:
        reply = await ResponsesLLM(FixedToken(), "m", http).complete([UserMessage(text="x")])
    assert reply.usage == TokenUsage(
        input_tokens=120,
        output_tokens=30,
        cached_tokens=100,
        reasoning_tokens=0,
    )
    assert reply.first_text_seconds is None
    request = build_request("m", [reply])
    assert request["input"] == [{"role": "assistant", "content": "ok"}]
    assert "usage" not in str(request)


async def test_streamed_answer_becomes_assistant_message() -> None:
    seen: list[httpx.Request] = []
    body = stream(
        {"type": "response.output_text.delta", "delta": "Mer"},
        completed({"type": "reasoning"}, text_item("Merhaba")),
    )
    async with fake_api(body, seen=seen) as http:
        reply = await ResponsesLLM(FixedToken(), "m", http).complete([UserMessage(text="selam")])

    assert reply.text == "Merhaba" and not reply.tool_calls and reply.usage is None
    assert reply.first_text_seconds is not None and reply.first_text_seconds >= 0
    assert seen[0].headers["Authorization"] == "Bearer test-token"


async def test_answer_collected_from_items_when_completed_output_is_empty() -> None:
    # Gerçek API'de store: false iken böyle geliyor: parçalar akışta, son olayda boş liste.
    body = stream(
        {"type": "response.output_item.done", "item": {"type": "reasoning"}},
        {"type": "response.output_item.done", "item": text_item("Merhaba")},
        completed(),
    )
    async with fake_api(body) as http:
        reply = await ResponsesLLM(FixedToken(), "m", http).complete([UserMessage(text="selam")])

    assert reply == AssistantMessage(text="Merhaba")


async def test_function_call_in_output_becomes_tool_call() -> None:
    item = {"type": "function_call", "call_id": "c9", "name": "read_file", "arguments": "{}"}
    async with fake_api(stream(completed(item))) as http:
        reply = await ResponsesLLM(FixedToken(), "m", http).complete([UserMessage(text="oku")])

    assert reply.tool_calls == (ToolCall(id="c9", name="read_file", arguments="{}"),)


async def test_failed_response_reports_the_error_code() -> None:
    failed = {
        "type": "response.failed",
        "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
    }
    async with fake_api(stream(failed)) as http:
        with pytest.raises(ResponsesError, match="usage_limit_exceeded"):
            await ResponsesLLM(FixedToken(), "m", http).complete([UserMessage(text="x")])


async def test_stream_cut_before_completion_is_an_error() -> None:
    body = stream({"type": "response.output_text.delta", "delta": "yarım"})
    async with fake_api(body) as http:
        with pytest.raises(ResponsesError, match="tamamlanmadan"):
            await ResponsesLLM(FixedToken(), "m", http).complete([UserMessage(text="x")])


async def test_http_error_is_reported() -> None:
    async with fake_api('{"error": "unauthorized"}', status=401) as http:
        with pytest.raises(ResponsesError, match="401"):
            await ResponsesLLM(FixedToken(), "m", http).complete([UserMessage(text="x")])


async def test_real_adapter_plugs_into_the_same_loop() -> None:
    # Döngü değişmeden, sahte model yerine Responses adaptörüyle çalışır.
    async with fake_api(stream(completed(text_item("Python 3.12")))) as http:
        llm: LLMClient = ResponsesLLM(FixedToken(), "m", http)
        agent = Agent(llm, ToolRegistry([]), system_prompt="kural")
        events = [event async for event in agent.run("sürüm?")]

    assert isinstance(events[-1], TaskFinished)
    assert events[-1].reason == "done" and events[-1].steps == 1
