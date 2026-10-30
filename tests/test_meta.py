from datetime import date

import pytest

from gads_mcp import meta
from test_gbp import Resp, Session

ROW = {"campaign_id": "1", "campaign_name": "WA Tangerang", "spend": "150000", "impressions": "20000",
       "reach": "5000", "frequency": "4", "clicks": "300", "inline_link_clicks": "250", "ctr": "1.5", "cpc": "500",
       "cpm": "7500", "date_start": "2026-10-01", "date_stop": "2026-10-29",
       "actions": [{"action_type": "link_click", "value": "250"},
                   {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "30"},
                   {"action_type": "lead", "value": "5"}, {"action_type": "offsite_conversion.fb_pixel_lead",
                                                          "value": "5"}]}


def test_clean_results_and_cost():
    r = meta.clean(ROW)
    assert r["biaya"] == 150000 and r["frekuensi"] == 4.0 and r["klik_link"] == 250
    assert r["hasil"] == {"percakapan_wa": 30, "prospek": 5}          # lead dihitung sekali
    assert r["biaya_per_hasil"] == {"percakapan_wa": 5000, "prospek": 30000}
    assert "date_start" not in r and r["campaign_name"] == "WA Tangerang"
    assert meta.clean({})["hasil"] == {}


def test_budget_currency_units():
    assert meta.budget({"daily_budget": "50000"}, "IDR") == {"harian": 50000}
    assert meta.budget({"lifetime_budget": "1050"}, "USD") == {"total": 10.5}


def test_ranges_and_accounts():
    assert meta.ranges(7, date(2026, 10, 30)) == (("2026-10-23", "2026-10-29"), ("2026-10-16", "2026-10-22"))
    accs = [{"akun": "act_11", "nama": "Tiga Pilar"}, {"akun": "act_22", "nama": "Klien B"}]
    assert meta.pick_account(accs, "22")["nama"] == "Klien B"
    assert meta.pick_account(accs, "tiga")["akun"] == "act_11"
    with pytest.raises(meta.MetaError, match="beberapa"):
        meta.pick_account(accs, None)
    assert meta.account_id("act_123") == "act_123" and meta.account_id(123) == "act_123"


def test_findings():
    rows = [meta.clean(ROW), meta.clean({**ROW, "campaign_name": "Boros", "frequency": "1", "actions": []})]
    places = [{"publisher_platform": "facebook", "biaya": 70}, {"publisher_platform": "audience_network",
                                                                "biaya": 30}]
    f = meta.findings(rows, places)
    assert any("'Boros'" in x and "tanpa hasil" in x for x in f)
    assert any("frekuensi 4.0" in x for x in f)
    assert any("30% biaya tayang di Audience Network" in x for x in f)


def test_errors():
    assert "Token tidak valid" in meta.explain(400, {"error": {"code": 190, "message": "expired"}})
    assert "ads_read" in meta.explain(403, {"error": {"code": 200, "message": "perm"}})
    assert "Batas panggilan" in meta.explain(400, {"error": {"code": 17, "message": "limit"}})

    class S:
        meta_access_token = ""
    with pytest.raises(meta.MetaError, match="META_ACCESS_TOKEN"):
        meta.Meta.from_settings(S())
    with pytest.raises(meta.MetaError, match="Breakdown"):
        meta.Meta(Session({}), "t").insights("1", "2026-10-01", "2026-10-02", breakdowns=["hobi"])


def test_pagination_and_token_param():
    pages = Session({("GET", "/me/adaccounts"): {"data": [{"id": "act_1", "name": "A", "account_status": 1,
                                                            "currency": "IDR"}],
                                                   "paging": {"next": "https://graph.facebook.com/v26.0/next"}},
                     ("GET", "/next"): {"data": [{"id": "act_2", "name": "B", "account_status": 3}]}})
    accs = meta.Meta(pages, "TOKEN").ad_accounts()
    assert [a["akun"] for a in accs] == ["act_1", "act_2"] and accs[1]["status"] == "TUNGGAKAN"
    assert pages.calls[0][2]["access_token"] == "TOKEN" and pages.calls[1][2] is None
    assert "/v26.0/me/adaccounts" in pages.calls[0][1]


def test_server_performance(monkeypatch, tmp_path):
    from fakes import FakeAds

    from gads_mcp import server
    from gads_mcp.config import Safety

    def request(method, url, params=None, json=None, timeout=None):
        calls.append(params)
        if url.endswith("/me/adaccounts"):
            return Resp(200, {"data": [{"id": "act_9", "name": "Tiga Pilar", "account_status": 1, "currency": "IDR"}]})
        if url.endswith("/act_9/campaigns"):
            return Resp(200, {"data": [{"id": "1", "effective_status": "ACTIVE", "objective": "OUTCOME_ENGAGEMENT",
                                        "daily_budget": "50000"}]})
        if url.endswith("/act_9/insights"):
            if params.get("breakdowns"):
                return Resp(200, {"data": [{**ROW, "publisher_platform": "instagram"}]})
            return Resp(200, {"data": [ROW]})
        return Resp(404, {"error": {"code": 100, "message": "x"}})

    calls: list = []
    session = Session({})
    session.request = request
    monkeypatch.setattr(server, "_ads", FakeAds(safety=Safety(audit_log_dir=tmp_path)))
    monkeypatch.setattr(server, "_meta", meta.Meta(session, "t"))
    out = server.meta_performance(days=7)
    assert out["nama"] == "Tiga Pilar" and out["total"]["biaya"] == 150000
    assert out["kampanye"][0]["budget"] == {"harian": 50000} and out["kampanye"][0]["status"] == "ACTIVE"
    assert out["porsi_biaya_per_platform"] == {"instagram": "100%"} and out["dibanding_sebelumnya"]["biaya"] == "+0%"
    detail = server.meta_insights(breakdowns=["age"], level="account")
    assert detail["jumlah_baris"] == 1
    assert server.meta_status()["status"] == "SIAP"
