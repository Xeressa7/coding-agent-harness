"""Sağlayıcı kaydı: hangi modele, hangi API ile, hangi anahtarla gidileceği.

Her sağlayıcı bir satırdır: konuştuğu API (ChatGPT planı / Chat Completions /
Anthropic Messages / OpenCode'da model başına seçilen biçim), adresi, anahtarının
okunduğu ortam değişkeni, varsayılan modeli ve effort seviyeleri. Yeni bir OpenAI
uyumlu sağlayıcı eklemek bu tabloya bir satır eklemektir; adaptör yazmak gerekmez.

Anahtar sohbette `/connect` ile girilip `~/.harness/auth.json`'a kaydedilir
(llm/credentials.py); aynı adlı ortam değişkeni tanımlıysa o kazanır. ChatGPT planı
anahtar değil tarayıcı girişiyle (`/connect chatgpt` ya da `harness login`) alınan
belirteci kullanır.
"""

from collections.abc import Mapping
from typing import Literal

import httpx
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict

from harness.llm.anthropic_messages import EFFORTS as ANTHROPIC_EFFORTS
from harness.llm.anthropic_messages import AnthropicLLM, list_anthropic_models
from harness.llm.base import LLMClient, LLMError, ModelInfo
from harness.llm.chat_completions import (
    EffortStyle,
    StreamingChatCompletionsLLM,
    list_chat_models,
)
from harness.llm.chatgpt_auth import DEFAULT_PATH as CHATGPT_CREDENTIALS
from harness.llm.chatgpt_auth import ChatGPTTokens
from harness.llm.credentials import environment
from harness.llm.opencode import (
    GO_URL,
    ZEN_URL,
    Catalog,
    OpenCodeLLM,
    list_opencode_models,
    load_catalog,
)
from harness.llm.responses import ResponsesLLM
from harness.llm.responses import list_models as list_chatgpt_models

ApiKind = Literal["chatgpt", "chat_completions", "anthropic", "opencode"]


class ProviderError(LLMError):
    """Sağlayıcı kullanılamıyor (anahtar yok, bilinmeyen ad...)."""


