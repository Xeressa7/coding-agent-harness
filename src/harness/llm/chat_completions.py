"""OpenAI uyumlu Chat Completions adaptörü.

httpx ile doğrudan HTTP konuşur; modele giden ve modelden gelen JSON gizlenmez.
Aynı biçimi konuşan her sağlayıcıyla çalışır: OpenAI, OpenRouter, DeepSeek,
Ollama, LM Studio. Uç nokta, anahtar ve model adı sağlayıcı tanımından gelir
(llm/providers.py).

`ChatCompletionsLLM` akışsızdır (`complete()`); `StreamingChatCompletionsLLM`
aynı isteği akış olarak ister, metni parça parça verir ve ilk metne kadar geçen
süreyi ölçer.

Bazı sağlayıcılar düşünceyi açık metin olarak döner (DeepSeek `reasoning_content`,
OpenRouter `reasoning`). Bu metin `AssistantMessage.reasoning`'e yazılır; DeepSeek
araçlı turda onu geri ister, bu yüzden aynı uç noktaya geri gönderilir.
"""

import json
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from time import perf_counter
from typing import Any, Literal, cast

import httpx

from harness.llm.base import Effort, LLMError, ModelInfo
from harness.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
)

# Effort'un istekte nasıl yazıldığı sağlayıcıya göre değişir.
EffortStyle = Literal["reasoning_effort", "reasoning_object", "none"]


class ChatCompletionsError(LLMError):
    """Model isteği başarısız; mesaj kullanıcıya gösterilebilir."""


