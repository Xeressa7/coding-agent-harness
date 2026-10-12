"""ChatGPT girişinin internetsiz denetlenen adımları: PKCE, adres, geri dönüş, imza, yenileme."""

import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from harness.llm.chatgpt_auth import (
    DYNAMIC_CLIENT,
    ISSUER,
    AuthError,
    ChatGPTTokens,
    Credentials,
    apply_token_response,
    authorize_url,
    load_credentials,
    open_callback_server,
    parse_callback,
    pkce_challenge,
    save_credentials,
    validate_id_token,
    wait_for_callback,
)

PLAN_SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


def test_pkce_challenge_matches_rfc_7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def test_first_login_registers_with_dynamic_client_and_app_name() -> None:
    credentials = Credentials(host_id="urn:uuid:test")

    params = query(authorize_url(credentials, "http://127.0.0.1:1455/auth/callback", "s", "n", "c"))

    assert params["client_id"] == DYNAMIC_CLIENT
    assert params["agent_name_hint"] == "coding-agent-harness"
    assert params["ext_agent_host_id"] == "urn:uuid:test"
    assert "chatgpt.tokens.use.direct" in params["scope"].split()
    assert params["resource"] == "https://api.openai.com/v1"
    assert params["code_challenge_method"] == "S256"


def test_later_login_reuses_issued_client_id() -> None:
    credentials = Credentials(host_id="urn:uuid:test", client_id="oaiapp_1", id_token="eski")

    params = query(authorize_url(credentials, "http://127.0.0.1:1455/auth/callback", "s", "n", "c"))

    assert params["client_id"] == "oaiapp_1"
    assert params["id_token_hint"] == "eski"
    assert "agent_name_hint" not in params


def test_new_host_gets_an_opaque_uuid_host_id(tmp_path: Path) -> None:
    credentials = load_credentials(tmp_path / "yok.json")

    assert credentials.host_id.startswith("urn:uuid:")


def test_credentials_survive_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "chatgpt.json"
    credentials = Credentials(host_id="urn:uuid:x", client_id="oaiapp_1", access_token="t")

    save_credentials(credentials, path)

    assert load_credentials(path) == credentials


class TestCallback:
    fresh = Credentials(host_id="urn:uuid:x")

    def test_wrong_state_is_rejected(self) -> None:
        with pytest.raises(AuthError, match="state"):
            parse_callback(
                {"state": "baska", "code": "k", "client_id": "oaiapp_1"}, "s", self.fresh
            )

    def test_user_denial_is_reported(self) -> None:
        with pytest.raises(AuthError, match="reddedildi"):
            parse_callback({"state": "s", "error": "access_denied"}, "s", self.fresh)

    def test_first_login_takes_issued_client_id(self) -> None:
        params = {"state": "s", "code": "k", "client_id": "oaiapp_1"}

        assert parse_callback(params, "s", self.fresh) == ("k", "oaiapp_1")

    def test_dynamic_client_is_never_saved_as_issued_id(self) -> None:
        with pytest.raises(AuthError):
            parse_callback(
                {"state": "s", "code": "k", "client_id": DYNAMIC_CLIENT}, "s", self.fresh
            )

    def test_mismatching_client_id_on_relogin_is_rejected(self) -> None:
        saved = Credentials(host_id="urn:uuid:x", client_id="oaiapp_1")

        with pytest.raises(AuthError, match="aynı değil"):
            parse_callback({"state": "s", "code": "k", "client_id": "oaiapp_2"}, "s", saved)


class TestIdToken:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(self, **overrides: Any) -> str:
        claims = {
            "iss": ISSUER,
            "aud": "oaiapp_1",
            "exp": int(time.time()) + 600,
            "nonce": "n",
            "email": "a@b.c",
            **overrides,
        }
        return jwt.encode(claims, self.key, algorithm="RS256")

    def test_valid_token_returns_claims(self) -> None:
        claims = validate_id_token(self.token(), self.key.public_key(), "oaiapp_1", "n")

        assert claims["email"] == "a@b.c"

    @pytest.mark.parametrize(
        "overrides",
        [{"aud": "baskasi"}, {"iss": "https://sahte.example"}, {"exp": 1}, {"nonce": "eski"}],
    )
    def test_invalid_token_is_rejected(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(AuthError):
            validate_id_token(self.token(**overrides), self.key.public_key(), "oaiapp_1", "n")

    def test_token_signed_by_another_key_is_rejected(self) -> None:
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        with pytest.raises(AuthError):
            validate_id_token(self.token(), other.public_key(), "oaiapp_1", "n")


def test_missing_plan_scope_is_refused() -> None:
    with pytest.raises(AuthError, match="izin"):
        apply_token_response(
            Credentials(host_id="x"), {"access_token": "t", "scope": "openid profile email"}
        )


@pytest.mark.anyio
async def test_expired_token_is_refreshed_and_saved(tmp_path: Path) -> None:
    path = tmp_path / "chatgpt.json"
    save_credentials(
        Credentials(
            host_id="x",
            client_id="oaiapp_1",
            access_token="eski",
            refresh_token="yenileme-1",
            expires_at=time.time() - 10,
        ),
        path,
    )
    sent: list[dict[str, list[str]]] = []

    def server(request: httpx.Request) -> httpx.Response:
        sent.append(parse_qs(request.content.decode()))
        body = {
            "access_token": "yeni",
            "refresh_token": "yenileme-2",
            "expires_in": 3600,
            "scope": PLAN_SCOPES,
        }
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        token = await ChatGPTTokens(http, path).access_token()

    assert token == "yeni"
    assert sent[0]["grant_type"] == ["refresh_token"]
    assert sent[0]["refresh_token"] == ["yenileme-1"]
    # Yenileme belirteci döner: bir sonraki yenileme yenisiyle yapılmalı.
    assert load_credentials(path).refresh_token == "yenileme-2"


@pytest.mark.anyio
async def test_without_login_asks_user_to_log_in(tmp_path: Path) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(AuthError, match="harness login"):
            await ChatGPTTokens(http, tmp_path / "yok.json").access_token()


def test_local_listener_captures_browser_redirect() -> None:
    with open_callback_server() as server:
        base = f"http://127.0.0.1:{server.server_port}"

        def browser() -> None:
            httpx.get(f"{base}/favicon.ico")  # alakasız istek yok sayılmalı
            httpx.get(f"{base}/auth/callback?code=k&state=s&client_id=oaiapp_1")

        threading.Thread(target=browser).start()
        params = wait_for_callback(server, timeout=10)

    assert params == {"code": "k", "state": "s", "client_id": "oaiapp_1"}
