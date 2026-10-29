"""OpenCode Zen ve Go: tek anahtarla birçok sağlayıcının modeli.

OpenCode her modeli kendi API biçimiyle sunar: GPT/Grok modelleri Responses,
Claude ve bazı Qwen/MiniMax modelleri Anthropic Messages, geri kalanı Chat
Completions ile konuşur. Hangi modelin hangi kapıdan gittiği models.dev
kataloğundan okunur (OpenCode'un kendisi de oradan okur). Katalog bir gün diskte
saklanır; ağ yoksa model adına bakan kaba kurallar kullanılır.

Üçüncü taraf istemciler kendini User-Agent ile tanıtmalı ve sabit bir oturum
kimliğini `x-opencode-session` başlığında göndermelidir (opencode.ai/docs/go).
"""

import json
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import aclosing
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

import httpx
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict

from harness.llm.anthropic_messages import AnthropicLLM
from harness.llm.base import Effort, LLMError, ModelInfo
from harness.llm.chat_completions import StreamingChatCompletionsLLM
from harness.llm.responses import ResponsesLLM
from harness.messages import (
    AssistantMessage,
    Message,
    ReasoningDelta,
    TextDelta,
    ToolSpec,
)

ZEN_URL = "https://opencode.ai/inference"
GO_URL = "https://opencode.ai/inference/go"
CATALOG_URL = "https://models.dev/api.json"
CATALOG_PATH = Path.home() / ".harness" / "cache" / "opencode-models.json"
CATALOG_MAX_AGE = 24 * 3600  # saniye

Route = Literal["responses", "messages", "chat", "google"]
# Model listesinde kısa ad: Responses, Anthropic Messages, Chat Completions, Gemini.
ROUTE_LABELS: dict[Route, str] = {
    "responses": "Responses",
    "messages": "Messages",
    "chat": "Chat",
    "google": "Gemini",
}
# models.dev her modelin hangi AI SDK paketiyle konuşulacağını yazar; paket = API biçimi.
_PACKAGE_ROUTES: dict[str, Route] = {
    "@ai-sdk/openai": "responses",
    "@ai-sdk/anthropic": "messages",
    "@ai-sdk/google": "google",
}


class OpenCodeError(LLMError):
    """OpenCode isteği kurulamadı ya da başarısız oldu."""


class CatalogModel(BaseModel):
    """Katalogdaki bir model: adı, API biçimi, effort seviyeleri, ücretsiz mi."""

    model_config = ConfigDict(frozen=True)

    name: str
    route: Route
    efforts: tuple[str, ...] = ()
    free: bool = False


Catalog = dict[str, CatalogModel]
CatalogLoader = Callable[[], Awaitable[Catalog]]


def user_agent() -> str:
    try:
        return f"harness/{version('harness')}"
    except PackageNotFoundError:
        return "harness"


def guess_route(model: str) -> Route:
    """Katalog yokken: ailenin bilinen biçimi. Yanılırsa OpenCode anlaşılır hata döner."""
    if model.startswith("claude-"):
        return "messages"
    if model.startswith(("gpt-", "grok-4", "grok-build", "muse-")):
        return "responses"
    if model.startswith("gemini-"):
        return "google"
    return "chat"


def parse_catalog(payload: dict[str, Any], provider_id: str) -> Catalog:
    """models.dev `api.json`'dan bir OpenCode sağlayıcısının modelleri."""
    provider: dict[str, Any] = payload.get(provider_id) or {}
    entries: dict[str, dict[str, Any]] = provider.get("models") or {}
    catalog: Catalog = {}
    for model_id, entry in entries.items():
        transport: dict[str, Any] = entry.get("provider") or {}
        package = str(transport.get("npm") or "")
        options: list[dict[str, Any]] = entry.get("reasoning_options") or []
        efforts: list[str] = []
        for option in options:
            if option.get("type") == "effort":
                values: list[Any] = option.get("values") or []
                efforts.extend(str(value) for value in values)
        cost: dict[str, Any] = entry.get("cost") or {}
        catalog[model_id] = CatalogModel(
            name=str(entry.get("name") or model_id),
            route=_PACKAGE_ROUTES.get(package, "chat"),
            efforts=tuple(efforts),
            free=bool(cost) and not cost.get("input") and not cost.get("output"),
        )
    return catalog


