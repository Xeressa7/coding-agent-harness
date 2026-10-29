"""Sağlayıcı bağlama ve seçim sohbetin içinde: anahtar deposu, /connect, tek model listesi."""

import json
import os
from pathlib import Path

import httpx
import pytest

from harness.cli.commands import ChatSession, Choice, Picker
from harness.core.anatomy import get_preset
from harness.llm.base import Effort, LLMError, ModelInfo
from harness.llm.credentials import environment, key_source, load_keys, remove_key, save_key
from harness.llm.opencode import OpenCodeError, OpenCodeLLM
from harness.llm.providers import Provider, ProviderError, get_provider, make_llm, readiness
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage

pytestmark = pytest.mark.anyio

GO_MODELS = [
    ModelInfo(
        slug="glm-5.3",
        display_name="GLM-5.3",
        description="Chat",
        efforts=[Effort(name="low"), Effort(name="high")],
    ),
    ModelInfo(slug="kimi-k3", display_name="Kimi K3", description="Chat"),
]
CHATGPT_MODELS = [
    ModelInfo(slug="gpt-6-luna", display_name="GPT-6-Luna", efforts=[Effort(name="medium")])
]


# ------------------------------------------------------------ anahtar deposu


def test_key_is_stored_by_env_name_and_the_real_env_var_wins(
    auth_files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_key("OPENCODE_API_KEY", "kayitli")
    stored = json.loads((auth_files / "auth.json").read_text(encoding="utf-8"))
    assert stored == {"OPENCODE_API_KEY": "kayitli"}
    assert environment()["OPENCODE_API_KEY"] == "kayitli"
    assert key_source("OPENCODE_API_KEY") == "file"

    monkeypatch.setenv("OPENCODE_API_KEY", "ortamdan")
    assert environment()["OPENCODE_API_KEY"] == "ortamdan"
    assert key_source("OPENCODE_API_KEY") == "env"

    assert remove_key("OPENCODE_API_KEY") and not remove_key("OPENCODE_API_KEY")
    assert load_keys() == {}


def test_unreadable_key_file_is_treated_as_empty(auth_files: Path) -> None:
    auth_files.mkdir()
    (auth_files / "auth.json").write_text("[bozuk", encoding="utf-8")
    assert load_keys() == {}
    (auth_files / "auth.json").write_text('["liste"]', encoding="utf-8")
    assert load_keys() == {}
    save_key("DEEPSEEK_API_KEY", "k")  # bozuk dosyanın üstüne temiz yazılır
    assert load_keys() == {"DEEPSEEK_API_KEY": "k"}


@pytest.mark.skipif(os.name == "nt", reason="Windows dosya izinlerini chmod ile vermez")
def test_key_file_is_readable_only_by_its_owner(auth_files: Path) -> None:
    save_key("OPENAI_API_KEY", "k")
    assert (auth_files / "auth.json").stat().st_mode & 0o777 == 0o600


async def test_providers_use_the_stored_key_when_no_env_is_given() -> None:
    go = get_provider("opencode-go")
    assert not readiness(go)[0]
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderError, match="/connect opencode-go"):
            make_llm(go, "glm-5.3", None, http)
        save_key("OPENCODE_API_KEY", "k")
        assert readiness(go) == (True, "anahtar var")
        assert readiness(get_provider("opencode"))[0]  # Zen ve Go aynı anahtarı paylaşır
        assert isinstance(make_llm(go, "glm-5.3", None, http), OpenCodeLLM)


# ------------------------------------------------------------ sohbet içi seçim


def session_with(
    tmp_path: Path,
    saved: list[tuple[str, str, str | None, str]] | None = None,
    login_email: str | None = "ben@example.com",
    go_rejects: str | None = None,
) -> ChatSession:
    async def load_models(provider: Provider) -> list[ModelInfo]:
        key = environment().get("OPENCODE_API_KEY")
        if provider.name == "opencode-go" and go_rejects is not None and key == go_rejects:
            raise OpenCodeError('HTTP 401: {"error":{"message":"Invalid credential"}}', status=401)
        if provider.name == "opencode-go":
            return GO_MODELS
        if provider.name == "chatgpt":
            return CHATGPT_MODELS
        raise httpx.ConnectError("kapalı")

    def make(provider: Provider, model: str, effort: str | None) -> ScriptedLLM:
        if provider.api_key_env and provider.api_key_env not in environment():
            raise ProviderError(f"{provider.name}: anahtar yok")
        return ScriptedLLM([AssistantMessage(text="ok")])

    async def login(on_url: object) -> str | None:
        if login_email is None:
            raise LLMError("tarayıcı kapandı")
        (tmp_path / "auth").mkdir(exist_ok=True)
        (tmp_path / "auth" / "chatgpt.json").write_text("{}", encoding="utf-8")
        return login_email

    def remember(session: ChatSession) -> None:
        if saved is not None:
            entry = (session.provider.name, session.model, session.effort, session.anatomy.name)
            saved.append(entry)

    return ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        provider=get_provider("chatgpt"),
        model="gpt-6-luna",
        effort=None,
        make_llm=make,
        load_models=load_models,
        load_prompt=lambda anatomy: "talimat",
        sessions_dir=tmp_path / "logs",
        save_choice=remember,
        login=login,
    )


