from gads_mcp import geo


def row(pid, impr, clicks, cost, conv):
    return {"segments.geo_target_province": f"geoTargetConstants/{pid}", "metrics.impressions": impr,
            "metrics.clicks": clicks, "metrics.cost": cost, "metrics.conversions": conv}


def test_aggregate_merges_physical_and_interest_rows_and_flags():
    rows = [row(20438, 228, 7, 18586.07, 1), row(20438, 687, 46, 280294.39, 10),     # Jawa Barat
            row(9056637, 20, 2, 2986, 0), row(9056637, 423, 32, 151064.14, 8),      # Banten
            row(20440, 1128, 43, 297940.77, 5), row(20440, 14, 1, 8716, 0),         # Jakarta
            row(20439, 14, 9, 51892, 0), row(20439, 389, 10, 20910.01, 0),          # Jawa Timur
            row(9056649, 3, 3, 40263.5, 0)]                                          # Papua Barat
    names = {"20438": "West Java", "9056637": "Banten", "20440": "Jakarta", "20439": "East Java",
             "9056649": "West Papua"}
    out = geo.summarize(geo.aggregate(rows, "provinsi"), names)
    by = {d["daerah"]: d for d in out["daerah"]}
    assert by["West Java"]["klik"] == 53 and by["West Java"]["konversi"] == 11
    assert out["daerah"][0]["daerah"] in ("Jakarta", "West Java")                 # urut biaya
    t = " ".join(out["temuan"])
    assert "East Java" in t and "tanpa konversi" in t
    assert "West Papua: 3 klik dari 3 tayangan" in t
    assert "Banten: biaya/konversi" in t and "kandidat dinaikkan" in t
    assert "Jakarta: biaya/konversi" in t and "di atas rata-rata" in t
