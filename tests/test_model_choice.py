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


def test_model_menu_picks_only_the_model_and_keeps_a_valid_effort(saved: Path) -> None:
    save_settings(Settings(model="gpt-6-luna", effort="low"), saved)

    result = CliRunner().invoke(cli.app, ["model"], input="1\n")

    assert result.exit_code == 0, result.output
    assert "GPT-6-Astra" in result.output and "ne kadar" not in result.output
    # Astra'da da "low" var: effort korunur, ayrıca sorulmaz.
    assert load_settings(saved).model == "gpt-6-astra"
    assert load_settings(saved).effort == "low"
    assert load_settings(saved).models == {"chatgpt": "gpt-6-astra"}


def test_model_by_name_drops_an_effort_the_new_model_lacks(saved: Path) -> None:
    save_settings(Settings(model="gpt-6-astra", effort="high"), saved)

    result = CliRunner().invoke(cli.app, ["model", "gpt-6-luna"])

    assert result.exit_code == 0, result.output
    assert (load_settings(saved).model, load_settings(saved).effort) == ("gpt-6-luna", None)
    assert CliRunner().invoke(cli.app, ["model", "yok"]).exit_code == 1


def test_effort_is_chosen_separately_and_can_return_to_default(saved: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(cli.app, ["effort"], input="9\n3\n")  # geçersiz numara tekrar sorulur
    assert result.exit_code == 0, result.output
    assert "varsayılan" in result.output
    assert load_settings(saved) == Settings(model="gpt-6-luna", effort="medium")

    assert runner.invoke(cli.app, ["effort", "low"]).exit_code == 0
    assert load_settings(saved).effort == "low"
    assert runner.invoke(cli.app, ["effort", "varsayılan"]).exit_code == 0
    assert load_settings(saved).effort is None
    assert runner.invoke(cli.app, ["effort", "ultra"]).exit_code == 1
    assert load_settings(saved).model == "gpt-6-luna"  # effort komutu modele dokunmaz


def test_provider_switch_remembers_each_providers_last_model(saved: Path) -> None:
    runner = CliRunner()
    save_settings(Settings(model="gpt-6-sol", effort="high"), saved)

    assert runner.invoke(cli.app, ["provider", "anthropic"]).exit_code == 0
    settings = load_settings(saved)
    assert (settings.provider, settings.model, settings.effort) == (
        "anthropic",
        "claude-opus-5-5",
        None,
    )

    assert runner.invoke(cli.app, ["provider", "chatgpt"]).exit_code == 0
    assert load_settings(saved).model == "gpt-6-sol"  # chatgpt'de son kullanılan
    assert runner.invoke(cli.app, ["provider", "yok"]).exit_code == 1


def test_flags_override_saved_choices_for_one_run(saved: Path) -> None:
    save_settings(Settings(provider="chatgpt", model="gpt-6-sol", effort="high"), saved)

    config = cli.resolve_config("deepseek", None, None, "bash")
    assert (config.provider.name, config.model, config.effort, config.anatomy.name) == (
        "deepseek",
        "deepseek-flash",
        None,
        "bash",
    )
    same = cli.resolve_config(None, None, None, None)
    assert (same.model, same.effort, same.anatomy.name) == ("gpt-6-sol", "high", "files")
    assert cli.resolve_config(None, None, "varsayılan", None).effort is None


def test_anatomy_default_is_saved(saved: Path) -> None:
    assert CliRunner().invoke(cli.app, ["anatomy", "raw"]).exit_code == 0
    assert load_settings(saved).anatomy == "raw"
    assert cli.resolve_config(None, None, None, None).anatomy.name == "raw"
    assert CliRunner().invoke(cli.app, ["anatomy", "yok"]).exit_code != 0


def test_status_and_providers_show_readiness_without_network(saved: Path) -> None:
    result = CliRunner().invoke(cli.app, ["status"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    for name in ("chatgpt", "anthropic", "ollama", "deepseek"):
        assert name in result.output
    assert "ANTHROPIC_API_KEY" in result.output or "anahtar var" in result.output


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
