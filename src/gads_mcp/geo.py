"""Performa iklan per provinsi/kota — data milik akun sendiri, tersedia di level akses API mana pun.

Pengganti sementara volume Keyword Planner per daerah (butuh Basic Access): bukan berapa orang mencari, tetapi
di mana iklan kita benar-benar menghasilkan klik & prospek, dan di mana uang habis tanpa hasil.
user_location_view memecah tiap daerah menjadi beberapa baris (lokasi fisik vs minat) — dijumlahkan di sini.
"""

from __future__ import annotations

from datetime import date
from typing import Any

LEVELS = {"provinsi": "segments.geo_target_province", "kota": "segments.geo_target_city"}


def query(level: str, start: date, end: date) -> str:
    seg = LEVELS[level]
    return (f"SELECT {seg}, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions "
            f"FROM user_location_view WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' "
            "AND metrics.impressions > 0")


def names_query(ids: list[str]) -> str:
    return ("SELECT geo_target_constant.id, geo_target_constant.name FROM geo_target_constant "
            f"WHERE geo_target_constant.id IN ({', '.join(ids)})")


def _f(r: dict, k: str) -> float:
    try:
        return float(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


def aggregate(rows: list[dict[str, Any]], level: str) -> dict[str, dict[str, float]]:
    seg = LEVELS[level]
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        rid = str(r.get(seg) or "").rsplit("/", 1)[-1]
        if not rid:
            continue
        t = out.setdefault(rid, {"tayangan": 0.0, "klik": 0.0, "biaya": 0.0, "konversi": 0.0})
        for key, col in (("tayangan", "metrics.impressions"), ("klik", "metrics.clicks"), ("biaya", "metrics.cost"),
                         ("konversi", "metrics.conversions")):
            t[key] += _f(r, col)
    return out


def summarize(agg: dict[str, dict[str, float]], names: dict[str, str], min_cost_share: float = 0.03) -> dict:
    total_cost = sum(t["biaya"] for t in agg.values()) or 0.0
    total_conv = sum(t["konversi"] for t in agg.values())
    avg_cpa = total_cost / total_conv if total_conv else None
    rows = []
    for rid, t in agg.items():
        rows.append({"daerah": names.get(rid, rid), "id": rid, "tayangan": int(t["tayangan"]), "klik": int(t["klik"]),
                     "biaya": round(t["biaya"]), "konversi": round(t["konversi"], 1),
                     "biaya_per_konversi": round(t["biaya"] / t["konversi"]) if t["konversi"] else None,
                     "porsi_biaya_pct": round(t["biaya"] / total_cost * 100, 1) if total_cost else 0.0})
    rows.sort(key=lambda x: (-x["biaya"], -x["tayangan"]))
    temuan = []
    for x in rows:
        share = x["biaya"] / total_cost if total_cost else 0
        if x["konversi"] == 0 and share >= min_cost_share:
            temuan.append(f"{x['daerah']}: Rp{x['biaya']:,} ({x['klik']} klik) tanpa konversi.".replace(",", "."))
        elif avg_cpa and x["biaya_per_konversi"] and x["konversi"] >= 2:
            if x["biaya_per_konversi"] <= 0.7 * avg_cpa:
                temuan.append(f"{x['daerah']}: biaya/konversi Rp{x['biaya_per_konversi']:,} — jauh di bawah rata-rata "
                              f"Rp{round(avg_cpa):,}; kandidat dinaikkan.".replace(",", "."))
            elif x["biaya_per_konversi"] >= 1.5 * avg_cpa:
                temuan.append(f"{x['daerah']}: biaya/konversi Rp{x['biaya_per_konversi']:,} — jauh di atas rata-rata "
                              f"Rp{round(avg_cpa):,}.".replace(",", "."))
        if x["klik"] and x["tayangan"] and x["klik"] / x["tayangan"] >= 0.5 and x["tayangan"] >= 3 and not x["konversi"]:
            temuan.append(f"{x['daerah']}: {x['klik']} klik dari {x['tayangan']} tayangan tanpa konversi — CTR tidak wajar, "
                          "periksa kemungkinan klik tidak valid / daerah di luar jangkauan pengiriman.")
    return {"total_biaya": round(total_cost), "total_konversi": round(total_conv, 1),
            "biaya_per_konversi_rata2": round(avg_cpa) if avg_cpa else None, "daerah": rows, "temuan": temuan,
            "catatan": "Lokasi = lokasi pengguna (fisik atau minat). Ini performa iklan Anda, bukan volume pencarian."}
