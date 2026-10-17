import math
from datetime import date

import pytest

from gads_mcp import monitors as mon


def r(name, d, cost, clicks, impr, conv, cid="1"):
    return {"campaign.id": cid, "campaign.name": name, "segments.date": d, "metrics.cost": cost,
            "metrics.clicks": clicks, "metrics.impressions": impr, "metrics.conversions": conv}


def test_parse_conditions_text_and_formats():
    c = mon.parse_conditions(["cpa > 150.000", "ctr < 1,5", "konversi = 0", {"metrik": "spend", "operator": ">=",
                                                                              "nilai": "50000"}])
    assert [(x.metrik, x.operator, x.nilai) for x in c] == [("cpa", ">", 150000), ("ctr", "<", 1.5),
                                                            ("konversi", "=", 0), ("biaya", ">=", 50000)]
    assert mon.parse_conditions(["ctr < 1.5"])[0].nilai == 1.5
    with pytest.raises(ValueError):
        mon.parse_conditions(["roas > 2"])
    with pytest.raises(ValueError):
        mon.parse_conditions([])


def test_consecutive_days_counted_from_latest_day():
    """Adspirer test_monitor mengabaikan 'berturut-turut'; AdsAi menghitungnya sungguhan."""
    rows = [r("Plastik Cor", "2026-10-14", 90000, 18, 700, 0), r("Plastik Cor", "2026-10-15", 90000, 19, 700, 0),
            r("Plastik Cor", "2026-10-16", 70000, 15, 654, 1),
            r("Lead", "2026-10-14", 60000, 13, 200, 0, cid="2"), r("Lead", "2026-10-15", 70000, 13, 219, 0, cid="2"),
            r("Lead", "2026-10-16", 61000, 13, 219, 0, cid="2")]
    m = mon.Monitor("Boros tanpa konversi", mon.parse_conditions(["konversi = 0", "biaya > 50000"]), "5827572362",
                    berturut_hari=2)
    res = mon.evaluate(m, mon.daily_series(rows, date(2026, 10, 14), date(2026, 10, 16), None))
    assert res["terpicu"] and [p["entitas"] for p in res["pemicu"]] == ["Lead"]
    assert res["pemicu"][0]["hari_berturut"] == 3
    m.berturut_hari = 4
    assert not mon.evaluate(m, mon.daily_series(rows, date(2026, 10, 14), date(2026, 10, 16), None))["terpicu"]


def test_cpa_with_zero_conversions_is_worst_case_and_account_scope():
    rows = [r("A", "2026-10-16", 40000, 5, 100, 0), r("B", "2026-10-16", 40000, 5, 100, 0, cid="2")]
    s = mon.daily_series(rows, date(2026, 10, 16), date(2026, 10, 16), "AKUN")
    assert list(s) == ["Total akun"] and s["Total akun"][0]["biaya"] == 80000 and math.isinf(s["Total akun"][0]["cpa"])
    m = mon.Monitor("CPA tinggi", mon.parse_conditions(["cpa > 150000"]), "x", kampanye="AKUN")
    res = mon.evaluate(m, s)
    assert res["terpicu"] and res["pemicu"][0]["kemarin"]["cpa"] is None    # inf → None (JSON aman)
    only_a = mon.daily_series(rows, date(2026, 10, 16), date(2026, 10, 16), "a")
    assert list(only_a) == ["A"]


def test_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ADSAI_MONITORS_FILE", str(tmp_path / "m.json"))
    assert mon.load() == []
    m = mon.Monitor("x", mon.parse_conditions(["klik < 5"]), "123", berturut_hari=3, kampanye="Lead")
    mon.save([m])
    back = mon.load()
    assert back[0].id == m.id and back[0].berturut_hari == 3 and back[0].kondisi[0].nilai == 5
    assert "3 hari berturut-turut" in back[0].to_dict()["ringkas"]


def test_run_queries_once_per_account_with_enough_lookback():
    calls = []

    class FakeAds:
        def search(self, cid, q):
            calls.append((cid, q))
            return [r("Lead", "2026-10-16", 61000, 13, 219, 0)]

    ms = [mon.Monitor("a", mon.parse_conditions(["konversi = 0"]), "111", berturut_hari=3),
          mon.Monitor("b", mon.parse_conditions(["biaya > 1"]), "111")]
    out = mon.run(FakeAds(), ms, date(2026, 10, 17))
    assert len(calls) == 1 and "BETWEEN '2026-10-13' AND '2026-10-16'" in calls[0][1]
    # hari tanpa data dihitung 0 konversi → 'a' (konversi = 0) terpicu 4 hari; pasangkan dengan "biaya > x"
    # bila hanya ingin hari yang benar-benar berbiaya
    assert [o["terpicu"] for o in out] == [True, True] and out[0]["pemicu"][0]["hari_berturut"] == 4
