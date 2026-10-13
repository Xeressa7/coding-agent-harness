"""OpenAI Responses API adaptörü (ChatGPT planı üzerinden).

LLMClient sözleşmesine uyar: mesaj geçmişini Responses biçimine çevirir,
akış (streaming) olarak gelen cevabı okur ve messages.py tiplerine geri
çevirir. Çekirdek, karşısında sahte model yerine bunun olduğunu bilmez.

ChatGPT planıyla kullanımın kuralları (developers.openai.com/siwc):
`store: false` ve `stream: true` zorunlu; sistem mesajı `input` içinde
gönderilemez, `instructions` alanına yazılır; cevap ancak
`response.completed` olayı geldiyse tamamdır.
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from harness.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)

API_URL = "https://api.openai.com/v1"

# ChatGPT planıyla kullanımda fonksiyon araçları bir "namespace" içinde verilmeli.
TOOL_NAMESPACE = "harness"


# "ultra" listede görünür ama API reddeder: Codex'e özgü otomatik görev devri (2026-10-13).
_UNSUPPORTED_EFFORTS = {"ultra"}


class Effort(BaseModel):
    """Modelin ne kadar düşüneceği: seviye adı ve OpenAI'ın açıklaması."""

    name: str
    description: str


class ModelInfo(BaseModel):
    """Hesapta kullanılabilen bir model ve desteklediği effort seviyeleri."""

    slug: str
    display_name: str
    description: str
    default_effort: str | None
    efforts: list[Effort]


class ResponsesError(Exception):
    """Model isteği başarısız; mesaj kullanıcıya gösterilebilir."""


class TokenSource(Protocol):
    """Her istekte geçerli bir erişim belirteci verebilen nesne (bkz. chatgpt_auth.py)."""

    async def access_token(self) -> str: ...


class ResponsesLLM:
    """Responses API ile konuşan model adaptörü."""

    def __init__(
        self,
        tokens: TokenSource,
        model: str,
        http: httpx.AsyncClient,
        effort: str | None = None,
    ) -> None:
        self._tokens = tokens
        self._model = model
        self._http = http
        self._effort = effort

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        body = build_request(self._model, history, tools, self._effort)
        headers = {"Authorization": f"Bearer {await self._tokens.access_token()}"}
        async with self._http.stream(
            "POST", f"{API_URL}/responses", json=body, headers=headers
        ) as response:
            if response.status_code != 200:
                await response.aread()
                raise ResponsesError(f"HTTP {response.status_code}: {response.text[:500]}")
            # store: false iken response.completed'ın `output`'u boş gelir (2026-10-13
            # gözlemi); cevabın parçaları akış sırasında output_item.done ile gelir.
            items: list[dict[str, Any]] = []
            async for event in _events(response):
                kind = event.get("type")
                if kind == "response.output_item.done":
                    items.append(event["item"])
                if kind == "response.completed":
                    return parse_output(items or event["response"].get("output", []))
                if kind == "response.failed":
                    error: dict[str, Any] = event["response"].get("error") or {}
                    raise ResponsesError(f"Model hata verdi: {error.get('code')}: {error}")
                if kind == "response.incomplete":
                    details = event["response"].get("incomplete_details")
                    raise ResponsesError(f"Cevap yarım kaldı: {details}")
                if kind == "error":
                    raise ResponsesError(f"Akış hatası: {event}")
        raise ResponsesError("Akış, cevap tamamlanmadan kesildi.")


def build_request(
    model: str,
    history: Sequence[Message],
    tools: Sequence[ToolSpec] = (),
    effort: str | None = None,
) -> dict[str, Any]:
    """messages.py geçmişini Responses API istek gövdesine çevirir."""
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in history:
        match message:
            case SystemMessage():
                instructions.append(message.text)
            case UserMessage():
                items.append({"role": "user", "content": message.text})
            case AssistantMessage():
                if message.text:
                    items.append({"role": "assistant", "content": message.text})
                for call in message.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call.id,
                            "namespace": TOOL_NAMESPACE,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                    )
            case ToolResult():
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
    body: dict[str, Any] = {"model": model, "input": items, "store": False, "stream": True}
    if instructions:
        body["instructions"] = "\n\n".join(instructions)
    if effort:
        # Verilmezse modelin kendi varsayılanı kullanılır.
        body["reasoning"] = {"effort": effort}
    if tools:
        body["tools"] = [
            {
                "type": "namespace",
                "name": TOOL_NAMESPACE,
                "description": "Harness'in çalışma dizini üzerinde kullanılan araçları",
                "tools": [
                    {
                        "type": "function",
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    }
                    for tool in tools
                ],
            }
        ]
    return body


def parse_output(output: list[dict[str, Any]]) -> AssistantMessage:
    """Tamamlanan cevabın çıktı parçalarını tek bir AssistantMessage'a çevirir."""
    texts: list[str] = []
    calls: list[ToolCall] = []
    for item in output:
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    texts.append(part["text"])
        elif item.get("type") == "function_call":
            calls.append(
                ToolCall(id=item["call_id"], name=item["name"], arguments=item["arguments"])
            )
        # "reasoning" gibi diğer parçalar şimdilik atlanır.
    return AssistantMessage(text="".join(texts), tool_calls=tuple(calls))


async def _events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Sunucunun gönderdiği olay akışını (SSE) satır satır okuyup JSON olarak verir."""
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data and data != "[DONE]":
            yield json.loads(data)


async def list_models(tokens: TokenSource, http: httpx.AsyncClient) -> list[ModelInfo]:
    """Giriş yapan hesabın kullanabildiği modeller, OpenAI'ın verdiği sırayla."""
    headers = {"Authorization": f"Bearer {await tokens.access_token()}"}
    response = await http.get(f"{API_URL}/models", headers=headers)
    if response.status_code != 200:
        raise ResponsesError(f"HTTP {response.status_code}: {response.text[:500]}")
    payload: dict[str, Any] = response.json()
    entries: list[dict[str, Any]] = payload.get("data") or payload.get("models") or []
    return [_model_info(entry) for entry in entries if entry.get("visibility", "list") == "list"]


def _model_info(entry: dict[str, Any]) -> ModelInfo:
    slug = str(entry.get("slug") or entry.get("id"))
    levels: list[dict[str, Any]] = entry.get("supported_reasoning_levels") or []
    return ModelInfo(
        slug=slug,
        display_name=str(entry.get("display_name") or slug),
        description=str(entry.get("description") or ""),
        default_effort=entry.get("default_reasoning_level"),
        efforts=[
            Effort(name=level["effort"], description=level.get("description", ""))
            for level in levels
            if level["effort"] not in _UNSUPPORTED_EFFORTS
        ],
    )