def rows(picker: Picker) -> dict[str, Choice]:
    return {f"{c.group} | {c.value}": c for c in picker.choices}


async def test_connect_asks_for_the_key_then_offers_that_providers_models(tmp_path: Path) -> None:
    session = session_with(tmp_path)

    listing = (await session.run("/connect")).picker
    assert listing is not None and listing.kind == "connect"
    go = next(c for c in listing.choices if c.value == "opencode-go")
    assert "bağlı değil" in go.detail

    asked = await session.choose("connect", "opencode-go")
    assert asked.key is not None and asked.key.provider == "opencode-go"
    assert "OPENCODE_API_KEY" in asked.key.prompt and "görünmez" in asked.key.prompt

    connected = await session.save_key("opencode-go", "  oc-anahtar \n")
    assert load_keys() == {"OPENCODE_API_KEY": "oc-anahtar"}
    assert "bağlandı" in connected.text and "oc-anahtar" not in connected.text
    assert connected.picker is not None
    assert [c.value for c in connected.picker.choices] == [
        "opencode-go/glm-5.3",
        "opencode-go/kimi-k3",
    ]
    again = (await session.run("/connect")).picker
    assert again is not None
    assert "anahtar kayıtlı" in next(c for c in again.choices if c.value == "opencode").detail


async def test_empty_key_changes_nothing(tmp_path: Path) -> None:
    session = session_with(tmp_path)
    reply = await session.save_key("opencode-go", "   ")
    assert "hiçbir şey değişmedi" in reply.text and load_keys() == {}


async def test_text_with_spaces_is_not_saved_as_a_key(tmp_path: Path) -> None:
    # Gerçek olay: panoda anahtar yerine kopyalanan terminal çıktısı kalmıştı.
    session = session_with(tmp_path)
    reply = await session.save_key("opencode-go", "Windows PowerShell")
    assert "benzemiyor" in reply.text and reply.picker is None
    assert load_keys() == {}


async def test_rejected_key_is_not_saved_and_the_old_one_stays(tmp_path: Path) -> None:
    save_key("OPENCODE_API_KEY", "eski")
    session = session_with(tmp_path, go_rejects="yanlis")
    reply = await session.save_key("opencode-go", "yanlis")
    assert "reddetti" in reply.text and "Invalid credential" in reply.text
    assert reply.picker is None and "yanlis" not in reply.text
    assert load_keys() == {"OPENCODE_API_KEY": "eski"}

    first = session_with(tmp_path, go_rejects="yanlis")
    remove_key("OPENCODE_API_KEY")
    await first.save_key("opencode-go", "yanlis")
    assert load_keys() == {}


async def test_key_is_kept_when_the_provider_cannot_be_reached(tmp_path: Path) -> None:
    session = session_with(tmp_path)
    reply = await session.save_key("deepseek", "sk-ds")  # sahte liste: ağ hatası
    assert "bağlandı" in reply.text and load_keys() == {"DEEPSEEK_API_KEY": "sk-ds"}
    assert reply.picker is not None and reply.picker.choices[0].label == "ulaşılamadı"


async def test_one_model_list_spans_connected_providers_and_lists_the_rest(
    tmp_path: Path,
) -> None:
    save_key("OPENCODE_API_KEY", "k")
    session = session_with(tmp_path)

    picker = (await session.run("/model")).picker
    assert picker is not None and picker.kind == "model"
    found = rows(picker)
    luna = found["chatgpt · ChatGPT planı (Responses API, tarayıcıyla giriş) | chatgpt/gpt-6-luna"]
    assert luna.current
    go_group = "opencode-go · OpenCode Go (aylık abonelik)"
    assert found[f"{go_group} | opencode-go/glm-5.3"].detail == "GLM-5.3 · Chat"
    # Yerel sunucu kapalı: satır seçilemez ama görünür.
    ollama = next(c for c in picker.choices if c.group.startswith("ollama"))
    assert ollama.label == "ulaşılamadı" and not ollama.enabled
    # Anahtarı olmayanlar sonda; seçilince bağlanmaya gider.
    waiting = found["Bağlı değil | anthropic"]
    assert waiting.kind == "connect" and waiting.enabled
    assert picker.choices[-1].group == "Bağlı değil"


