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


def kw(text, match, qs, cost, clicks, conv, ctr="AVERAGE", rel="ABOVE_AVERAGE", lp="BELOW_AVERAGE", cid="1", name="Plastik Cor"):
    p = "ad_group_criterion.quality_info."
    return {"campaign.id": cid, "campaign.name": name, "ad_group_criterion.keyword.text": text,
            "ad_group_criterion.keyword.match_type": match, p + "quality_score": qs, p + "search_predicted_ctr": ctr,
            p + "creative_quality_score": rel, p + "post_click_quality_score": lp, "metrics.cost": cost,
            "metrics.clicks": clicks, "metrics.conversions": conv}


def test_keyword_insights_low_qs_landing_page_and_ai_max():
    kws = [kw("harga plastik cor", "PHRASE", 5, 64253, 10, 1),
           kw("plastik cor", "EXACT", 3, 37219, 7, 0, ctr="BELOW_AVERAGE"),
           kw("harga plastik cor per roll", "EXACT", 7, 5392, 2, 0, ctr="ABOVE_AVERAGE")]
    camps = [{"id": "1", "nama": "Plastik Cor", "biaya": 250525.0, "klik": 52.0, "konversi": 1.0, "ai_max": True}]
    out = db.keyword_insights(kws, camps)
    low, lp, ai = out
    assert low["lencana"] == "QS 3" and '"plastik cor" (EXACT)' in low["pesan"]
    assert "perkiraan CTR, landing page" in low["pesan"] and "plastik cor" in low["aksi"]["prompt"]
    assert "3 dari 3" in lp["pesan"]
    assert "0 konversi." in low["pesan"]
    assert "57% biaya" in ai["pesan"] and "AI Max aktif" in ai["pesan"] and "33 klik" in ai["pesan"]
    assert ai["level"] == "PERINGATAN"  # 0 konversi di luar kata kunci
    assert db.highlight(db.keyword_insights(kws, camps) + [{"level": "INFO", "pesan": "x"}]) == low


def test_keyword_insights_quiet_when_healthy():
    kws = [kw("plastik cor", "EXACT", 8, 50000, 10, 1, lp="AVERAGE")]
    camps = [{"id": "1", "nama": "Plastik Cor", "biaya": 52000.0, "klik": 10.0, "konversi": 1.0, "ai_max": False}]
    assert db.keyword_insights(kws, camps) == []
    assert db.highlight([{"level": "INFO", "pesan": "x"}]) is None


def test_html_has_highlight_actions_and_60_days():
    h = db.html()
    assert 'data-days="60"' in h and 'id="hl"' in h and "sendMessage" in h and 'id="b-insight"' in h


def test_app_script_parses_with_bundle(tmp_path):
    """Skrip kartu + SDK harus lolos parse sebagai satu module (nama ganda = SyntaxError di browser)."""
    import re as _re
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node tidak tersedia")
    src = _re.search(r'<script type="module">(.*)</script>', db.html(), _re.S).group(1)
    f = tmp_path / "card.mjs"
    f.write_text(src, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_ad_groups_and_top_keywords():
    ag = [{"campaign.name": "Plastik Cor", "ad_group.name": "Kebutuhan Proyek", "metrics.cost": 60496.36,
           "metrics.clicks": 19, "metrics.impressions": 300, "metrics.conversions": 0},
          {"campaign.name": "Plastik Cor", "ad_group.name": "Plastik Cor Umum", "metrics.cost": 103779.15,
           "metrics.clicks": 18, "metrics.impressions": 400, "metrics.conversions": 1}]
    g = db.ad_groups(ag)
    assert [x["nama"] for x in g] == ["Plastik Cor Umum", "Kebutuhan Proyek"] and g[0]["konversi"] == 1
    k = db.top_keywords([kw("plastik cor", "EXACT", 3, 37219, 7, 0, ctr="BELOW_AVERAGE"),
                         kw("harga plastik cor", "PHRASE", 5, 64253, 10, 1), kw("nol", "EXACT", 5, 0, 0, 0)])
    assert [x["teks"] for x in k] == ["harga plastik cor", "plastik cor"]
    assert k[1]["lemah"] == ["perkiraan CTR", "landing page"]
