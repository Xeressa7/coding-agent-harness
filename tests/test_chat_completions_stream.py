"""Chat Completions akışı: parçalar birleşir, sayımlar okunur, düşünce kaynağına döner."""

import json
from typing import Any

import httpx
import pytest

from harness.llm.base import StreamingLLMClient
from harness.llm.chat_completions import (
    ChatCompletionsError,
    StreamingChatCompletionsLLM,
    build_request,
    list_chat_models,
    parse_response,
)
from harness.messages import AssistantMessage, ReasoningDelta, TextDelta, ToolCall, UserMessage

pytestmark = pytest.mark.anyio

URL = "https://api.example.com/v1"


def sse(*chunks: dict[str, Any]) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def delta(**fields: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": fields}]}


def api(body: str, seen: list[httpx.Request] | None = None, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_stream_joins_text_tool_call_pieces_reasoning_and_usage() -> None:
    body = sse(
        delta(reasoning_content="Dosyayı "),
        delta(reasoning_content="okumalıyım."),
        delta(content="Bakı"),
        delta(content="yorum."),
        delta(
            tool_calls=[{"index": 0, "id": "c1", "function": {"name": "read_", "arguments": ""}}]
        ),
        delta(tool_calls=[{"index": 0, "function": {"name": "file", "arguments": '{"pa'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'th": "a"}'}}]),
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 64},
                "completion_tokens_details": {"reasoning_tokens": 12},
            },
        },
    )
    seen: list[httpx.Request] = []
    async with api(body, seen) as http:
        llm = StreamingChatCompletionsLLM(URL, "m", http, "anahtar", effort="high")
        assert isinstance(llm, StreamingLLMClient)
        parts = [p async for p in llm.stream([UserMessage(text="oku")])]

    assert parts[:-1] == [
        ReasoningDelta(text="Dosyayı "),
        ReasoningDelta(text="okumalıyım."),
        TextDelta(text="Bakı"),
        TextDelta(text="yorum."),
    ]
    reply = parts[-1]
    assert isinstance(reply, AssistantMessage)
    assert reply.text == "Bakıyorum."
    assert reply.tool_calls == (ToolCall(id="c1", name="read_file", arguments='{"path": "a"}'),)
    assert reply.reasoning == "Dosyayı okumalıyım."
    assert reply.usage is not None
    assert (reply.usage.input_tokens, reply.usage.cached_tokens) == (100, 64)
    assert (reply.usage.output_tokens, reply.usage.reasoning_tokens) == (20, 12)
    assert reply.first_text_seconds is not None
    sent = json.loads(seen[0].content)
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    assert sent["reasoning_effort"] == "high"
    assert seen[0].headers["authorization"] == "Bearer anahtar"


async def test_reasoning_goes_back_only_to_the_endpoint_that_wrote_it() -> None:
    payload = {"choices": [{"message": {"content": "tamam", "reasoning_content": "düşündüm"}}]}
    reply = parse_response(payload, source=URL)
    history = [UserMessage(text="x"), reply, UserMessage(text="y")]

    same = build_request("m", history, source=URL)["messages"][1]
    other = build_request("m", history, source="http://localhost:11434/v1")["messages"][1]

    assert same["reasoning_content"] == "düşündüm"
    assert "reasoning_content" not in other


def test_effort_style_follows_the_provider() -> None:
    history = [UserMessage(text="x")]
    assert build_request("m", history, effort="low")["reasoning_effort"] == "low"
    assert build_request("m", history, effort="low", effort_style="reasoning_object")[
        "reasoning"
    ] == {"effort": "low"}
    plain = build_request("m", history, effort="low", effort_style="none")
    assert "reasoning" not in plain and "reasoning_effort" not in plain
    assert "reasoning_effort" not in build_request("m", history)


def test_deepseek_cache_hit_field_is_read_as_cached_tokens() -> None:
    payload = {
        "choices": [{"message": {"content": "x"}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 5, "prompt_cache_hit_tokens": 40},
    }
    usage = parse_response(payload).usage
    assert usage is not None and usage.cached_tokens == 40


async def test_stream_http_error_and_empty_stream_are_errors() -> None:
    async with api('{"error": "bakiye yok"}', status=402) as http:
        with pytest.raises(ChatCompletionsError, match="402"):
            await StreamingChatCompletionsLLM(URL, "m", http).complete([UserMessage(text="x")])
    async with api("") as http:
        with pytest.raises(ChatCompletionsError, match="cevap gelmeden"):
            await StreamingChatCompletionsLLM(URL, "m", http).complete([UserMessage(text="x")])


async def test_model_list_uses_declared_efforts_or_provider_defaults() -> None:
    body = json.dumps(
        {
            "data": [
                {
                    "id": "deepseek-flash",
                    "name": "DeepSeek Flash",
                    "effort": {"supported_levels": ["low", "max"], "default_level": "low"},
                },
                {"id": "plain"},
            ]
        }
    )
    async with api(body) as http:
        models = await list_chat_models(URL, http, efforts=("medium",))

    assert [(m.slug, [e.name for e in m.efforts], m.default_effort) for m in models] == [
        ("deepseek-flash", ["low", "max"], "low"),
        ("plain", ["medium"], None),
    ]


async def test_provider_error_message_and_unreachable_server_are_readable() -> None:
    body = json.dumps({"error": {"message": "Insufficient Balance", "type": "x"}})
    async with api(body, status=402) as http:
        with pytest.raises(ChatCompletionsError) as caught:
            await StreamingChatCompletionsLLM(URL, "m", http).complete([UserMessage(text="x")])
    assert str(caught.value) == "HTTP 402: Insufficient Balance"

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("All connection attempts failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        with pytest.raises(ChatCompletionsError, match="localhost:11434.*çalışıyor mu"):
            await StreamingChatCompletionsLLM("http://localhost:11434/v1", "m", http).complete(
                [UserMessage(text="x")]
            )