class Provider(BaseModel):
    """Bir model sağlayıcısı; kayda girmez, değiştirilemez."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    label: str
    api: ApiKind
    default_model: str
    base_url: str = ""
    api_key_env: str | None = None
    base_url_env: str | None = None  # adres bu değişkenle değiştirilebilir (ör. OLLAMA_HOST)
    efforts: tuple[str, ...] = ()
    effort_style: EffortStyle = "reasoning_effort"
    local: bool = False

    def url(self, env: Mapping[str, str]) -> str:
        override = env.get(self.base_url_env) if self.base_url_env else None
        if not override:
            return self.base_url
        override = override.rstrip("/")
        if "://" not in override:
            override = f"http://{override}"
        return override if override.endswith("/v1") else f"{override}/v1"

    def api_key(self, env: Mapping[str, str]) -> str | None:
        return env.get(self.api_key_env) if self.api_key_env else None


PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in (
        Provider(
            name="chatgpt",
            label="ChatGPT planı (Responses API, tarayıcıyla giriş)",
            api="chatgpt",
            default_model="gpt-6-luna",
        ),
        Provider(
            name="openai",
            label="OpenAI API (anahtarla)",
            api="chat_completions",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            default_model="gpt-6-luna",
            efforts=("minimal", "low", "medium", "high"),
        ),
        Provider(
            name="anthropic",
            label="Anthropic Claude (Messages API)",
            api="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            default_model="claude-opus-5-5",
            efforts=ANTHROPIC_EFFORTS,
        ),
        Provider(
            name="openrouter",
            label="OpenRouter (çok sağlayıcılı)",
            api="chat_completions",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
            default_model="openai/gpt-oss-120b",
            efforts=("low", "medium", "high"),
            effort_style="reasoning_object",
        ),
        Provider(
            name="deepseek",
            label="DeepSeek",
            api="chat_completions",
            base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-flash",
            efforts=("low", "high", "max"),
        ),
        Provider(
            name="opencode",
            label="OpenCode Zen (kullandıkça öde; GPT, Claude, Kimi, GLM...)",
            api="opencode",
            base_url=ZEN_URL,
            api_key_env="OPENCODE_API_KEY",
            default_model="claude-sonnet-5-5",
        ),
        Provider(
            name="opencode-go",
            label="OpenCode Go (aylık abonelik)",
            api="opencode",
            base_url=GO_URL,
            api_key_env="OPENCODE_API_KEY",
            default_model="glm-5.3",
        ),
        Provider(
            name="ollama",
            label="Ollama (yerel)",
            api="chat_completions",
            base_url="http://localhost:11434/v1",
            base_url_env="OLLAMA_HOST",
            default_model="qwen3-coder",
            efforts=("low", "medium", "high"),
            local=True,
        ),
        Provider(
            name="lmstudio",
            label="LM Studio (yerel)",
            api="chat_completions",
            base_url="http://localhost:1234/v1",
            default_model="qwen/qwen3-coder-30b",
            local=True,
        ),
    )
}
DEFAULT_PROVIDER = "chatgpt"


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError:
        known = ", ".join(PROVIDERS)
        raise ProviderError(f"'{name}' adında sağlayıcı yok. Var olanlar: {known}") from None


def readiness(provider: Provider, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """Sağlayıcı kullanılmaya hazır mı; değilse ne eksik (ağa çıkmadan bakılır)."""
    env = environment() if env is None else env
    match provider.api:
        case "chatgpt":
            if CHATGPT_CREDENTIALS.exists():
                return True, "giriş yapılmış"
            return False, "giriş yok: /connect chatgpt"
        case "anthropic":
            if env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"):
                return True, "anahtar var"
            return False, "anahtar yok: /connect anthropic"
        case "chat_completions" | "opencode":
            if provider.local:
                return True, f"yerel: {provider.url(env)}"
            if provider.api_key(env):
                return True, "anahtar var"
            return False, f"anahtar yok: /connect {provider.name} ({provider.api_key_env})"


def missing_key(provider: Provider) -> str:
    return (
        f"{provider.name}: API anahtarı yok. Sohbette `/connect {provider.name}` ile gir "
        f"(ya da {provider.api_key_env} ortam değişkenini tanımla)."
    )


def make_llm(
    provider: Provider,
    model: str,
    effort: str | None,
    http: httpx.AsyncClient,
    env: Mapping[str, str] | None = None,
) -> LLMClient:
    """Sağlayıcının adaptörünü kurar. Anahtar eksikse hemen anlaşılır bir hata verir."""
    env = environment() if env is None else env
    match provider.api:
        case "chatgpt":
            return ResponsesLLM(ChatGPTTokens(http), model, http, effort)
        case "anthropic":
            # Anahtar verilmezse SDK kendi sırasıyla arar (ANTHROPIC_AUTH_TOKEN, `ant` profili).
            key = provider.api_key(env)
            return AnthropicLLM(
                AsyncAnthropic(api_key=key) if key else AsyncAnthropic(), model, effort
            )
        case "chat_completions":
            key = provider.api_key(env)
            if provider.api_key_env and not key and not provider.local:
                raise ProviderError(missing_key(provider))
            return StreamingChatCompletionsLLM(
                provider.url(env), model, http, key, effort, provider.effort_style
            )
        case "opencode":
            key = provider.api_key(env)
            if not key:
                raise ProviderError(missing_key(provider))

            async def catalog() -> Catalog:
                return await load_catalog(provider.name, http)

            return OpenCodeLLM(provider.url(env), model, key, http, effort, catalog=catalog)


async def list_models(
    provider: Provider,
    http: httpx.AsyncClient,
    env: Mapping[str, str] | None = None,
) -> list[ModelInfo]:
    """Sağlayıcının o an sunduğu modeller (ağ isteği)."""
    env = environment() if env is None else env
    match provider.api:
        case "chatgpt":
            return await list_chatgpt_models(ChatGPTTokens(http), http)
        case "anthropic":
            key = provider.api_key(env)
            client = AsyncAnthropic(api_key=key) if key else AsyncAnthropic()
            async with client:
                return await list_anthropic_models(client)
        case "chat_completions":
            try:
                return await list_chat_models(
                    provider.url(env), http, provider.api_key(env), provider.efforts
                )
            except httpx.HTTPError as e:
                raise ProviderError(
                    f"{provider.name}: {provider.url(env)} erişilemedi ({e})."
                ) from e
        case "opencode":
            catalog = await load_catalog(provider.name, http)
            return await list_opencode_models(
                provider.url(env), http, provider.api_key(env), catalog
            )
