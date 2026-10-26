"""Sağlayıcı kaydı: her satır doğru adaptörü kurar, eksik anahtar ağa çıkmadan söylenir."""

import httpx
import pytest

from harness.llm.anthropic_messages import AnthropicLLM
from harness.llm.chat_completions import StreamingChatCompletionsLLM
from harness.llm.providers import (
    DEFAULT_PROVIDER,
    PROVIDERS,
    ProviderError,
    get_provider,
    list_models,
    make_llm,
    reachable,
    readiness,
)
from harness.llm.responses import ResponsesLLM

pytestmark = pytest.mark.anyio


async def test_each_api_kind_builds_its_adapter() -> None:
    env = {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}
    async with httpx.AsyncClient() as http:
        assert isinstance(make_llm(get_provider("chatgpt"), "m", None, http, env), ResponsesLLM)
        assert isinstance(
            make_llm(get_provider("openai"), "m", "low", http, env), StreamingChatCompletionsLLM
        )
        assert isinstance(make_llm(get_provider("anthropic"), "m", None, http, env), AnthropicLLM)
        assert isinstance(
            make_llm(get_provider("ollama"), "m", None, http, {}), StreamingChatCompletionsLLM
        )


async def test_missing_key_is_reported_before_any_request() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
            make_llm(get_provider("openrouter"), "m", None, http, {})
    ready, detail = readiness(get_provider("openrouter"), {})
    assert not ready and "OPENROUTER_API_KEY" in detail
    assert readiness(get_provider("openrouter"), {"OPENROUTER_API_KEY": "x"})[0]
    assert readiness(get_provider("ollama"), {})[0]  # yerel: anahtar istemez


def test_unknown_provider_lists_the_known_ones() -> None:
    with pytest.raises(ProviderError, match="chatgpt"):
        get_provider("yok")
    assert DEFAULT_PROVIDER in PROVIDERS
    assert all(p.default_model for p in PROVIDERS.values())


def test_local_address_can_be_overridden_like_ollama_does() -> None:
    ollama = get_provider("ollama")
    assert ollama.url({}) == "http://localhost:11434/v1"
    assert ollama.url({"OLLAMA_HOST": "10.0.0.5:11434"}) == "http://10.0.0.5:11434/v1"
    assert ollama.url({"OLLAMA_HOST": "http://kutu:9000/v1"}) == "http://kutu:9000/v1"


async def test_unreachable_local_server_is_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bağlantı reddedildi", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError, match="erişilemedi"):
            await list_models(get_provider("ollama"), http, {})


async def test_local_server_probe_says_running_or_down() -> None:
    def up(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("reddedildi", request=request)

    ollama = get_provider("ollama")
    async with httpx.AsyncClient(transport=httpx.MockTransport(up)) as http:
        assert await reachable(ollama, http, {})
    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        assert not await reachable(ollama, http, {})
