"""Anthropic Messages API adaptörü (resmi `anthropic` SDK'sı ile).

LLMClient ve StreamingLLMClient sözleşmelerine uyar. Geçmişi Messages biçimine
çevirir: sistem talimatı `system` alanına, araç sonuçları tek bir kullanıcı
mesajında `tool_result` bloklarına gider.

Claude'un düşünce blokları imzalıdır ve sonraki istekte değiştirilmeden geri
gönderilmelidir. Bu yüzden yanıtın blokları `provider_data`'da saklanır ve
geçmiş yeniden kurulurken aynen kullanılır. Düşüncenin okunabilir özeti
(`display: "summarized"`) `AssistantMessage.reasoning`'e yazılır.

Güncel modellerde `fallbacks: "default"` açıktır: güvenlik sınıflandırıcısı
isteği reddederse sunucu, önerilen başka bir modelle yeniden dener.
"""

import json
from collections.abc import AsyncGenerator, Sequence
from time import perf_counter
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

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

SOURCE = "anthropic"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_TOKENS = 64000  # akışta zaman aşımı sorun değil; düşük tutmak cevabı yarıda keser

# Sunucu tarafı yedek model: yalnız bu modellerde ve bu başlıkla.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

# Uyarlanır düşünce (adaptive thinking) bu ailelerde var; daha eskilerde gönderilmez.
_ADAPTIVE_PREFIXES = (
    "claude-fable",
    "claude-mythos",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
)

# Geri gönderilecek bloklarda tutulan alanlar; yanıttaki diğer alanlar istek biçiminde yok.
_BLOCK_FIELDS = {
    "text": ("type", "text"),
    "thinking": ("type", "thinking", "signature"),
    "redacted_thinking": ("type", "data"),
    "tool_use": ("type", "id", "name", "input"),
}


class AnthropicError(LLMError):
    """Model isteği başarısız; mesaj kullanıcıya gösterilebilir."""


class AnthropicLLM:
    """Messages API ile konuşan model adaptörü; her çağrı akışlıdır."""

    def __init__(
        self,
        client: AsyncAnthropic,
        model: str,
        effort: str | None = None,
        *,
        fallbacks: bool = True,
    ) -> None:
        self._client = client
        self._model = model
        self._effort = effort
        self._fallbacks = fallbacks  # aracı (ör. OpenCode) üzerinden giderken kapalı

    @property
    def client(self) -> AsyncAnthropic:
        return self._client

    @property
    def fallbacks(self) -> bool:
        return self._fallbacks

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        async for part in self.stream(history, tools):
            if isinstance(part, AssistantMessage):
                return part
        raise AnthropicError("Akış, cevap tamamlanmadan kesildi.")

    async def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | ReasoningDelta | AssistantMessage, None]:
        started = perf_counter()
        first_text_seconds: float | None = None
        request = build_request(
            self._model, history, tools, self._effort, fallbacks=self._fallbacks
        )
        try:
            async with self._client.beta.messages.stream(**request) as stream:
                async for event in stream:
                    if event.type == "text" and event.text:
                        if first_text_seconds is None:
                            first_text_seconds = perf_counter() - started
                        yield TextDelta(text=event.text)
                    elif event.type == "thinking" and event.thinking:
                        yield ReasoningDelta(text=event.thinking)
                final = await stream.get_final_message()
        except anthropic.APIStatusError as e:
            raise _status_error(e) from e
        except anthropic.APIConnectionError as e:
            raise AnthropicError(f"Bağlantı hatası: {e}") from e
        except ValueError as e:
            # Akışla gelen araç girdisi JSON olarak hiç çözülemedi.
            raise AnthropicError(f"Araç girdisi çözülemedi: {e}") from e
        blocks = [block.model_dump(mode="json", exclude_none=True) for block in final.content]
        reply = parse_content(blocks)
        if final.stop_reason == "refusal":
            details = final.stop_details
            category = getattr(details, "category", None) if details else None
            raise AnthropicError(f"Model isteği reddetti (kategori: {category or '—'}).")
        if final.stop_reason == "max_tokens" and reply.tool_calls:
            raise AnthropicError("Araç girdisi max_tokens sınırında kesildi; çalıştırılmadı.")
        usage = final.usage
        cache_read = usage.cache_read_input_tokens or 0
        cache_write = usage.cache_creation_input_tokens or 0
        yield reply.model_copy(
            update={
                "usage": TokenUsage(
                    # Anthropic önbellekten okunanı input_tokens'a katmaz; burada toplam girdi.
                    input_tokens=usage.input_tokens + cache_read + cache_write,
                    output_tokens=usage.output_tokens,
                    cached_tokens=cache_read,
                ),
                "first_text_seconds": first_text_seconds,
            }
        )