async def test_picking_a_model_switches_provider_saves_it_and_asks_the_effort(
    tmp_path: Path,
) -> None:
    save_key("OPENCODE_API_KEY", "k")
    saved: list[tuple[str, str, str | None, str]] = []
    session = session_with(tmp_path, saved)

    reply = await session.choose("model", "opencode-go/glm-5.3")
    assert (session.provider.name, session.model, session.effort) == (
        "opencode-go",
        "glm-5.3",
        None,
    )
    assert saved[-1] == ("opencode-go", "glm-5.3", None, "files")
    assert reply.picker is not None and reply.picker.kind == "effort"
    assert [c.value for c in reply.picker.choices] == ["varsayılan", "low", "high"]
    assert reply.picker.choices[0].current

    await session.choose("effort", "high")
    assert session.effort == "high" and saved[-1][2] == "high"
    # Yazılan komut effort sormaz; effort'u olmayan modelde effort sıfırlanır.
    typed = await session.run("/model kimi-k3")
    assert typed.picker is None and (session.model, session.effort) == ("kimi-k3", None)
    await session.run("/anatomy bash")
    assert saved[-1] == ("opencode-go", "kimi-k3", None, "bash")


async def test_provider_prefix_falls_back_to_a_slash_in_the_model_name(tmp_path: Path) -> None:
    session = session_with(tmp_path)

    async def openrouter_style(provider: Provider) -> list[ModelInfo]:
        return [ModelInfo(slug="openai/gpt-oss-120b", display_name="gpt-oss")]

    session._load_models = openrouter_style  # pyright: ignore[reportPrivateUsage]
    reply = await session.run("/model openai/gpt-oss-120b")
    # `openai` bir sağlayıcı adı ama model orada yok: adın tamamı bu sağlayıcıda aranır.
    assert session.model == "openai/gpt-oss-120b" and session.provider.name == "chatgpt"
    assert "geçmiş korundu" in reply.text


async def test_unusable_provider_or_unknown_model_changes_nothing(tmp_path: Path) -> None:
    session = session_with(tmp_path)
    # opencode-go'nun modeli listelenir ama anahtar yok: geçiş reddedilir.
    refused = await session.run("/model opencode-go/glm-5.3")
    assert "kullanılamıyor" in refused.text and session.provider.name == "chatgpt"
    assert "yok" in (await session.run("/model uçak")).text
    assert "yok" in (await session.run("/connect uçak")).text
    assert session.model == "gpt-6-luna"


async def test_chatgpt_login_runs_in_chat_and_offers_its_models(tmp_path: Path) -> None:
    session = session_with(tmp_path)
    started = await session.run("/login")  # takma ad: /connect
    assert started.picker is not None and started.picker.kind == "connect"
    begin = await session.choose("connect", "chatgpt")
    assert begin.login

    done = await session.login(lambda url: None)
    assert "ben@example.com" in done.text
    assert done.picker is not None and done.picker.choices[0].value == "chatgpt/gpt-6-luna"

    failed = await session_with(tmp_path, login_email=None).login(lambda url: None)
    assert "Giriş başarısız" in failed.text and failed.picker is None


async def test_local_provider_connect_reports_a_closed_server(tmp_path: Path) -> None:
    reply = await session_with(tmp_path).run("/connect ollama")
    assert "kapalı" in reply.text and reply.picker is None and reply.key is None


async def test_disconnect_removes_the_stored_key_or_the_login(
    tmp_path: Path, auth_files: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = session_with(tmp_path)
    save_key("OPENCODE_API_KEY", "k")
    assert "silindi" in (await session.run("/disconnect opencode")).text
    assert load_keys() == {}
    assert "zaten yok" in (await session.run("/disconnect opencode")).text
    monkeypatch.setenv("OPENCODE_API_KEY", "x")
    assert "hâlâ tanımlı" in (await session.run("/disconnect opencode-go")).text

    auth_files.mkdir(exist_ok=True)
    (auth_files / "chatgpt.json").write_text("{}", encoding="utf-8")
    assert "silindi" in (await session.run("/logout chatgpt")).text
    assert not (auth_files / "chatgpt.json").exists()
    assert "zaten yok" in (await session.run("/disconnect chatgpt")).text
    assert "anahtar yok" in (await session.run("/disconnect ollama")).text
    assert "Hangi" in (await session.run("/disconnect")).text


async def test_effort_and_anatomy_and_resume_open_their_lists(tmp_path: Path) -> None:
    session = session_with(tmp_path)
    effort = (await session.run("/effort")).picker
    assert effort is not None and [c.value for c in effort.choices] == ["varsayılan", "medium"]
    anatomy = (await session.run("/anatomy")).picker
    assert anatomy is not None and next(c for c in anatomy.choices if c.current).value == "files"
    assert "kayıtlı oturum yok" in (await session.run("/resume")).text