async def load_catalog(
    provider_id: str,
    http: httpx.AsyncClient,
    path: Path = CATALOG_PATH,
    now: float | None = None,
) -> Catalog:
    """Önbellek tazeyse ondan, değilse models.dev'den; ağ yoksa bayat önbellek ya da boş."""
    now = time.time() if now is None else now
    cached = _read_cache(path)
    if cached is not None and now - cached[0] < CATALOG_MAX_AGE:
        return cached[1].get(provider_id, {})
    try:
        response = await http.get(CATALOG_URL, timeout=20)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError):
        return cached[1].get(provider_id, {}) if cached else {}
    # Bütün katalog ~6 MB; diske yalnız OpenCode sağlayıcıları (Zen ve Go) yazılır.
    providers = {
        name: parse_catalog(payload, name) for name in payload if name.startswith("opencode")
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        dumped = {
            name: {model_id: m.model_dump() for model_id, m in models.items()}
            for name, models in providers.items()
        }
        path.write_text(json.dumps({"fetched": now, "providers": dumped}), encoding="utf-8")
    except OSError:
        pass  # önbellek yazılamazsa bir sonraki çağrı yeniden indirir
    return providers.get(provider_id, {})


def _read_cache(path: Path) -> tuple[float, dict[str, Catalog]] | None:
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        providers: dict[str, dict[str, Any]] = raw["providers"]
        return float(raw["fetched"]), {
            name: {
                model_id: CatalogModel.model_validate(entry) for model_id, entry in models.items()
            }
            for name, models in providers.items()
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


class StaticToken:
    """Sabit API anahtarı; Responses adaptörünün belirteç arayüzüne uyar."""

    def __init__(self, key: str) -> None:
        self._key = key

    async def access_token(self) -> str:
        return self._key


Adapter = ResponsesLLM | AnthropicLLM | StreamingChatCompletionsLLM


class OpenCodeLLM:
    """Modelin API biçimini ilk çağrıda katalogdan öğrenip doğru adaptöre devreder."""

    def __init__(
        self,
        root: str,
        model: str,
        api_key: str,
        http: httpx.AsyncClient,
        effort: str | None = None,
        *,
        catalog: CatalogLoader,
    ) -> None:
        self._root = root.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._http = http
        self._effort = effort
        self._catalog = catalog
        self._adapter: Adapter | None = None
        self.route: Route | None = None
        # Aynı sohbet boyunca sabit kalır; OpenCode önbelleği ve kotası buna bağlıdır.
        self.headers = {"User-Agent": user_agent(), "x-opencode-session": uuid.uuid4().hex}

    async def resolve(self) -> Adapter:
        """Modelin adaptörü; ilk çağrıda katalogdan seçilir, sonra aynısı kullanılır."""
        if self._adapter is not None:
            return self._adapter
        entry = (await self._catalog()).get(self._model)
        route = entry.route if entry else guess_route(self._model)
        match route:
            case "responses":
                adapter: Adapter = ResponsesLLM(
                    StaticToken(self._api_key),
                    self._model,
                    self._http,
                    self._effort,
                    base_url=f"{self._root}/openai/v1",
                    headers=self.headers,
                    namespace=None,
                )
            case "messages":
                client = AsyncAnthropic(
                    api_key=self._api_key,
                    base_url=f"{self._root}/anthropic",
                    default_headers=self.headers,
                )
                # Sunucu tarafı yedek model Anthropic'e özgü bir beta; aracı geçirmeyebilir.
                adapter = AnthropicLLM(client, self._model, self._effort, fallbacks=False)
            case "chat":
                adapter = StreamingChatCompletionsLLM(
                    f"{self._root}/openai/v1",
                    self._model,
                    self._http,
                    self._api_key,
                    self._effort,
                    headers=self.headers,
                )
            case "google":
                raise OpenCodeError(
                    f"{self._model} Gemini API biçimiyle konuşuyor; harness bunu henüz "
                    "desteklemiyor. Başka bir model seç: /model"
                )
        self.route = route
        self._adapter = adapter
        return adapter

    async def complete(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AssistantMessage:
        async with aclosing(self.stream(history, tools)) as parts:
            async for part in parts:
                if isinstance(part, AssistantMessage):
                    return part
        raise OpenCodeError("Akış, cevap tamamlanmadan kesildi.")

    async def stream(
        self, history: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> AsyncGenerator[TextDelta | ReasoningDelta | AssistantMessage, None]:
        adapter = await self.resolve()
        try:
            async with aclosing(adapter.stream(history, tools)) as parts:
                async for part in parts:
                    yield part
        except LLMError as e:
            if str(e).startswith("HTTP 401"):
                raise OpenCodeError(
                    f"{e} — anahtar geçersiz; opencode.ai konsolundan yenisini al: /connect."
                ) from e
            if str(e).startswith("HTTP 429") and "free" in str(e).lower():
                raise OpenCodeError(
                    f"{e} — ücretsiz modeller yalnız OpenCode'un kendi uygulamasında çalışır."
                ) from e
            raise


async def list_opencode_models(
    root: str,
    http: httpx.AsyncClient,
    api_key: str | None,
    catalog: Catalog,
) -> list[ModelInfo]:
    """OpenCode'un o an sunduğu modeller; ad, API biçimi ve effort katalogdan.

    Gemini modelleri listelenmez: Google API biçimini konuşan adaptör yok.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = await http.get(f"{root.rstrip('/')}/v1/models", headers=headers)
    except httpx.HTTPError as e:
        raise OpenCodeError(f"{root} erişilemedi ({e}).") from e
    if response.status_code != 200:
        raise OpenCodeError(
            f"HTTP {response.status_code}: {response.text[:500]}", status=response.status_code
        )
    entries: list[dict[str, Any]] = response.json().get("data") or []
    models: list[ModelInfo] = []
    for entry in entries:
        model_id = str(entry.get("id") or "")
        if not model_id:
            continue
        known = catalog.get(model_id)
        route = known.route if known else guess_route(model_id)
        if route == "google":
            continue
        notes = [ROUTE_LABELS[route]] + (["ücretsiz"] if known and known.free else [])
        models.append(
            ModelInfo(
                slug=model_id,
                display_name=known.name if known else model_id,
                description=" · ".join(notes),
                efforts=[Effort(name=name) for name in (known.efforts if known else ())],
            )
        )
    return models
