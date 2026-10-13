"""Model ve effort seçimi: menü, kayıt ve isteğe yansıması."""

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.core.config import Settings, load_settings, save_settings
from harness.llm.responses import Effort, ModelInfo, build_request, list_models
from harness.messages import UserMessage


class FixedToken:
    async def access_token(self) -> str:
        return "t"


def test_effort_goes_into_the_request() -> None:
    body = build_request("m", [UserMessage(text="x")], effort="high")

    assert body["reasoning"] == {"effort": "high"}


def test_without_effort_the_model_default_is_used() -> None:
    assert "reasoning" not in build_request("m", [UserMessage(text="x")])


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
def saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "fetch_models", lambda: MODELS)
    monkeypatch.setattr(cli, "load_settings", lambda: load_settings(path))

    def save(settings: Settings) -> None:
        save_settings(settings, path)

    monkeypatch.setattr(cli, "save_settings", save)
    return path


def test_menu_saves_picked_model_and_effort(saved: Path) -> None:
    result = CliRunner().invoke(cli.app, ["model"], input="1\n2\n")

    assert result.exit_code == 0, result.output
    assert "GPT-6-Astra" in result.output and "GPT-6-Luna" in result.output
    assert load_settings(saved) == Settings(model="gpt-6-astra", effort="high")


def test_pressing_enter_keeps_current_model_and_suggests_its_default_effort(saved: Path) -> None:
    # Kayıt yok → şu anki model luna; Enter luna'yı, ikinci Enter önerilen effort'u seçer.
    result = CliRunner().invoke(cli.app, ["model"], input="\n\n")

    assert result.exit_code == 0, result.output
    assert load_settings(saved) == Settings(model="gpt-6-luna", effort="medium")


def test_invalid_number_is_asked_again(saved: Path) -> None:
    result = CliRunner().invoke(cli.app, ["model"], input="9\n2\n1\n")

    assert result.exit_code == 0, result.output
    assert load_settings(saved) == Settings(model="gpt-6-luna", effort="low")
