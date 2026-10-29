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
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import aclosing
from time import perf_counter
from typing import Any, Protocol

import httpx

from harness.llm.base import Effort, LLMError, ModelInfo, error_message
from harness.messages import (
    AssistantMessage,
    Message,
    ReasoningDelta,
    SystemMessage,
    TextDelta,
    TokenUsage,
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


class ResponsesError(LLMError):
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
        *,
        base_url: str = API_URL,
        headers: Mapping[str, str] | None = None,
        namespace: str | None = TOOL_NAMESPACE,
    ) -> None:
        self._tokens = tokens
        self._model = model
        self._http = http
        self._effort = effort
        self._base_url = base_url.rstrip("/")
        self._extra_headers = dict(headers or {})
        self._namespace = namespace  # None: araçlar düz liste (ChatGPT planı dışı)

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        async with aclosing(self.stream(history, tools)) as parts:
            async for part in parts:
                if isinstance(part, AssistantMessage):
                    return part
        raise ResponsesError("Akış, cevap tamamlanmadan kesildi.")

    async def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | ReasoningDelta | AssistantMessage, None]:
        started = perf_counter()
        first_text_seconds: float | None = None
        body = build_request(self._model, history, tools, self._effort, self._namespace)
        headers = {
            **self._extra_headers,
            "Authorization": f"Bearer {await self._tokens.access_token()}",
        }
        async with self._http.stream(
            "POST", f"{self._base_url}/responses", json=body, headers=headers
        ) as response:
            if response.status_code != 200:
                await response.aread()
                raise _http_error(response)
            # store: false iken response.completed'ın `output`'u boş gelir (2026-10-13
            # gözlemi); cevabın parçaları akış sırasında output_item.done ile gelir.
            items: list[dict[str, Any]] = []
            summary_part: tuple[object, object] | None = None
            async for event in _events(response):
                kind = event.get("type")
                if kind == "response.output_item.done":
                    items.append(event["item"])
                if kind == "response.reasoning_summary_text.delta" and event.get("delta"):
                    # Özet birkaç paragraf (part) olarak gelir; tamamında da "\n\n" ile birleşir.
                    part = (event.get("item_id"), event.get("summary_index"))
                    gap = "\n\n" if summary_part is not None and part != summary_part else ""
                    summary_part = part
                    yield ReasoningDelta(text=gap + event["delta"])
                if kind == "response.output_text.delta" and event.get("delta"):
                    if first_text_seconds is None:
                        first_text_seconds = perf_counter() - started
                    yield TextDelta(text=event["delta"])
                if kind == "response.completed":
                    response_data = event["response"]
                    reply = parse_output(items or response_data.get("output", []))
                    raw_usage = response_data.get("usage")
                    usage = None
                    if raw_usage is not None:
                        input_details: dict[str, Any] = raw_usage.get("input_tokens_details") or {}
                        output_details: dict[str, Any] = (
                            raw_usage.get("output_tokens_details") or {}
                        )
                        usage = TokenUsage(
                            input_tokens=raw_usage.get("input_tokens"),
                            output_tokens=raw_usage.get("output_tokens"),
                            cached_tokens=input_details.get("cached_tokens"),
                            reasoning_tokens=output_details.get("reasoning_tokens"),
                        )
                    yield reply.model_copy(
                        update={
                            "usage": usage,
                            "first_text_seconds": first_text_seconds,
                        }
                    )
                    return
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
    namespace: str | None = TOOL_NAMESPACE,
) -> dict[str, Any]:
    """messages.py geçmişini Responses API istek gövdesine çevirir.

    ChatGPT planı araçları bir `namespace` içinde ister; düz Responses API'sinde
    (ör. OpenCode üzerinden) `namespace=None` ile araçlar düz liste olarak gider.
    """
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
                    item = {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                    items.append({**item, "namespace": namespace} if namespace else item)
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
    # Düşüncenin kendisi gizlidir; "summary" okunabilir bir özetini ister (zaman çizelgesinde
    # modelin neden o aracı çağırdığı buradan görünür). Effort verilmezse modelin varsayılanı.
    body["reasoning"] = {"summary": "auto", **({"effort": effort} if effort else {})}
    if tools:
        functions = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
        ]
        body["tools"] = (
            [
                {
                    "type": "namespace",
                    "name": namespace,
                    "description": "Harness'in çalışma dizini üzerinde kullanılan araçları",
                    "tools": functions,
                }
            ]
            if namespace
            else functions
        )
    return body


def parse_output(output: list[dict[str, Any]]) -> AssistantMessage:
    """Tamamlanan cevabın çıktı parçalarını tek bir AssistantMessage'a çevirir."""
    texts: list[str] = []
    calls: list[ToolCall] = []
    summaries: list[str] = []
    for item in output:
        if item.get("type") == "reasoning":
            parts: list[dict[str, Any]] = item.get("summary") or []
            summaries.extend(
                str(part["text"])
                for part in parts
                if part.get("type") == "summary_text" and part.get("text")
            )
        elif item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    texts.append(part["text"])
        elif item.get("type") == "function_call":
            calls.append(
                ToolCall(id=item["call_id"], name=item["name"], arguments=item["arguments"])
            )
    return AssistantMessage(
        text="".join(texts), tool_calls=tuple(calls), reasoning="\n\n".join(summaries)
    )


async def _events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Sunucunun gönderdiği olay akışını (SSE) satır satır okuyup JSON olarak verir."""
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data and data != "[DONE]":
            yield json.loads(data)


def _http_error(response: httpx.Response) -> ResponsesError:
    try:
        body: object = response.json()
    except ValueError:
        body = None
    detail = error_message(body) or response.text[:500]
    return ResponsesError(f"HTTP {response.status_code}: {detail}")


async def list_models(tokens: TokenSource, http: httpx.AsyncClient) -> list[ModelInfo]:
    """Giriş yapan hesabın kullanabildiği modeller, OpenAI'ın verdiği sırayla."""
    headers = {"Authorization": f"Bearer {await tokens.access_token()}"}
    response = await http.get(f"{API_URL}/models", headers=headers)
    if response.status_code != 200:
        raise _http_error(response)
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
