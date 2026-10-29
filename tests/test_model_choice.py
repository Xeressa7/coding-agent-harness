"""Model ve effort seçimi: menü, kayıt ve isteğe yansıması."""

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.core.config import Settings, load_settings, save_settings
from harness.llm.base import Effort, ModelInfo
from harness.llm.providers import Provider
from harness.llm.responses import build_request, list_models, parse_output
from harness.llm.scripted import ScriptedLLM
from harness.messages import UserMessage


class FixedToken:
    async def access_token(self) -> str:
        return "t"


def test_effort_goes_into_the_request_next_to_the_summary_request() -> None:
    body = build_request("m", [UserMessage(text="x")], effort="high")

    assert body["reasoning"] == {"summary": "auto", "effort": "high"}


def test_without_effort_the_model_default_is_used() -> None:
    assert build_request("m", [UserMessage(text="x")])["reasoning"] == {"summary": "auto"}


@pytest.mark.anyio
async def test_model_list_carries_efforts_and_drops_ultra() -> None:
    payload = {
        "models": [
            {
                "slug": "gpt-6-sol",
                "display_name": "GPT-6-Sol",
                "description": "iş atı",
                "visibility": "list",
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "hızlı"},
                    {"effort": "medium", "description": "dengeli"},
                    {"effort": "ultra", "description": "görev devri"},
                ],
            },
            {"slug": "gizli", "visibility": "hide"},
        ]
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as http:
        (model,) = await list_models(FixedToken(), http)

    assert model.display_name == "GPT-6-Sol"
    assert model.default_effort == "medium"
    assert [e.name for e in model.efforts] == ["low", "medium"]


def test_settings_default_to_luna_and_survive_save(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert load_settings(path) == Settings(model="gpt-6-luna", effort=None)

    save_settings(Settings(model="gpt-6-sol", effort="high"), path)

    assert load_settings(path) == Settings(model="gpt-6-sol", effort="high")


MODELS = [
    ModelInfo(
        slug="gpt-6-astra",
        display_name="GPT-6-Astra",
        description="en güçlü",
        default_effort="low",
        efforts=[Effort(name="low", description="hızlı"), Effort(name="high", description="derin")],
    ),
    ModelInfo(
        slug="gpt-6-luna",
        display_name="GPT-6-Luna",
        description="küçük",
        default_effort="medium",
        efforts=[
            Effort(name="low", description="hızlı"),
            Effort(name="medium", description="dengeli"),
        ],
    ),
]


@pytest.fixture
def saved(settings_file: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLI'nin model listesi sabit; ayar dosyası conftest'teki geçici dosya."""

    def fetch(provider: Provider) -> list[ModelInfo]:
        return MODELS

    monkeypatch.setattr(cli, "fetch_models", fetch)
    return settings_file


def silent_model(provider: Provider, model: str, effort: str | None, http: object) -> ScriptedLLM:
    return ScriptedLLM([])


def test_model_flag_can_name_the_provider_for_one_run(saved: Path) -> None:
    save_settings(Settings(provider="chatgpt", model="gpt-6-sol", effort="high"), saved)

    config = cli.resolve_config("opencode-go/glm-5.3", None, "bash")
    assert (config.provider.name, config.model, config.effort, config.anatomy.name) == (
        "opencode-go",
        "glm-5.3",
        None,
        "bash",
    )
    same = cli.resolve_config(None, None, None)
    assert (same.provider.name, same.model, same.effort, same.anatomy.name) == (
        "chatgpt",
        "gpt-6-sol",
        "high",
        "files",
    )
    assert cli.resolve_config("gpt-6-sol", None, None).effort == "high"
    assert cli.resolve_config("gpt-6-luna", None, None).effort is None
    # OpenRouter'ın kendi `/`'lu adı: tam yol; sağlayıcı olmayan ön ek modelin parçasıdır.
    routed = cli.resolve_config("openrouter/openai/gpt-oss-120b", None, None)
    assert (routed.provider.name, routed.model) == ("openrouter", "openai/gpt-oss-120b")
    plain = cli.resolve_config("meta/llama-5", None, None)
    assert (plain.provider.name, plain.model) == ("chatgpt", "meta/llama-5")
    assert cli.resolve_config(None, "varsayılan", None).effort is None
    assert load_settings(saved) == Settings(provider="chatgpt", model="gpt-6-sol", effort="high")


def test_choices_made_in_chat_are_saved_for_the_next_run(
    saved: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    async def models(provider: Provider, http: object) -> list[ModelInfo]:
        if provider.name != "chatgpt":
            raise httpx.ConnectError("kapalı")
        return MODELS

    monkeypatch.setattr(cli, "list_models", models)
    monkeypatch.setattr(cli, "make_llm", silent_model)
    # /model listesi: 1 Astra, 2 Luna; seçilen modelde effort sorulur: 1 varsayılan, 2 low, 3 high.
    # 9: seçilebilir 8 satır var (kapalı yerel sunucular numarasız); yeniden sorulur.
    result = CliRunner().invoke(
        cli.app, ["--plain"], input="/model\n9\n1\n3\n/anatomy bash\n/exit\n"
    )

    assert result.exit_code == 0, result.output
    assert "GPT-6-Astra" in result.output and "ne kadar düşünsün" in result.output
    assert load_settings(saved) == Settings(
        provider="chatgpt", model="gpt-6-astra", effort="high", anatomy="bash", theme="koyu"
    )
    config = cli.resolve_config(None, None, None)
    assert (config.model, config.effort, config.anatomy.name) == ("gpt-6-astra", "high", "bash")


def test_plain_list_can_be_left_without_choosing(
    saved: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "make_llm", silent_model)
    result = CliRunner().invoke(cli.app, ["--plain"], input="/anatomy\n9\n\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Listede böyle bir numara yok" in result.output
    assert not saved.exists()  # vazgeçilen seçim kaydedilmez


def test_models_lists_any_provider_and_old_choice_commands_are_gone(saved: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.app, ["models", "opencode-go"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "opencode-go modelleri" in result.output and "opencode-go/gpt-6-astra" in result.output
    assert runner.invoke(cli.app, ["models", "yok"]).exit_code != 0
    for gone in ("provider", "providers", "model", "effort", "anatomy", "status"):
        assert runner.invoke(cli.app, [gone]).exit_code == 2  # böyle komut yok


def test_version_flag() -> None:
    result = CliRunner().invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and result.output.startswith("harness")


def test_reasoning_summary_is_read_from_reasoning_items() -> None:
    reply = parse_output(
        [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Önce README."}]},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Sonra kod."}]},
            {"type": "message", "content": [{"type": "output_text", "text": "Cevap"}]},
        ]
    )

    assert reply.reasoning == "Önce README.\n\nSonra kod."
    assert reply.text == "Cevap"
