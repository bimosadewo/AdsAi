"""Dijalankan sebagai subprocess oleh test_remote_server.py: server online dengan Google palsu."""
import sys

from gads_mcp import server

ALLOWED, BLOCKED = "budi3pilar@gmail.com", "orang.lain@gmail.com"


def fake_google(url, data=None, timeout=15):
    if data is not None:                       # tukar kode Google → id_token
        return {"id_token": "idtok-" + data["code"]}
    email = BLOCKED if "idtok-blocked" in url else ALLOWED
    return {"aud": "login-client", "email_verified": "true", "email": email}


server._remote[0].http_json = fake_google
sys.argv = ["adsai-server"]
server.main_http()
