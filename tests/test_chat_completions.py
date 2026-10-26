"""Chat Completions adaptörü: doğru istek gövdesi, doğru mesaj, hatada anlaşılır hata."""

import json
from pathlib import Path

import httpx
import pytest

from harness.core.agent import Agent
from harness.core.events import TaskFinished
from harness.core.permissions import PermissionManager
from harness.llm.base import LLMClient
from harness.llm.chat_completions import ChatCompletionsError, ChatCompletionsLLM, build_request
from harness.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)
from harness.tools.base import ToolRegistry

pytestmark = pytest.mark.anyio


def fake_api(body: str, status: int = 200, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_system_and_user_messages_keep_their_roles() -> None:
    body = build_request("m", [SystemMessage(text="kural"), UserMessage(text="soru")])

    assert body["messages"] == [
        {"role": "system", "content": "kural"},
        {"role": "user", "content": "soru"},
    ]


def test_tool_call_and_result_become_chat_completions_shape() -> None:
    call = ToolCall(id="c1", name="read_file", arguments='{"path": "a"}')
    body = build_request(
        "m",
        [AssistantMessage(tool_calls=(call,)), ToolResult(tool_call_id="c1", content="içerik")],
    )

    assert body["messages"] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "içerik"},
    ]


def test_tools_are_wrapped_as_function_type() -> None:
    spec = ToolSpec(name="read_file", description="dosya okur", parameters={"type": "object"})
    body = build_request("m", [UserMessage(text="x")], [spec])

    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "dosya okur",
                "parameters": {"type": "object"},
            },
        }
    ]


def test_no_tools_means_no_tools_key() -> None:
    body = build_request("m", [UserMessage(text="x")])
    assert "tools" not in body


async def test_reply_without_tool_calls() -> None:
    payload = json.dumps(
        {
            "choices": [{"message": {"role": "assistant", "content": "Merhaba"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        }
    )
    async with fake_api(payload) as http:
        reply = await ChatCompletionsLLM("http://localhost:11434/v1", "m", http).complete(
            [UserMessage(text="selam")]
        )

    assert reply.text == "Merhaba"
    assert reply.tool_calls == ()
    assert reply.usage is not None
    assert reply.usage.input_tokens == 10 and reply.usage.output_tokens == 3


async def test_reply_with_tool_calls() -> None:
    payload = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": '{"path":"a"}'},
                            }
                        ],
                    }
                }
            ]
        }
    )
    async with fake_api(payload) as http:
        reply = await ChatCompletionsLLM("http://localhost:11434/v1", "m", http).complete(
            [UserMessage(text="a dosyasını oku")]
        )

    assert reply.text == ""
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0] == ToolCall(id="c1", name="read_file", arguments='{"path":"a"}')


async def test_api_key_becomes_bearer_header_when_given() -> None:
    seen: list[httpx.Request] = []
    payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    async with fake_api(payload, seen=seen) as http:
        await ChatCompletionsLLM("http://x/v1", "m", http, api_key="gizli").complete(
            [UserMessage(text="x")]
        )

    assert seen[0].headers["authorization"] == "Bearer gizli"


async def test_no_api_key_means_no_authorization_header() -> None:
    seen: list[httpx.Request] = []
    payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
    async with fake_api(payload, seen=seen) as http:
        await ChatCompletionsLLM("http://x/v1", "m", http).complete([UserMessage(text="x")])

    assert "authorization" not in seen[0].headers


async def test_http_error_is_reported() -> None:
    async with fake_api('{"error": "unauthorized"}', status=401) as http:
        with pytest.raises(ChatCompletionsError, match="401"):
            await ChatCompletionsLLM("http://x/v1", "m", http).complete([UserMessage(text="x")])


async def test_missing_choices_is_reported() -> None:
    async with fake_api("{}") as http:
        with pytest.raises(ChatCompletionsError, match="choices"):
            await ChatCompletionsLLM("http://x/v1", "m", http).complete([UserMessage(text="x")])


async def test_real_adapter_plugs_into_the_same_loop(tmp_path: Path) -> None:
    # Döngü değişmeden, sahte model yerine Chat Completions adaptörüyle çalışır.
    payload = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": "Python 3.12"}}]}
    )
    async with fake_api(payload) as http:
        llm: LLMClient = ChatCompletionsLLM("http://localhost:11434/v1", "m", http)
        tools = ToolRegistry([])
        agent = Agent(
            llm, tools, system_prompt="kural", permissions=PermissionManager(tmp_path, tools)
        )
        events = [event async for event in agent.run("sürüm?")]

    assert isinstance(events[-1], TaskFinished)
    assert events[-1].reason == "done" and events[-1].steps == 1
