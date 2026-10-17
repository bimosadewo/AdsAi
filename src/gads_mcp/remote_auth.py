"""Login untuk AdsAi online: OAuth 2.1 (dengan PKCE + pendaftaran klien dinamis) untuk Claude, yang
mendelegasikan identitas ke "Sign in with Google" dan hanya mengizinkan email di ADSAI_ALLOWED_EMAILS.

Alur:
  Claude ──/authorize──▶ AdsAi ──302──▶ Google login ──/auth/google/callback──▶ AdsAi
        ◀──302 redirect_uri?code=…── (bila email diizinkan)   ──/token──▶ access + refresh token

AdsAi menerbitkan token sendiri (acak 256-bit). Token TIDAK pernah dikirim ke Google Ads; kredensial Google
Ads tetap dari Secret Manager. Penyimpanan: satu berkas JSON (ADSAI_AUTH_STORE, mis. di volume Cloud
Storage) — token disimpan sebagai hash SHA-256, bukan teks asli.

Variabel lingkungan:
  ADSAI_PUBLIC_URL           https://adsai-xxxx.run.app (tanpa garis miring di akhir)
  ADSAI_LOGIN_CLIENT_ID      OAuth client Google tipe "Web application" (khusus login)
  ADSAI_LOGIN_CLIENT_SECRET
  ADSAI_ALLOWED_EMAILS       daftar email dipisah koma, mis. budi3pilar@gmail.com
  ADSAI_AUTH_STORE           path berkas penyimpanan (default /data/auth.json)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

SCOPE = "adsai"
ACCESS_TTL = 3600                 # 1 jam
REFRESH_TTL = 30 * 24 * 3600      # 30 hari
CODE_TTL = 300
PENDING_TTL = 600
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
CALLBACK_PATH = "/auth/google/callback"


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _http_json(url: str, data: dict[str, str] | None = None, timeout: float = 15) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — URL tetap (Google)
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as ex:
        try:
            return {"error": json.loads(ex.read().decode())}
        except Exception:  # noqa: BLE001
            return {"error": f"HTTP {ex.code}"}


class JsonStore:
    """Penyimpanan kecil berbasis satu berkas JSON. Cocok untuk 1 instance (Cloud Run max-instances=1)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data: dict[str, dict[str, Any]] = {"clients": {}, "pending": {}, "codes": {}, "access": {}, "refresh": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                for k in self.data:
                    self.data[k] = loaded.get(k, {})
            except (OSError, ValueError):
                pass  # berkas rusak → mulai bersih; pengguna cukup login ulang

    def save(self) -> None:
        with self._lock:
            now = time.time()
            for k in ("pending", "codes", "access", "refresh"):
                self.data[k] = {key: v for key, v in self.data[k].items()
                                if not v.get("expires_at") or v["expires_at"] > now}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(tmp, self.path)


class GoogleLoginProvider:
    """OAuthAuthorizationServerProvider untuk MCPServer — login Google + daftar email."""

    def __init__(self, public_url: str, login_client_id: str, login_client_secret: str,
                 allowed_emails: list[str], store: JsonStore,
                 http_json: Callable[..., dict[str, Any]] = _http_json):
        self.public_url = public_url.rstrip("/")
        self.client_id = login_client_id
        self.client_secret = login_client_secret
        self.allowed = {e.strip().lower() for e in allowed_emails if e.strip()}
        self.store = store
        self.http_json = http_json
        if not self.allowed:
            raise ValueError("ADSAI_ALLOWED_EMAILS kosong — server online tanpa daftar email ditolak.")

    @property
    def callback_url(self) -> str:
        return self.public_url + CALLBACK_PATH

    # ----- pendaftaran klien (Claude mendaftar otomatis) -----

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = self.store.data["clients"].get(client_id)
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.store.data["clients"][client_info.client_id] = client_info.model_dump(mode="json")
        self.store.save()

    # ----- otorisasi: arahkan ke Google -----

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        txn = secrets.token_urlsafe(32)
        self.store.data["pending"][txn] = {"client_id": client.client_id,
                                           "params": params.model_dump(mode="json"),
                                           "expires_at": time.time() + PENDING_TTL}
        self.store.save()
        q = urllib.parse.urlencode({"client_id": self.client_id, "redirect_uri": self.callback_url,
                                    "response_type": "code", "scope": "openid email", "state": txn,
                                    "prompt": "select_account"})
        return f"{GOOGLE_AUTH}?{q}"

    async def handle_callback(self, request: Request) -> Response:
        txn = request.query_params.get("state", "")
        pending = self.store.data["pending"].pop(txn, None)
        self.store.save()
        if not pending or pending["expires_at"] < time.time():
            return _page("Sesi login kedaluwarsa", "Ulangi dari Claude: buka connector AdsAi lalu klik Connect.", 400)
        params = AuthorizationParams.model_validate(pending["params"])
        if request.query_params.get("error"):
            return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), error="access_denied",
                                                           state=params.state), status_code=302)
        email = await self._verify_google(request.query_params.get("code", ""))
        if not email:
            return _page("Login Google gagal", "Google tidak mengonfirmasi akun Anda. Coba lagi.", 400)
        if email not in self.allowed:
            return _page("Akses ditolak", f"Akun {email} tidak terdaftar untuk AdsAi. Hubungi pemilik agensi.", 403)
        code = secrets.token_urlsafe(32)
        self.store.data["codes"][_h(code)] = {
            "client_id": pending["client_id"], "scopes": params.scopes or [SCOPE],
            "code_challenge": params.code_challenge, "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource, "subject": email, "expires_at": time.time() + CODE_TTL}
        self.store.save()
        return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state),
                                status_code=302)

    async def _verify_google(self, code: str) -> str | None:
        import anyio

        if not code:
            return None
        tok = await anyio.to_thread.run_sync(lambda: self.http_json(GOOGLE_TOKEN, {
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": self.callback_url, "grant_type": "authorization_code"}))
        id_token = tok.get("id_token")
        if not id_token:
            return None
        info = await anyio.to_thread.run_sync(
            lambda: self.http_json(f"{GOOGLE_TOKENINFO}?{urllib.parse.urlencode({'id_token': id_token})}"))
        if info.get("aud") != self.client_id or str(info.get("email_verified")).lower() != "true":
            return None
        return str(info.get("email", "")).lower() or None

    # ----- kode → token -----

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        raw = self.store.data["codes"].get(_h(authorization_code))
        if not raw or raw["client_id"] != client.client_id or raw["expires_at"] < time.time():
            return None
        return AuthorizationCode(code=authorization_code, **raw)

    def _issue(self, client_id: str, scopes: list[str], resource: str | None, subject: str | None) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        common = {"client_id": client_id, "scopes": scopes, "resource": resource, "subject": subject}
        self.store.data["access"][_h(access)] = {**common, "expires_at": now + ACCESS_TTL, "refresh": _h(refresh)}
        self.store.data["refresh"][_h(refresh)] = {**common, "expires_at": now + REFRESH_TTL, "access": _h(access)}
        self.store.save()
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, scope=" ".join(scopes), refresh_token=refresh)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        if self.store.data["codes"].pop(_h(authorization_code.code), None) is None:
            raise TokenError("invalid_grant", "Kode otorisasi sudah dipakai atau kedaluwarsa.")
        if authorization_code.subject not in self.allowed:
            raise TokenError("invalid_grant", "Akun tidak lagi diizinkan.")
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource,
                           authorization_code.subject)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        raw = self.store.data["refresh"].get(_h(refresh_token))
        if not raw or raw["client_id"] != client.client_id or raw["expires_at"] < time.time():
            return None
        return RefreshToken(token=refresh_token, **{k: raw[k] for k in
                                                   ("client_id", "scopes", "expires_at", "resource", "subject")})

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        old = self.store.data["refresh"].pop(_h(refresh_token.token), None)
        if old is None:
            raise TokenError("invalid_grant", "Refresh token tidak berlaku.")
        self.store.data["access"].pop(old.get("access", ""), None)
        if refresh_token.subject not in self.allowed:
            self.store.save()
            raise TokenError("invalid_grant", "Akun tidak lagi diizinkan.")
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource,
                           refresh_token.subject)

    async def load_access_token(self, token: str) -> AccessToken | None:
        raw = self.store.data["access"].get(_h(token))
        if not raw or raw["expires_at"] < time.time() or raw.get("subject") not in self.allowed:
            return None
        return AccessToken(token=token, **{k: raw[k] for k in
                                           ("client_id", "scopes", "expires_at", "resource", "subject")})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        h = _h(token.token)
        a = self.store.data["access"].pop(h, None)
        r = self.store.data["refresh"].pop(h, None)
        if a:
            self.store.data["refresh"].pop(a.get("refresh", ""), None)
        if r:
            self.store.data["access"].pop(r.get("access", ""), None)
        self.store.save()


