"""ChatGPT ile giriş (Sign in with ChatGPT): OAuth 2.0 + PKCE.

Kullanıcı tarayıcıda ChatGPT hesabıyla girer; harness parolayı hiç görmez.
OpenAI karşılığında kısa ömürlü bir erişim belirteci (access token, 1 saat)
ve onu yenilemeye yarayan bir yenileme belirteci (refresh token) verir. Bu
belirteçlerle Responses API'ye, kullanımı kullanıcının ChatGPT planından
düşecek şekilde istek atılır.

Akış (developers.openai.com/siwc, "token sharing for open source"):
1. Hazırlık: kalıcı host ID, tek kullanımlık state/nonce ve PKCE çifti
2. Tarayıcı: authorize adresi açılır, kullanıcı girer ve izin verir
3. Geri dönüş: OpenAI tarayıcıyı 127.0.0.1'deki yerel dinleyiciye yollar
4. Takas: gelen kod, PKCE doğrulayıcısıyla belirteçlere çevrilir
5. Kontrol: ID token'ın imzası, yayıncısı, alıcısı ve nonce'u doğrulanır

Belirteçler kullanıcı klasöründe (~/.harness/chatgpt.json) durur; repoya,
loga ve sohbete asla girmez.
"""

import base64
import hashlib
import os
import secrets
import time
import uuid
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import jwt
from pydantic import BaseModel

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"

# İlk ikisi kimlik; sonrakiler planla model kullanma izni.
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"

# İlk girişte kullanılan genel kimlik; OpenAI karşılığında bu kuruluma özel client_id verir.
DYNAMIC_CLIENT = "dynamic_agent_client"
APP_NAME = "coding-agent-harness"

CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
LOGIN_TIMEOUT_SECONDS = 300

DEFAULT_PATH = Path.home() / ".harness" / "chatgpt.json"


class AuthError(Exception):
    """Giriş ya da yenileme başarısız; mesaj kullanıcıya gösterilebilir."""


class Credentials(BaseModel):
    """Diske yazılan giriş bilgisi."""

    host_id: str
    client_id: str | None = None
    email: str | None = None
    id_token: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: float = 0.0
    scopes: list[str] = []


# --- Saklama ---------------------------------------------------------------


def load_credentials(path: Path = DEFAULT_PATH) -> Credentials:
    """Kayıtlı bilgiyi okur; yoksa bu bilgisayar için yeni bir host ID üretir."""
    if path.exists():
        return Credentials.model_validate_json(path.read_text(encoding="utf-8"))
    # Host ID kişisel bilgi taşımaz: rastgele bir UUID, bu kurulumu tanıtır.
    return Credentials(host_id=f"urn:uuid:{uuid.uuid4()}")