class ChatCompletionsLLM:
    """`{base_url}/chat/completions` ile konuşan model adaptörü."""

    def __init__(
        self,
        base_url: str,
        model: str,
        http: httpx.AsyncClient,
        api_key: str | None = None,
        effort: str | None = None,
        effort_style: EffortStyle = "reasoning_effort",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._http = http
        self._api_key = api_key
        self._effort = effort
        self._effort_style: EffortStyle = effort_style

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _body(self, history: Sequence[Message], tools: Sequence[ToolSpec]) -> dict[str, Any]:
        return build_request(
            self._model,
            history,
            tools,
            effort=self._effort,
            effort_style=self._effort_style,
            source=self._base_url,
        )

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        try:
            response = await self._http.post(
                f"{self._base_url}/chat/completions",
                json=self._body(history, tools),
                headers=self._headers(),
            )
        except httpx.TransportError as e:
            raise _unreachable(self._base_url, e) from e
        if response.status_code != 200:
            raise _http_error(response)
        return parse_response(response.json(), source=self._base_url)


class StreamingChatCompletionsLLM(ChatCompletionsLLM):
    """Aynı adaptör, cevabı akış (SSE) olarak okur."""

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        async for part in self.stream(history, tools):
            if isinstance(part, AssistantMessage):
                return part
        raise ChatCompletionsError("Akış, cevap tamamlanmadan kesildi.")

    async def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | AssistantMessage, None]:
        started = perf_counter()
        first_text_seconds: float | None = None
        body = self._body(history, tools)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        texts: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage: TokenUsage | None = None
        received = False
        url = f"{self._base_url}/chat/completions"
        try:
            async with self._http.stream(
                "POST", url, json=body, headers=self._headers()
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise _http_error(response)
                async for chunk in _events(response):
                    received = True
                    if chunk.get("error"):
                        raise ChatCompletionsError(f"Akış hatası: {chunk['error']}")
                    if chunk.get("usage"):
                        usage = _usage(chunk["usage"])
                    choices: list[dict[str, Any]] = chunk.get("choices") or []
                    for choice in choices:
                        delta: dict[str, Any] = choice.get("delta") or {}
                        if piece := _reasoning_of(delta):
                            reasoning.append(piece)
                        text: str = delta.get("content") or ""
                        if text:
                            if first_text_seconds is None:
                                first_text_seconds = perf_counter() - started
                            texts.append(text)
                            yield TextDelta(text=text)
                        raw_calls: list[dict[str, Any]] = delta.get("tool_calls") or []
                        for raw in raw_calls:
                            # Bir çağrı birkaç parçada gelir; `index` onları aynı çağrıda toplar.
                            index: int = raw.get("index", len(calls))
                            slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            function: dict[str, Any] = raw.get("function") or {}
                            slot["id"] = raw.get("id") or slot["id"]
                            slot["name"] += function.get("name") or ""
                            slot["arguments"] += function.get("arguments") or ""
        except httpx.TransportError as e:
            raise _unreachable(self._base_url, e) from e
        if not received:
            raise ChatCompletionsError("Akış, cevap gelmeden kapandı.")
        thought = "".join(reasoning)
        yield AssistantMessage(
            text="".join(texts),
            tool_calls=tuple(
                ToolCall(
                    id=c["id"] or f"call_{i}", name=c["name"], arguments=c["arguments"] or "{}"
                )
                for i, c in sorted(calls.items())
            ),
            usage=usage,
            first_text_seconds=first_text_seconds,
            reasoning=thought,
            provider_data=_echo(self._base_url, thought),
        )


def build_request(
    model: str,
    history: Sequence[Message],
    tools: Sequence[ToolSpec] = (),
    *,
    effort: str | None = None,
    effort_style: EffortStyle = "reasoning_effort",
    source: str | None = None,
) -> dict[str, Any]:
    """messages.py geçmişini Chat Completions istek gövdesine çevirir.

    `source`: bu isteğin gideceği uç nokta. Bir mesajın `provider_data`'sı yalnız
    aynı uç noktadan geldiyse geri gönderilir; başka sağlayıcı o alanı tanımayabilir.
    """
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
                data = message.provider_data or {}
                if source is not None and data.get("source") == source:
                    entry.update(data.get("fields") or {})
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
    if effort and effort_style == "reasoning_effort":
        body["reasoning_effort"] = effort
    elif effort and effort_style == "reasoning_object":
        body["reasoning"] = {"effort": effort}
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


def parse_response(payload: dict[str, Any], source: str | None = None) -> AssistantMessage:
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
    thought = _reasoning_of(message)
    return AssistantMessage(
        text=message.get("content") or "",
        tool_calls=tuple(calls),
        usage=_usage(raw_usage) if raw_usage is not None else None,
        reasoning=thought,
        provider_data=_echo(source, thought) if source else None,
    )


async def list_chat_models(
    base_url: str,
    http: httpx.AsyncClient,
    api_key: str | None = None,
    efforts: Sequence[str] = (),
) -> list[ModelInfo]:
    """`{base_url}/models`: OpenAI uyumlu sağlayıcıların ortak model listesi.

    Effort desteğini söyleyen sağlayıcı (ör. DeepSeek `effort.supported_levels`) kendi
    listesini verir; söylemeyende sağlayıcı tanımındaki liste her modele verilir.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = await http.get(f"{base_url.rstrip('/')}/models", headers=headers)
    if response.status_code != 200:
        raise ChatCompletionsError(f"HTTP {response.status_code}: {response.text[:500]}")
    entries: list[dict[str, Any]] = response.json().get("data") or []
    models: list[ModelInfo] = []
    for entry in entries:
        if not entry.get("id"):
            continue
        declared: dict[str, Any] = entry.get("effort") or {}
        levels: list[str] = declared.get("supported_levels") or list(efforts)
        models.append(
            ModelInfo(
                slug=str(entry["id"]),
                display_name=str(entry.get("name") or entry["id"]),
                description=str(entry.get("description") or "")[:120],
                default_effort=declared.get("default_level"),
                efforts=[Effort(name=name) for name in levels],
            )
        )
    return models


def _http_error(response: httpx.Response) -> ChatCompletionsError:
    """Sağlayıcının hata mesajı varsa onu, yoksa gövdenin başını gösterir."""
    message: object = None
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = cast(dict[str, object], body).get("error")
        message = (
            cast(dict[str, object], error).get("message") if isinstance(error, dict) else error
        )
    detail = str(message) if message else response.text[:500]
    return ChatCompletionsError(f"HTTP {response.status_code}: {detail}")


def _unreachable(base_url: str, error: httpx.TransportError) -> ChatCompletionsError:
    return ChatCompletionsError(
        f"{base_url} bağlantı hatası ({type(error).__name__}: {error}). Sunucu çalışıyor mu?"
    )


def _usage(raw: dict[str, Any]) -> TokenUsage:
    prompt_details: dict[str, Any] = raw.get("prompt_tokens_details") or {}
    completion_details: dict[str, Any] = raw.get("completion_tokens_details") or {}
    cached = prompt_details.get("cached_tokens")
    if cached is None:
        cached = raw.get("prompt_cache_hit_tokens")  # DeepSeek kendi adını kullanır
    return TokenUsage(
        input_tokens=raw.get("prompt_tokens"),
        output_tokens=raw.get("completion_tokens"),
        cached_tokens=cached,
        reasoning_tokens=completion_details.get("reasoning_tokens"),
    )


def _reasoning_of(part: dict[str, Any]) -> str:
    value = part.get("reasoning_content") or part.get("reasoning")
    return value if isinstance(value, str) else ""


def _echo(source: str | None, thought: str) -> dict[str, Any] | None:
    """DeepSeek araçlı turda düşünceyi geri ister; yalnız aynı uç noktaya gider."""
    if not source or not thought:
        return None
    return {"source": source, "fields": {"reasoning_content": thought}}


async def _events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data and data != "[DONE]":
            yield json.loads(data)