def _page(title: str, msg: str, status: int) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                        f"<title>AdsAi</title><body style='font:16px system-ui;max-width:520px;margin:15vh auto;"
                        f"padding:0 16px'><h2>{title}</h2><p>{msg}</p></body>", status_code=status)


def from_env() -> tuple[GoogleLoginProvider, AuthSettings] | None:
    """None bila ADSAI_PUBLIC_URL tidak diisi (mode lokal/stdio)."""
    public = os.getenv("ADSAI_PUBLIC_URL", "").strip().rstrip("/")
    if not public:
        return None
    missing = [k for k in ("ADSAI_LOGIN_CLIENT_ID", "ADSAI_LOGIN_CLIENT_SECRET", "ADSAI_ALLOWED_EMAILS")
               if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Mode online butuh variabel: {', '.join(missing)}")
    provider = GoogleLoginProvider(public, os.environ["ADSAI_LOGIN_CLIENT_ID"].strip(), os.environ["ADSAI_LOGIN_CLIENT_SECRET"].strip(),
                                   os.environ["ADSAI_ALLOWED_EMAILS"].split(","),
                                   JsonStore(Path(os.getenv("ADSAI_AUTH_STORE") or "/data/auth.json")))
    settings = AuthSettings(
        issuer_url=public, resource_server_url=f"{public}/mcp", validate_token_resource=False,
        client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE],
                                                              default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True), required_scopes=[SCOPE])
    return provider, settings