def save_credentials(credentials: Credentials, path: Path = DEFAULT_PATH) -> None:
    """Yarım yazılmış dosya kalmasın diye önce geçici dosyaya yazar, sonra yerine koyar."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(credentials.model_dump_json(indent=2), encoding="utf-8")
    temporary.chmod(0o600)  # Unix'te yalnız sahibi okur; Windows'ta etkisi sınırlı.
    os.replace(temporary, path)


# --- Hazırlık ve tarayıcı ----------------------------------------------------


def pkce_challenge(verifier: str) -> str:
    """PKCE: doğrulayıcının SHA-256 özeti, base64url, dolgusuz (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(
    credentials: Credentials, redirect_uri: str, state: str, nonce: str, challenge: str
) -> str:
    first_time = credentials.client_id is None
    params = {
        "client_id": DYNAMIC_CLIENT if first_time else credentials.client_id,
        "ext_agent_host_id": credentials.host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    if first_time:
        params["agent_name_hint"] = APP_NAME
    elif credentials.id_token:
        params["id_token_hint"] = credentials.id_token
    if credentials.email:
        params["login_hint"] = credentials.email
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


# --- Geri dönüş --------------------------------------------------------------


def parse_callback(
    params: dict[str, str], expected_state: str, credentials: Credentials
) -> tuple[str, str]:
    """Tarayıcının getirdiği parametreleri denetler; (kod, client_id) döner."""
    if params.get("state") != expected_state:
        raise AuthError("Geri dönüşteki state eşleşmedi; giriş iptal edildi.")
    if "error" in params:
        reason = params.get("error_description") or params["error"]
        raise AuthError(f"Giriş reddedildi: {reason}")
    code = params.get("code")
    if not code:
        raise AuthError("Geri dönüşte yetkilendirme kodu yok.")

    returned = params.get("client_id")
    saved = credentials.client_id
    if saved is None:
        if not returned or returned == DYNAMIC_CLIENT:
            raise AuthError("İlk girişte OpenAI bu kuruluma client_id vermedi.")
        return code, returned
    if returned and returned != saved:
        raise AuthError("Geri dönen client_id kayıtlı olanla aynı değil; giriş reddedildi.")
    return code, saved


def wait_for_callback(server: HTTPServer, timeout: float = LOGIN_TIMEOUT_SECONDS) -> dict[str, str]:
    """Yerel dinleyicide, tarayıcı geri dönene kadar bekler."""
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path != CALLBACK_PATH:
                self.send_error(404)
                return
            received.update(parse_qsl(url.query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            page = "<h2>Giriş tamamlandı.</h2><p>Bu sekmeyi kapatıp terminale dönebilirsiniz.</p>"
            self.wfile.write(page.encode("utf-8"))

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Tarayıcı isteklerini terminale yazma; URL kod içerir.

    server.RequestHandlerClass = Handler
    server.timeout = 1
    deadline = time.monotonic() + timeout
    while not received and time.monotonic() < deadline:
        server.handle_request()
    if not received:
        raise AuthError("Tarayıcıdan geri dönüş gelmedi (zaman aşımı).")
    return received


def open_callback_server() -> HTTPServer:
    """127.0.0.1 üzerinde dinleyici açar; 1455 doluysa boş bir port seçer."""
    try:
        return HTTPServer(("127.0.0.1", CALLBACK_PORT), BaseHTTPRequestHandler)
    except OSError:
        return HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)


# --- Takas ve kontrol --------------------------------------------------------


def validate_id_token(id_token: str, key: Any, client_id: str, nonce: str) -> dict[str, Any]:
    """İmza, yayıncı, alıcı, süre ve nonce kontrolü; geçerse içindeki bilgileri döner."""
    try:
        claims: dict[str, Any] = jwt.decode(
            id_token, key, algorithms=["RS256"], audience=client_id, issuer=ISSUER
        )
    except jwt.InvalidTokenError as e:
        raise AuthError(f"ID token doğrulanamadı: {e}") from e
    if claims.get("nonce") != nonce:
        raise AuthError("ID token'daki nonce bu girişe ait değil.")
    return claims


def signing_key_for(id_token: str) -> Any:
    """ID token'ı imzalayan anahtarı OpenAI'ın yayımladığı listeden (JWKS) bulur."""
    return jwt.PyJWKClient(JWKS_URL).get_signing_key_from_jwt(id_token).key


def apply_token_response(credentials: Credentials, data: dict[str, Any]) -> Credentials:
    """Token yanıtını kayda işler. Plan kullanım izni verilmediyse reddeder."""
    scopes = str(data.get("scope", "")).split()
    if PLAN_SCOPE not in scopes:
        raise AuthError(
            "ChatGPT planıyla model kullanma izni verilmedi. "
            "Planınız uygun değil ya da izin ekranında reddedilmiş olabilir."
        )
    return credentials.model_copy(
        update={
            "access_token": data["access_token"],
            # Yenileme belirteci her kullanımda değişir; yenisi gelmediyse eskisi kalır.
            "refresh_token": data.get("refresh_token", credentials.refresh_token),
            "id_token": data.get("id_token", credentials.id_token),
            "expires_at": time.time() + float(data.get("expires_in", 3600)),
            "scopes": scopes,
        }
    )


def _token_error(response: httpx.Response) -> AuthError:
    try:
        body: dict[str, Any] = response.json()
        detail = body.get("error_description") or body.get("error") or response.text
    except ValueError:
        detail = response.text
    return AuthError(f"Token isteği başarısız (HTTP {response.status_code}): {detail}")


def login(
    path: Path = DEFAULT_PATH,
    open_browser: Callable[[str], object] = webbrowser.open,
    on_url: Callable[[str], None] = lambda url: None,
) -> Credentials:
    """Tarayıcıyla girişin tamamı. Başarılıysa bilgiyi diske yazar ve döner."""
    credentials = load_credentials(path)
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    with open_callback_server() as server:
        redirect_uri = f"http://127.0.0.1:{server.server_port}{CALLBACK_PATH}"
        url = authorize_url(credentials, redirect_uri, state, nonce, pkce_challenge(verifier))
        # Adres yalnız tarayıcı açılamazsa gösterilir: içinde kimlik bilgisi (id_token_hint) var.
        if not open_browser(url):
            on_url(url)
        params = wait_for_callback(server)

    code, client_id = parse_callback(params, state, credentials)
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": RESOURCE,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise _token_error(response)
    data: dict[str, Any] = response.json()

    claims = validate_id_token(
        data["id_token"], signing_key_for(data["id_token"]), client_id, nonce
    )
    credentials = credentials.model_copy(
        update={"client_id": client_id, "email": claims.get("email")}
    )
    credentials = apply_token_response(credentials, data)
    save_credentials(credentials, path)
    return credentials


# --- Kullanım sırasında: geçerli belirteç ------------------------------------


class ChatGPTTokens:
    """Model adaptörüne her istekte geçerli bir erişim belirteci verir.

    Süresi dolmak üzereyse (son 1 dakika) önce yeniler ve yenisini diske yazar.
    """

    def __init__(self, http: httpx.AsyncClient, path: Path = DEFAULT_PATH) -> None:
        self._http = http
        self._path = path

    async def access_token(self) -> str:
        credentials = load_credentials(self._path)
        if credentials.access_token is None or credentials.client_id is None:
            raise AuthError("Önce giriş yapın: harness login")
        if credentials.expires_at - 60 > time.time():
            return credentials.access_token
        return (await self._refresh(credentials)).access_token or ""

    async def _refresh(self, credentials: Credentials) -> Credentials:
        if credentials.refresh_token is None:
            raise AuthError("Oturumun süresi doldu; yeniden giriş yapın: harness login")
        response = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": credentials.client_id or "",
                "refresh_token": credentials.refresh_token,
                "resource": RESOURCE,
            },
        )
        if response.status_code != 200:
            raise _token_error(response)
        credentials = apply_token_response(credentials, response.json())
        save_credentials(credentials, self._path)
        return credentials
