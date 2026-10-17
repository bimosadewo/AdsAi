"""Uji ujung-ke-ujung mode online: discovery → register → authorize → login Google (palsu) → token → /mcp."""
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).parent


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


opener = urllib.request.build_opener(NoRedirect)


def call(url, data=None, headers=None, method=None, form=False):
    body = None
    h = dict(headers or {})
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method or ("POST" if body else "GET"))
    try:
        r = opener.open(req, timeout=10)
        return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as ex:
        return ex.code, dict(ex.headers), ex.read().decode()


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("remote")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "PORT": str(port), "ADSAI_PUBLIC_URL": url, "ADSAI_LOGIN_CLIENT_ID": "login-client",
           "ADSAI_LOGIN_CLIENT_SECRET": "login-secret", "ADSAI_ALLOWED_EMAILS": "Budi3pilar@gmail.com",
           "ADSAI_AUTH_STORE": str(tmp / "auth.json"), "GOOGLE_ADS_ENV_FILE": str(tmp / "none.env"),
           "PYTHONPATH": str(HERE) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.Popen([sys.executable, str(HERE / "remote_server_fixture.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            if call(url + "/healthz")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError(proc.stdout.read().decode()[-3000:])
    yield url, tmp
    proc.terminate(); proc.wait(5)


def pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login(url, google_code="ok"):
    st, _, body = call(url + "/register", {"redirect_uris": ["http://localhost:9999/cb"], "client_name": "Claude",
                                           "token_endpoint_auth_method": "none",
                                           "grant_types": ["authorization_code", "refresh_token"]})
    assert st == 201, body
    client = json.loads(body)
    verifier, challenge = pkce()
    q = urllib.parse.urlencode({"response_type": "code", "client_id": client["client_id"],
                                "redirect_uri": "http://localhost:9999/cb", "code_challenge": challenge,
                                "code_challenge_method": "S256", "state": "xyz", "scope": "adsai",
                                "resource": url + "/mcp"})
    st, h, _ = call(f"{url}/authorize?{q}")
    assert st == 302 and h["location"].startswith("https://accounts.google.com/"), (st, h)
    google = urllib.parse.parse_qs(urllib.parse.urlparse(h["location"]).query)
    assert google["redirect_uri"] == [url + "/auth/google/callback"] and google["scope"] == ["openid email"]
    st, h, body = call(f"{url}/auth/google/callback?code={google_code}&state={google['state'][0]}")
    return client, verifier, st, h, body


def test_metadata_and_unauthenticated(base):
    url, _ = base
    st, _, body = call(url + "/.well-known/oauth-authorization-server")
    meta = json.loads(body)
    assert st == 200 and meta["registration_endpoint"].endswith("/register") and "S256" in meta["code_challenge_methods_supported"]
    st, h, _ = call(url + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    {"Accept": "application/json, text/event-stream"})
    assert st == 401 and "resource_metadata" in h.get("www-authenticate", "")


def test_full_login_and_mcp_call(base):
    url, tmp = base
    client, verifier, st, h, _ = login(url)
    assert st == 302
    back = urllib.parse.parse_qs(urllib.parse.urlparse(h["location"]).query)
    assert back["state"] == ["xyz"]
    st, _, body = call(url + "/token", {"grant_type": "authorization_code", "code": back["code"][0],
                                        "redirect_uri": "http://localhost:9999/cb", "client_id": client["client_id"],
                                        "code_verifier": verifier, "resource": url + "/mcp"}, form=True)
    assert st == 200, body
    tok = json.loads(body)
    assert tok["refresh_token"] and tok["expires_in"] == 3600
    stored = (tmp / "auth.json").read_text()
    assert tok["access_token"] not in stored and tok["refresh_token"] not in stored    # hanya hash

    hdr = {"Authorization": f"Bearer {tok['access_token']}", "Accept": "application/json, text/event-stream"}
    st, h, body = call(url + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}, hdr)
    assert st == 200, body
    sid = h.get("mcp-session-id")
    hdr2 = {**hdr, **({"mcp-session-id": sid} if sid else {})}
    call(url + "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, hdr2)
    st, _, body = call(url + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, hdr2)
    assert st == 200 and "performance_dashboard" in body and "research_keywords" in body

    # refresh token berputar: token lama tidak bisa dipakai lagi
    st, _, body = call(url + "/token", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                        "client_id": client["client_id"]}, form=True)
    assert st == 200, body
    st, _, _ = call(url + "/token", {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                     "client_id": client["client_id"]}, form=True)
    assert st == 400
    st, _, _ = call(url + "/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, hdr2)
    assert st == 401                                                     # access token lama ikut dicabut


def test_email_not_allowed_is_rejected(base):
    url, _ = base
    _, _, st, _, body = login(url, google_code="blocked")
    assert st == 403 and "tidak terdaftar" in body


def test_replayed_google_state_rejected(base):
    url, _ = base
    st, _, body = call(url + "/auth/google/callback?code=ok&state=palsu")
    assert st == 400 and "kedaluwarsa" in body
