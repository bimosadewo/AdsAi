import json
from pathlib import Path

import pytest
import yaml

from gads_mcp.clients import Client, find_client, load_clients, normalize_phone, parse_clients
from gads_mcp.notify import Email, WhatsApp, deliver

ROOT = Path(__file__).resolve().parents[1]


def test_example_yaml_is_valid():
    clients = load_clients(ROOT / "clients.example.yaml")
    tp = clients["tigapilar"]
    assert tp.customer_id == "5827572362"
    assert tp.thresholds.min_waste_cost == 20000
    assert "plastik cor" in tp.protected_terms


def test_parse_clients_overrides_and_errors():
    data = {"defaults": {"thresholds": {"zero_conversion_days": 3}},
            "clients": {"a": {"customer_id": "111-222-3333", "whatsapp": "0812-3456-7890",
                              "thresholds": {"zero_conversion_days": 5}}}}
    a = parse_clients(data)["a"]
    assert a.whatsapp == ("6281234567890",) and a.thresholds.zero_conversion_days == 5
    with pytest.raises(ValueError):
        parse_clients({"clients": {"b": {"customer_id": "1112223333", "thresholds": {"typo": 1}}}})
    with pytest.raises(ValueError):
        parse_clients({"clients": {"c": {}}})


def test_normalize_phone():
    assert normalize_phone("+62 812-3456-7890") == "6281234567890"
    with pytest.raises(ValueError):
        normalize_phone("12345")


def test_find_client():
    clients = parse_clients(yaml.safe_load("""
clients:
  tigapilar: {name: PT Tiga Pilar, customer_id: 5827572362}
  habib: {name: Habib Aqiqah, customer_id: 1112223333}
"""))
    assert find_client(clients, "tigapilar").name == "PT Tiga Pilar"
    assert find_client(clients, "582-757-2362").key == "tigapilar"
    assert find_client(clients, "aqiqah").key == "habib"
    with pytest.raises(ValueError):
        find_client(clients, "tidak ada")


def test_whatsapp_console_does_not_send(capsys):
    r = WhatsApp(env={}).send("6281", "halo")
    assert r.ok and "console" in r.channel
    assert "halo" in capsys.readouterr().out


def test_whatsapp_fonnte_request():
    seen = {}

    def transport(req):
        seen["url"], seen["auth"], seen["body"] = req.full_url, req.get_header("Authorization"), req.data
        return 200, '{"status": true}'

    r = WhatsApp(env={"ADSAI_WA_PROVIDER": "fonnte", "ADSAI_FONNTE_TOKEN": "tok"}, transport=transport).send("628", "hi & bye")
    assert r.ok
    assert seen["url"] == "https://api.fonnte.com/send" and seen["auth"] == "tok"
    assert b"target=628" in seen["body"] and b"hi+%26+bye" in seen["body"]
    bad = WhatsApp(env={"ADSAI_WA_PROVIDER": "fonnte", "ADSAI_FONNTE_TOKEN": "t"},
                   transport=lambda req: (200, '{"status":false,"reason":"invalid token"}')).send("628", "x")
    assert not bad.ok


def test_whatsapp_webhook_template_escapes_message():
    seen = {}

    def transport(req):
        seen["body"] = json.loads(req.data)
        seen["key"] = req.get_header("X-api-key")
        return 200, "ok"

    env = {"ADSAI_WA_PROVIDER": "webhook", "ADSAI_WA_WEBHOOK_URL": "https://gw.example/send",
           "ADSAI_WA_WEBHOOK_HEADERS": '{"X-Api-Key": "k"}',
           "ADSAI_WA_WEBHOOK_BODY": '{"api_key": "k", "phone": "{phone}", "text": "{message}"}'}
    msg = 'Laporan "minggu" ini\nbaris 2'
    assert WhatsApp(env=env, transport=transport).send("628", msg).ok
    assert seen["body"] == {"api_key": "k", "phone": "628", "text": msg}
    assert seen["key"] == "k"


def test_email_and_deliver():
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, m): sent.append(m)

    email = Email(env={"ADSAI_SMTP_HOST": "smtp", "ADSAI_SMTP_FROM": "a@b.c"}, smtp_factory=FakeSMTP)
    client = Client("k", "Klien", "1112223333", whatsapp=("628",), email=("x@y.z",))
    results = deliver(client, "Subjek", "*Halo*", WhatsApp(env={}), email)
    assert [r.ok for r in results] == [True, True]
    assert sent[0]["To"] == "x@y.z" and "*" not in sent[0].get_content()
    empty = deliver(Client("k", "K", "1112223333"), "s", "m", WhatsApp(env={}), Email(env={}))
    assert not empty[0].ok
