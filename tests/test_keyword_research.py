from types import SimpleNamespace as NS

import pytest

from gads_mcp import keyword_research as kr

from fakes import FakeAds


def idea(text, vol, low=0, high=0, comp="LOW"):
    return NS(text=text, keyword_idea_metrics=NS(avg_monthly_searches=vol, competition=NS(name=comp),
                                                low_top_of_page_bid_micros=low * 1_000_000,
                                                high_top_of_page_bid_micros=high * 1_000_000,
                                                average_cpc_micros=0))


IDEAS = [
    idea("geopipe", 320, 5000, 36000),
    idea("harga pipa hdpe corrugated", 210, 4000, 20000, "MEDIUM"),
    idea("pipa corrugated adalah", 90, 500, 2000),
    idea("pipa pvc", 9000, 1000, 5000, "HIGH"),
    idea("Pipa  Subdrain", 140, 3000, 12000),
    idea("pipa subdrain", 140, 3000, 12000),        # duplikat setelah normalisasi
]


def test_language_and_normalize():
    assert kr.language_id("id") == "1025" and kr.language_id("1000") == "1000"
    with pytest.raises(ValueError):
        kr.language_id("klingon")
    rows = kr.normalize(IDEAS)
    assert len(rows) == 5 and rows[0]["bid_tinggi"] == 36000 and rows[0]["persaingan"] == "LOW"


def test_analyze_keeps_seed_labels_intent_and_filters():
    rows = kr.normalize(IDEAS) + [{"kata_kunci": "pipa hdpe double wall", "pencarian_bulanan": 0, "persaingan": "-",
                                   "bid_rendah": None, "bid_tinggi": None, "cpc_rata2": None}]
    out = kr.analyze(rows, ["pipa hdpe double wall", "geopipe"], include_terms=["geopipe", "corrugated", "subdrain"])
    by = {r["kata_kunci"]: r for r in out["kata_kunci"]}
    assert "pipa pvc" not in by and out["dibuang"][0]["kata_kunci"] == "pipa pvc"      # melenceng dari produk
    assert by["pipa hdpe double wall"]["seed"] and by["pipa hdpe double wall"]["catatan"] == "volume tidak terukur"
    assert by["pipa corrugated adalah"]["niat"] == "INFORMASI"
    assert by["harga pipa hdpe corrugated"]["niat"] == "BELI"
    assert out["kata_kunci"][0]["seed"]                                                  # seed di urutan atas
    assert "pipa corrugated adalah" in out["kandidat_negatif"]
    assert out["budget_harian_rp"]["10_klik_per_hari"] > 0
    assert kr.analyze([], ["x"])["catatan"].startswith("Google tidak mengembalikan")


def test_run_builds_requests():
    ads = FakeAds()
    ads.extras["generate_keyword_ideas"] = IDEAS
    ads.extras["generate_keyword_historical_metrics"] = NS(results=[NS(
        text="pipa hdpe double wall", keyword_metrics=NS(avg_monthly_searches=30, competition=NS(name="LOW"),
                                                         low_top_of_page_bid_micros=0, high_top_of_page_bid_micros=0,
                                                         average_cpc_micros=0))])
    out = kr.KeywordResearch(ads).run("5827572362", ["geopipe", "pipa hdpe double wall"], url="https://tigapilar.co.id")
    (n1, req), (n2, hreq) = ads.calls
    assert n1 == "generate_keyword_ideas" and req.language == "languageConstants/1025"
    assert list(req.geo_target_constants) == ["geoTargetConstants/2360"]
    assert req.keyword_and_url_seed.url == "https://tigapilar.co.id"
    assert n2 == "generate_keyword_historical_metrics" and list(hreq.keywords) == ["geopipe", "pipa hdpe double wall"]
    texts = [r["kata_kunci"] for r in out["kata_kunci"]]
    assert "pipa hdpe double wall" in texts and out["bahasa"] == "languageConstants/1025"
    with pytest.raises(ValueError):
        kr.KeywordResearch(ads).run("1", [], None)
