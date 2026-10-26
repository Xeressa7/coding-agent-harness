"""OpenAI uyumlu Chat Completions adaptörü.

httpx ile doğrudan HTTP konuşur; modele giden ve modelden gelen JSON gizlenmez.
Aynı biçimi konuşan her sağlayıcıyla çalışır: Ollama (yerel) ve OpenCode
Go'daki GLM, Kimi, DeepSeek modelleri. Uç nokta ve model adı yapılandırmadan
gelir. Akışsız: `complete()`'i gerçekler, StreamingLLMClient'ı değil.
"""

from collections.abc import Sequence
from typing import Any

import httpx

from harness.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    TokenUsage,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)


class ChatCompletionsError(Exception):
    """Model isteği başarısız; mesaj kullanıcıya gösterilebilir."""


class ChatCompletionsLLM:
    """`{base_url}/chat/completions` ile konuşan model adaptörü."""

    def __init__(
        self,
        base_url: str,
        model: str,
        http: httpx.AsyncClient,
        api_key: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._http = http
        self._api_key = api_key

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        body = build_request(self._model, history, tools)
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        response = await self._http.post(
            f"{self._base_url}/chat/completions", json=body, headers=headers
        )
        if response.status_code != 200:
            raise ChatCompletionsError(f"HTTP {response.status_code}: {response.text[:500]}")
        return parse_response(response.json())


def build_request(
    model: str, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
) -> dict[str, Any]:
    """messages.py geçmişini Chat Completions istek gövdesine çevirir."""
    messages: list[dict[str, Any]] = []
    for message in history:
        match message:
            case SystemMessage():
                messages.append({"role": "system", "content": message.text})
            case UserMessage():
                messages.append({"role": "user", "content": message.text})
            case AssistantMessage():
                entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in message.tool_calls
                    ]
                messages.append(entry)
            case ToolResult():
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content,
                    }
                )
    body: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
    return body


def parse_response(payload: dict[str, Any]) -> AssistantMessage:
    """Tamamlanan cevabı tek bir AssistantMessage'a çevirir."""
    choices: list[dict[str, Any]] = payload.get("choices") or []
    if not choices:
        raise ChatCompletionsError(f"Yanıtta 'choices' yok: {payload}")
    message: dict[str, Any] = choices[0].get("message") or {}
    raw_calls: list[dict[str, Any]] = message.get("tool_calls") or []
    calls = [
        ToolCall(
            id=call["id"],
            name=call["function"]["name"],
            arguments=call["function"]["arguments"],
        )
        for call in raw_calls
    ]
    raw_usage: dict[str, Any] | None = payload.get("usage")
    usage = None
    if raw_usage is not None:
        usage = TokenUsage(
            input_tokens=raw_usage.get("prompt_tokens"),
            output_tokens=raw_usage.get("completion_tokens"),
        )
    return AssistantMessage(text=message.get("content") or "", tool_calls=tuple(calls), usage=usage)
