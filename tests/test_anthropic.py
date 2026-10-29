"""Anthropic adaptörü: geçmiş Messages biçimine doğru çevrilir, akış bizim tiplerimize döner."""

import json
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic

from harness.llm.anthropic_messages import (
    FALLBACK_BETA,
    AnthropicError,
    AnthropicLLM,
    build_request,
)
from harness.messages import (
    AssistantMessage,
    Message,
    ReasoningDelta,
    SystemMessage,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)

pytestmark = pytest.mark.anyio

TOOL = ToolSpec(name="read_file", description="okur", parameters={"type": "object"})


def sse(*events: dict[str, Any]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def message_start(input_tokens: int = 10, cache_read: int = 0) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 1,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": 0,
            },
        },
    }


def block(index: int, start: dict[str, Any], *deltas: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "content_block_start", "index": index, "content_block": start},
        *({"type": "content_block_delta", "index": index, "delta": d} for d in deltas),
        {"type": "content_block_stop", "index": index},
    ]


def finish(stop_reason: str, output_tokens: int = 30, **extra: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None, **extra},
            "usage": {"output_tokens": output_tokens},
        },
        {"type": "message_stop"},
    ]


TOOL_TURN = sse(
    message_start(input_tokens=10, cache_read=90),
    *block(
        0,
        {"type": "thinking", "thinking": "", "signature": ""},
        {"type": "thinking_delta", "thinking": "Önce dosyaya bakmalıyım."},
        {"type": "signature_delta", "signature": "imza-1"},
    ),
    *block(1, {"type": "text", "text": ""}, {"type": "text_delta", "text": "Okuyorum."}),
    *block(
        2,
        {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {}},
        {"type": "input_json_delta", "partial_json": '{"path": "a.txt"}'},
    ),
    *finish("tool_use"),
)


def client_for(body: str, seen: list[httpx2.Request] | None = None) -> AsyncAnthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})

    return AsyncAnthropic(
        api_key="test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


async def collect(
    llm: AnthropicLLM, history: list[Message]
) -> list[TextDelta | ReasoningDelta | AssistantMessage]:
    return [part async for part in llm.stream(history, [TOOL])]


async def test_stream_becomes_text_tool_call_reasoning_and_total_usage() -> None:
    seen: list[httpx2.Request] = []
    async with client_for(TOOL_TURN, seen) as client:
        parts = await collect(
            AnthropicLLM(client, "claude-opus-5-5", "high"), [UserMessage(text="oku")]
        )

    # Düşünce de canlı akar (soluk gösterilir); tamamı yine reply.reasoning'dedir.
    assert parts[:-1] == [
        ReasoningDelta(text="Önce dosyaya bakmalıyım."),
        TextDelta(text="Okuyorum."),
    ]
    reply = parts[-1]
    assert isinstance(reply, AssistantMessage)
    assert reply.text == "Okuyorum."
    assert reply.tool_calls == (
        ToolCall(id="tu_1", name="read_file", arguments='{"path": "a.txt"}'),
    )
    assert reply.reasoning == "Önce dosyaya bakmalıyım."
    assert reply.first_text_seconds is not None
    # Önbellekten okunan girdi toplam girdiye katılır, ayrıca alt küme olarak yazılır.
    assert reply.usage is not None
    assert (reply.usage.input_tokens, reply.usage.cached_tokens) == (100, 90)
    assert reply.usage.output_tokens == 30

    sent = json.loads(seen[0].content)
    assert sent["output_config"] == {"effort": "high"}
    assert sent["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert sent["fallbacks"] == "default"
    assert FALLBACK_BETA in seen[0].headers["anthropic-beta"]


async def test_reply_blocks_are_sent_back_unchanged_with_signed_thinking() -> None:
    async with client_for(TOOL_TURN) as client:
        parts = await collect(AnthropicLLM(client, "claude-opus-5-5"), [UserMessage(text="oku")])
    reply = parts[-1]
    assert isinstance(reply, AssistantMessage)
    history: list[Message] = [
        UserMessage(text="oku"),
        reply,
        ToolResult(tool_call_id="tu_1", content="merhaba"),
    ]

    request = build_request("claude-opus-5-5", history)

    assistant = request["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {
        "type": "thinking",
        "thinking": "Önce dosyaya bakmalıyım.",
        "signature": "imza-1",
    }
    assert assistant["content"][2] == {
        "type": "tool_use",
        "id": "tu_1",
        "name": "read_file",
        "input": {"path": "a.txt"},
    }


def test_system_goes_to_system_and_parallel_results_share_one_user_message() -> None:
    calls = (
        ToolCall(id="a", name="read_file", arguments='{"path": "x"}'),
        ToolCall(id="b", name="read_file", arguments='{"path": "y"}'),
    )
    history: list[Message] = [
        SystemMessage(text="talimat"),
        UserMessage(text="iki dosya"),
        AssistantMessage(text="", tool_calls=calls),  # başka sağlayıcıdan gelmiş mesaj
        ToolResult(tool_call_id="a", content="X"),
        ToolResult(tool_call_id="b", content="", is_error=True),
        UserMessage(text="devam"),
    ]

    request = build_request("claude-haiku-4-5", history, [TOOL])

    assert request["system"] == "talimat"
    assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user", "user"]
    assert [b["tool_use_id"] for b in request["messages"][2]["content"]] == ["a", "b"]
    assert request["messages"][2]["content"][1]["content"] == "(boş)"
    assert request["messages"][1]["content"][0]["input"] == {"path": "x"}
    assert request["tools"][0]["input_schema"] == {"type": "object"}
    # Eski modelde uyarlanır düşünce ve sunucu yedeği gönderilmez.
    assert "thinking" not in request and "fallbacks" not in request and "betas" not in request


async def test_refusal_is_an_error_not_an_answer() -> None:
    body = sse(
        message_start(),
        *block(0, {"type": "text", "text": ""}, {"type": "text_delta", "text": "Yap"}),
        *finish("refusal"),
    )
    async with client_for(body) as client:
        with pytest.raises(AnthropicError, match="reddetti"):
            await AnthropicLLM(client, "claude-opus-5-5").complete([UserMessage(text="x")])


async def test_tool_input_cut_by_max_tokens_is_not_run() -> None:
    body = sse(
        message_start(),
        *block(
            0,
            {"type": "tool_use", "id": "t", "name": "read_file", "input": {}},
            {"type": "input_json_delta", "partial_json": '{"path": "a'},
        ),
        *finish("max_tokens"),
    )
    async with client_for(body) as client:
        with pytest.raises(AnthropicError):
            await AnthropicLLM(client, "claude-opus-5-5").complete([UserMessage(text="x")])


async def test_http_error_is_reported_readably() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            401, json={"type": "error", "error": {"message": "invalid x-api-key"}}
        )

    client = AsyncAnthropic(
        api_key="bad",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    async with client:
        with pytest.raises(AnthropicError, match="401"):
            await AnthropicLLM(client, "claude-opus-5-5").complete([UserMessage(text="x")])
