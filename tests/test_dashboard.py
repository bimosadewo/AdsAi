import asyncio
import json
from datetime import date

import pytest

from gads_mcp import dashboard as db

from fakes import FakeAds

CID = "5827572362"


def row(cid, name, d, cost, clicks, impr, conv):
    return {"campaign.id": cid, "campaign.name": name, "segments.date": d, "metrics.cost": cost,
            "metrics.clicks": clicks, "metrics.impressions": impr, "metrics.conversions": conv}


def test_period():
    s, e, ps, pe = db.period(7, today=date(2026, 10, 17))
    assert (s, e, ps, pe) == (date(2026, 10, 10), date(2026, 10, 16), date(2026, 10, 3), date(2026, 10, 9))
    s, e, ps, pe = db.period(7, start="2026-10-16", end="2026-10-16")
    assert (s, e, ps, pe) == (date(2026, 10, 16), date(2026, 10, 16), date(2026, 10, 15), date(2026, 10, 15))
    with pytest.raises(ValueError):
        db.period(7, start="2026-10-16")
    with pytest.raises(ValueError):
        db.period(0)


def test_build_data_kpis_daily_campaigns_insights():
    s, e, ps, pe = db.period(3, today=date(2026, 10, 17))
    rows = [row("1", "Plastik Cor", "2026-10-15", 50000, 10, 200, 0),
            row("1", "Plastik Cor", "2026-10-16", 52000, 12, 250, 1),
            row("2", "Lead", "2026-10-16", 27000, 7, 170, 1)]
    prev = [row("1", "Plastik Cor", "2026-10-12", 40000, 8, 100, 0)]
    share = [{"campaign.id": "1", "campaign.status": "ENABLED", "metrics.search_budget_lost_impression_share": 0.9}]
    d = db.build_data(rows, prev, share, s, e, ps, pe, CID, "PT Tiga Pilar", None, 15000)
    k = d["kpi"]
    assert k["biaya"]["nilai"] == 129000 and k["konversi"]["nilai"] == 2
    assert k["biaya_per_konversi"]["nilai"] == 64500 and k["biaya"]["perubahan_pct"] == 222.5
    assert k["konversi"]["perubahan_pct"] is None                     # periode lalu 0 → tidak dibagi nol
    assert [x["tanggal"] for x in d["harian"]] == ["2026-10-14", "2026-10-15", "2026-10-16"]
    assert d["harian"][0]["biaya"] == 0                                # hari tanpa data tetap tampil
    assert d["kampanye"][0]["nama"] == "Plastik Cor" and d["kampanye"][0]["hilang_karena_budget"] == 0.9
    texts = " ".join(i["pesan"] for i in d["insight"])
    assert "di atas target Rp15.000" in texts and "90% tayangan hilang karena budget" in texts
    assert "bukan ROAS" in texts
    assert "Rp129.000" in db.text_summary(d)


def test_zero_conversion_is_critical():
    s, e, ps, pe = db.period(1, today=date(2026, 10, 17))
    d = db.build_data([row("1", "A", "2026-10-16", 80000, 19, 400, 0)], [], [], s, e, ps, pe, CID)
    assert d["insight"][0]["level"] == "KRITIS"


def test_html_bundles_official_sdk_without_network():
    h = db.html()
    assert h.startswith("<!doctype html>") and "const __ExtApp =" in h and "export{" not in h
    assert "https://" not in h.split("<script type=\"module\">")[0]      # tanpa CDN/font eksternal
    assert "performance_dashboard" in h and 'data-days="30"' in h


def test_server_registers_app_tool(tmp_path, monkeypatch):
    from gads_mcp import server
    from gads_mcp.config import Safety

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert tools["performance_dashboard"].meta["ui"]["resourceUri"] == db.RESOURCE_URI
    res = {r.uri: r for r in asyncio.run(server.mcp.list_resources())}
    assert res[db.RESOURCE_URI].mime_type == "text/html;profile=mcp-app"

    def search(cid, q):
        if "segments.date BETWEEN '2026-10-16'" in q and "search_impression_share" not in q:
            return [row("1", "A", "2026-10-16", 79276.4, 19, 422, 2)]
        return []
    ads = FakeAds(search, Safety(audit_log_dir=tmp_path))
    monkeypatch.setattr(server, "_ads", ads)
    out = server.performance_dashboard(customer_id=CID, start="2026-10-16", end="2026-10-16")
    assert out["kpi"]["konversi"]["nilai"] == 2 and out["kpi"]["biaya_per_konversi"]["nilai"] == 39638
    json.dumps(out)
