from datetime import date

from gads_mcp import analysis
from gads_mcp.clients import Client, Thresholds


def term(text, cost, conv=0, clicks=1):
    return {"search_term_view.search_term": text, "metrics.cost": cost, "metrics.conversions": conv,
            "metrics.clicks": clicks}


TERMS = [
    term("harga geotextile per m2", 863_543, 55.8, 141),
    term("geotextile", 1_182_322, 55.5, 200),
    term("beda geotextile woven dan nonwoven", 19_898),
    term("beda geotextile woven dan non woven", 25_711),
    term("plastic product", 19_366, 0, 10),
    term("plastic manufacturer", 13_000, 0, 8),
    term("pt multi spunindo jaya", 35_179),
    term("multi spunindo jaya", 41_443, 1),
    term("geotextile untuk taman", 38_435),
    term("jual kain geotextile terdekat", 32_033),
]


def test_ngram_table_counts_terms_once():
    table = {g["ngram"]: g for g in analysis.ngram_table(TERMS, 1)}
    assert table["beda"]["istilah"] == 2
    assert table["beda"]["biaya"] == 19_898 + 25_711
    assert table["beda"]["konversi"] == 0
    bigrams = {g["ngram"] for g in analysis.ngram_table(TERMS, 2)}
    assert "plastic product" in bigrams


def test_suggest_negatives_respects_protection_and_intent():
    sugg = analysis.suggest_negatives(
        TERMS, min_cost=15_000, protected_terms=["geotextile", "geotekstil"], existing_negatives=["plastic manufacturer"])
    texts = {s["text"] for s in sugg}
    assert "beda" in texts                                  # n-gram tanpa konversi
    phrase = {s["text"] for s in sugg if s["match_type"] == "PHRASE"}
    assert not any("geotextile" in t for t in phrase)       # kata produk dilindungi di PHRASE
    exact = {s["text"] for s in sugg if s["match_type"] == "EXACT"}
    assert "geotextile untuk taman" in exact                # istilah spesifik boleh EXACT
    assert not any("jual" in t or "terdekat" in t for t in texts)  # niat beli tidak diblokir
    assert "plastic" in texts or "plastic product" in texts
    assert "plastic manufacturer" not in texts              # sudah terpasang
    assert "spunindo" not in texts                          # ada konversi di 'multi spunindo jaya'
    # Tidak ada usulan yang tumpang-tindih (frasa panjang yang sudah tercakup frasa pendek).
    for s in sugg:
        assert not any(o != s["text"] and analysis.contains_phrase(s["text"], o) for o in texts)


def test_contains_phrase_whole_words():
    assert analysis.contains_phrase("plastik cor hijau", "plastik cor")
    assert not analysis.contains_phrase("corak plastik", "cor")


def daily(costs, convs=None, clicks=None):
    convs = convs or [5] * len(costs)
    clicks = clicks or [20] * len(costs)
    return [{"date": f"2026-10-{i + 1:02d}", "cost": c, "conversions": v, "clicks": k}
            for i, (c, v, k) in enumerate(zip(costs, convs, clicks))]


def test_detect_anomalies_spike_and_drop():
    base = [100_000, 95_000, 105_000, 98_000, 102_000, 99_000, 101_000, 100_000]
    spike = analysis.detect_anomalies(daily(base + [400_000]))
    assert any(a["metrik"] == "biaya" and a["arah"] == "naik" for a in spike)
    drop = analysis.detect_anomalies(daily(base + [100_000], convs=[5] * 8 + [0]))
    assert any(a["metrik"] == "konversi" and a["arah"] == "turun" for a in drop)
    assert analysis.detect_anomalies(daily(base + [101_000])) == []
    assert analysis.detect_anomalies(daily(base[:3])) == []


def test_zero_conversion_streak():
    d = daily([10, 10, 10, 10, 0], convs=[1, 0, 0, 0, 0])
    assert analysis.zero_conversion_streak(d) == 0  # hari terakhir tanpa biaya memutus hitungan
    d = daily([10, 10, 10, 10], convs=[1, 0, 0, 0])
    assert analysis.zero_conversion_streak(d) == 3


def test_budget_pacing():
    p = analysis.budget_pacing(1_400_000, 3_000_000, date(2026, 10, 15))  # 14 hari berlalu, 31 hari
    assert p["proyeksi_akhir_bulan"] == 3_100_000
    assert p["status"] == "SESUAI"
    assert analysis.budget_pacing(2_000_000, 3_000_000, date(2026, 10, 15))["status"] == "BOROS"
    low = analysis.budget_pacing(500_000, 3_000_000, date(2026, 10, 15))
    assert low["status"] == "KURANG" and low["saran_budget_harian"] == round(2_500_000 / 17)


def test_evaluate_alerts_orders_by_severity():
    client = Client("tp", "PT Tiga Pilar", "5827572362", target_cpa=15_000, thresholds=Thresholds())
    snap = {
        "kampanye_aktif_tidak_tayang": [{"nama": "Lead-Search", "alasan": "BUDGET_CONSTRAINED"}],
        "harian": daily([50_000] * 10, convs=[3] * 6 + [0] * 4),
        "minggu_ini": {"biaya_per_konversi": 30_000},
        "minggu_lalu": {"biaya_per_konversi": 15_000},
        "pacing": {"status": "BOROS", "proyeksi_akhir_bulan": 4e6, "persen_proyeksi": 133.3, "budget_bulanan": 3e6},
        "jumlah_konversi_utama": 17,
        "impor_offline_bermasalah": [{"nama": "ContactWA", "rate": 0.2}],
    }
    alerts = analysis.evaluate_alerts(snap, client)
    codes = [a.kode for a in alerts]
    assert {"TIDAK_TAYANG", "KONVERSI_NOL", "CPA_NAIK", "DI_ATAS_TARGET", "PACING_BOROS",
            "KONVERSI_UTAMA_BERLEBIH", "IMPOR_GAGAL"} <= set(codes)
    levels = [a.level for a in alerts]
    assert levels == sorted(levels, key=lambda lv: analysis.LEVEL_ORDER[lv])
    assert analysis.evaluate_alerts({}, client) == []
