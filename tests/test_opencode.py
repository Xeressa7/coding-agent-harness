"""OpenCode: model başına doğru API kapısı, katalog önbelleği, kimlik başlıkları."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from harness.llm.anthropic_messages import AnthropicLLM, build_request
from harness.llm.chat_completions import StreamingChatCompletionsLLM
from harness.llm.opencode import (
    CATALOG_MAX_AGE,
    CATALOG_URL,
    GO_URL,
    ZEN_URL,
    Catalog,
    CatalogModel,
    OpenCodeError,
    OpenCodeLLM,
    guess_route,
    list_opencode_models,
    load_catalog,
    parse_catalog,
)
from harness.llm.providers import ProviderError, get_provider, make_llm, readiness
from harness.llm.responses import ResponsesLLM
from harness.messages import AssistantMessage, ToolSpec, UserMessage

pytestmark = pytest.mark.anyio

MODELS_DEV: dict[str, Any] = {
    "opencode-go": {
        "models": {
            "glm-5.3": {"name": "GLM-5.3", "cost": {"input": 1.4, "output": 4}},
            "qwen3.8-max": {
                "name": "Qwen3.8 Max",
                "provider": {"npm": "@ai-sdk/anthropic"},
                "reasoning_options": [{"type": "effort", "values": ["low", "high"]}],
            },
            "gpt-6-luna": {"name": "GPT 6 Luna", "provider": {"npm": "@ai-sdk/openai"}},
            "gemini-4-argon": {"name": "Gemini 4", "provider": {"npm": "@ai-sdk/google"}},
            "beam-free": {"name": "Beam Free", "cost": {"input": 0, "output": 0}},
        }
    },
    # Aynı model Zen'de başka kapıdan gidebilir: katalog sağlayıcı başına okunur.
    "opencode": {"models": {"qwen3.8-max": {"name": "Qwen3.8 Max"}}},
    "anthropic": {"models": {"claude-x": {"name": "başka sağlayıcı; önbelleğe girmez"}}},
}
TOOL = ToolSpec(name="read_file", description="oku", parameters={"type": "object"})


def go_catalog() -> Catalog:
    return parse_catalog(MODELS_DEV, "opencode-go")


def test_catalog_routes_each_model_by_its_sdk_package() -> None:
    go = go_catalog()
    assert {k: m.route for k, m in go.items()} == {
        "glm-5.3": "chat",
        "qwen3.8-max": "messages",
        "gpt-6-luna": "responses",
        "gemini-4-argon": "google",
        "beam-free": "chat",
    }
    assert go["qwen3.8-max"].efforts == ("low", "high")
    assert go["beam-free"].free and not go["glm-5.3"].free
    assert not go["gpt-6-luna"].free  # fiyatı bilinmeyen model ücretsiz sayılmaz
    assert parse_catalog(MODELS_DEV, "opencode")["qwen3.8-max"].route == "chat"


def test_without_catalog_the_model_family_decides() -> None:
    assert guess_route("claude-sonnet-5-5") == "messages"
    assert guess_route("gpt-6-luna") == "responses"
    assert guess_route("grok-4.7") == "responses"
    assert guess_route("grok-code") == "chat"
    assert guess_route("gemini-4-argon") == "google"
    assert guess_route("kimi-k3") == "chat"


async def test_catalog_is_downloaded_once_and_trimmed_to_opencode(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=MODELS_DEV)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await load_catalog("opencode-go", http, path, now=1000.0)
        again = await load_catalog("opencode", http, path, now=1000.0 + CATALOG_MAX_AGE - 1)
    assert calls == [CATALOG_URL]
    assert first["qwen3.8-max"].route == "messages" and again["qwen3.8-max"].route == "chat"
    assert set(json.loads(path.read_text(encoding="utf-8"))["providers"]) == {
        "opencode",
        "opencode-go",
    }


async def test_stale_catalog_is_refreshed_but_kept_when_offline(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    online = httpx.MockTransport(lambda request: httpx.Response(200, json=MODELS_DEV))
    async with httpx.AsyncClient(transport=online) as http:
        await load_catalog("opencode-go", http, path, now=0.0)

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ağ yok", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as http:
        stale = await load_catalog("opencode-go", http, path, now=CATALOG_MAX_AGE + 1)
        nothing = await load_catalog("opencode-go", http, tmp_path / "yok.json")
    assert "glm-5.3" in stale  # ağ yokken bayat katalog, boştan iyidir
    assert nothing == {}


def recording(responses: list[str]) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    bodies = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=next(bodies))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def gateway(http: httpx.AsyncClient, model: str, catalog: Catalog | None = None) -> OpenCodeLLM:
    async def load() -> Catalog:
        return go_catalog() if catalog is None else catalog

    return OpenCodeLLM(GO_URL, model, "oc-key", http, "high", catalog=load)


def chat_sse(text: str) -> str:
    chunk = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"


async def test_chat_model_goes_to_chat_completions_with_identity_headers() -> None:
    http, seen = recording([chat_sse("Bir"), chat_sse("İki")])
    async with http:
        llm = gateway(http, "glm-5.3")
        first = await llm.complete([UserMessage(text="x")])
        await llm.complete([UserMessage(text="y")])
    assert first.text == "Bir" and llm.route == "chat"
    assert str(seen[0].url) == f"{GO_URL}/openai/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer oc-key"
    assert seen[0].headers["User-Agent"].startswith("harness")
    # Oturum kimliği sohbet boyunca sabittir (OpenCode önbelleği ve kotası ona bağlı).
    session = seen[0].headers["x-opencode-session"]
    assert session and seen[1].headers["x-opencode-session"] == session
    assert json.loads(seen[0].content)["reasoning_effort"] == "high"


async def test_gpt_model_goes_to_responses_with_plain_function_tools() -> None:
    done = {
        "type": "response.completed",
        "response": {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]
        },
    }
    http, seen = recording([f"data: {json.dumps(done)}\n\n"])
    async with http:
        reply = await gateway(http, "gpt-6-luna").complete([UserMessage(text="x")], [TOOL])
    assert reply == AssistantMessage(text="ok")
    assert str(seen[0].url) == f"{GO_URL}/openai/v1/responses"
    body = json.loads(seen[0].content)
    assert body["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "description": "oku",
            "parameters": TOOL.parameters,
        }
    ]
    assert body["reasoning"] == {"summary": "auto", "effort": "high"}
    assert "x-opencode-session" in seen[0].headers


async def test_messages_model_uses_anthropic_sdk_at_opencode_without_fallback_beta() -> None:
    async with httpx.AsyncClient() as http:
        adapter = await gateway(http, "qwen3.8-max").resolve()
    assert isinstance(adapter, AnthropicLLM)
    assert str(adapter.client.base_url).rstrip("/") == f"{GO_URL}/anthropic"
    assert adapter.client.api_key == "oc-key"
    assert not adapter.fallbacks
    request = build_request("claude-opus-5-5", [UserMessage(text="x")], fallbacks=False)
    assert "fallbacks" not in request and "betas" not in request


async def test_unknown_model_falls_back_to_family_rule_and_gemini_is_refused() -> None:
    async with httpx.AsyncClient() as http:
        guessed = await gateway(http, "kimi-k9", catalog={}).resolve()
        assert isinstance(guessed, StreamingChatCompletionsLLM)
        assert isinstance(await gateway(http, "gpt-7", catalog={}).resolve(), ResponsesLLM)
        with pytest.raises(OpenCodeError, match="Gemini"):
            await gateway(http, "gemini-4-argon").complete([UserMessage(text="x")])


async def test_model_list_names_the_api_and_hides_gemini() -> None:
    ids = ["glm-5.3", "qwen3.8-max", "gemini-4-argon", "beam-free", "yeni-model"]
    listing = {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}
    http, seen = recording([json.dumps(listing)])
    async with http:
        models = await list_opencode_models(GO_URL, http, "oc-key", go_catalog())
    assert str(seen[0].url) == f"{GO_URL}/v1/models"
    by_id = {m.slug: m for m in models}
    assert list(by_id) == ["glm-5.3", "qwen3.8-max", "beam-free", "yeni-model"]
    assert by_id["qwen3.8-max"].display_name == "Qwen3.8 Max"
    assert by_id["qwen3.8-max"].description == "Messages"
    assert [e.name for e in by_id["qwen3.8-max"].efforts] == ["low", "high"]
    assert by_id["beam-free"].description == "Chat · ücretsiz"
    assert by_id["yeni-model"].description == "Chat"


async def test_providers_need_the_opencode_key() -> None:
    zen, go = get_provider("opencode"), get_provider("opencode-go")
    assert (zen.base_url, go.base_url) == (ZEN_URL, GO_URL)
    assert not readiness(go, {})[0] and "OPENCODE_API_KEY" in readiness(go, {})[1]
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError, match="OPENCODE_API_KEY"):
            make_llm(go, "glm-5.3", None, http, {})
        assert isinstance(
            make_llm(go, "glm-5.3", None, http, {"OPENCODE_API_KEY": "k"}), OpenCodeLLM
        )


def test_catalog_model_is_a_frozen_value() -> None:
    model = CatalogModel(name="x", route="chat")
    with pytest.raises(ValueError):
        model.name = "y"  # type: ignore[misc]