def build_request(
    model: str,
    history: Sequence[Message],
    tools: Sequence[ToolSpec] = (),
    effort: str | None = None,
    *,
    fallbacks: bool = True,
) -> dict[str, Any]:
    """messages.py geçmişini `beta.messages.stream` argümanlarına çevirir."""
    system: list[str] = []
    messages: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    def flush_results() -> None:
        # Paralel araç sonuçlarının hepsi tek kullanıcı mesajında döner.
        if results:
            messages.append({"role": "user", "content": list(results)})
            results.clear()

    for message in history:
        match message:
            case SystemMessage():
                system.append(message.text)
            case ToolResult():
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.content or "(boş)",
                        "is_error": message.is_error,
                    }
                )
            case UserMessage():
                flush_results()
                messages.append({"role": "user", "content": message.text})
            case AssistantMessage():
                flush_results()
                content = _assistant_content(message)
                if content:
                    messages.append({"role": "assistant", "content": content})
    flush_results()

    request: dict[str, Any] = {"model": model, "max_tokens": MAX_TOKENS, "messages": messages}
    if system:
        request["system"] = "\n\n".join(system)
    if model.startswith(_ADAPTIVE_PREFIXES):
        request["thinking"] = {"type": "adaptive", "display": "summarized"}
    if effort:
        request["output_config"] = {"effort": effort}
    if tools:
        request["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
                # Büyük girdiler (dosya içeriği) üretildikçe akar. Sunucu girdiyi doğrulamaz;
                # doğrulamayı ToolRegistry Pydantic ile yapar, kesik girdiyi stop_reason yakalar.
                "eager_input_streaming": True,
            }
            for tool in tools
        ]
    if fallbacks and model in _FALLBACK_MODELS:
        request["fallbacks"] = "default"
        request["betas"] = [FALLBACK_BETA]
    return request


def parse_content(blocks: list[dict[str, Any]]) -> AssistantMessage:
    """Yanıt bloklarını AssistantMessage'a çevirir; bloklar geri gönderilmek üzere saklanır."""
    texts: list[str] = []
    thoughts: list[str] = []
    calls: list[ToolCall] = []
    kept: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        fields = _BLOCK_FIELDS.get(str(kind))
        kept.append({k: block[k] for k in fields if k in block} if fields else block)
        if kind == "text":
            texts.append(block.get("text", ""))
        elif kind == "thinking" and block.get("thinking"):
            thoughts.append(block["thinking"])
        elif kind == "tool_use":
            calls.append(
                ToolCall(
                    id=block["id"],
                    name=block["name"],
                    arguments=json.dumps(block.get("input") or {}, ensure_ascii=False),
                )
            )
    return AssistantMessage(
        text="".join(texts),
        tool_calls=tuple(calls),
        reasoning="\n\n".join(thoughts),
        provider_data={"source": SOURCE, "content": kept},
    )


async def list_anthropic_models(client: AsyncAnthropic) -> list[ModelInfo]:
    """Hesabın erişebildiği Claude modelleri; effort seviyeleri bu uç noktadan gelmez."""
    try:
        return [
            ModelInfo(
                slug=model.id,
                display_name=model.display_name,
                efforts=[Effort(name=name) for name in EFFORTS],
            )
            async for model in client.models.list()
        ]
    except anthropic.APIStatusError as e:
        raise _status_error(e) from e
    except anthropic.APIConnectionError as e:
        raise AnthropicError(f"Bağlantı hatası: {e}") from e


def _assistant_content(message: AssistantMessage) -> list[dict[str, Any]]:
    data = message.provider_data or {}
    if data.get("source") == SOURCE and data.get("content"):
        # Claude'un kendi blokları: düşünce imzaları bozulmasın diye olduğu gibi.
        return list(data["content"])
    content: list[dict[str, Any]] = []
    if message.text:
        content.append({"type": "text", "text": message.text})
    for call in message.tool_calls:
        try:
            arguments: Any = json.loads(call.arguments)
        except json.JSONDecodeError:
            arguments = {}
        content.append(
            {
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": arguments if isinstance(arguments, dict) else {},
            }
        )
    return content


def _status_error(error: anthropic.APIStatusError) -> AnthropicError:
    return AnthropicError(
        f"HTTP {error.status_code}: {error_message(error.body) or error.message}",
        status=error.status_code,
    )
