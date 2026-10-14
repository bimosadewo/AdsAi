"""Analisa murni (tanpa API): n-gram, usulan negatif, anomali, pacing, dan aturan alert."""

from __future__ import annotations

import calendar
import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from statistics import mean, pstdev
from typing import Any, Iterable

from .fmt import number, rupiah

# Kata sambung yang tidak berguna sebagai kandidat negatif tunggal.
STOPWORDS = {
    "dan", "di", "ke", "dari", "yang", "untuk", "dengan", "atau", "per", "the", "of", "for", "and", "a", "in",
    "1", "2", "3", "4", "5", "m", "m2", "meter", "roll", "harga", "jual", "beli", "toko", "terdekat",
}
# Penanda niat beli: jangan pernah diusulkan jadi negatif.
BUY_INTENT = {"harga", "jual", "beli", "toko", "distributor", "supplier", "pabrik", "agen", "grosir", "murah",
              "terdekat", "order", "pesan", "price"}

_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def contains_phrase(text: str, phrase: str) -> bool:
    """True bila phrase muncul sebagai rangkaian kata utuh di text."""
    return f" {phrase} " in f" {text} "


def _f(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# N-gram
# ---------------------------------------------------------------------------

def ngram_table(rows: Iterable[dict[str, Any]], n: int = 1, term_key: str = "search_term_view.search_term",
                min_cost: float = 0) -> list[dict[str, Any]]:
    """Agregasi biaya/klik/konversi per n-gram. Satu istilah dihitung sekali per n-gram unik."""
    agg: dict[str, dict[str, float]] = {}
    for r in rows:
        toks = tokens(str(r.get(term_key, "")))
        grams = {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}
        for g in grams:
            a = agg.setdefault(g, {"istilah": 0, "biaya": 0.0, "klik": 0.0, "konversi": 0.0})
            a["istilah"] += 1
            a["biaya"] += _f(r, "metrics.cost")
            a["klik"] += _f(r, "metrics.clicks")
            a["konversi"] += _f(r, "metrics.conversions")
    out = []
    for g, a in agg.items():
        if a["biaya"] < min_cost:
            continue
        out.append({
            "ngram": g,
            "istilah": int(a["istilah"]),
            "biaya": round(a["biaya"], 2),
            "klik": int(a["klik"]),
            "konversi": round(a["konversi"], 2),
            "biaya_per_konversi": round(a["biaya"] / a["konversi"], 2) if a["konversi"] else None,
        })
    return sorted(out, key=lambda x: x["biaya"], reverse=True)


def suggest_negatives(
    rows: list[dict[str, Any]],
    min_cost: float = 20_000,
    protected_terms: Iterable[str] = (),
    existing_negatives: Iterable[str] = (),
    term_key: str = "search_term_view.search_term",
    max_suggestions: int = 40,
) -> list[dict[str, Any]]:
    """Usulan kata kunci negatif dari n-gram & istilah yang memakan biaya tanpa konversi.

    - PHRASE (n-gram yang muncul di beberapa istilah): tidak pernah mengandung kata dilindungi
      (produk sendiri) atau kata niat beli.
    - EXACT (satu istilah spesifik): boleh mengandung kata produk karena hanya memblokir
      pencarian persis itu; kata niat beli tetap tidak diblokir.
    """
    protected = [p.lower() for p in protected_terms]
    existing = {e.lower() for e in existing_negatives}

    def blocked(text: str, exact: bool = False) -> bool:
        toks = set(tokens(text))
        if toks & BUY_INTENT:
            return True
        # Negatif EXACT hanya memblokir pencarian persis itu, jadi boleh mengandung kata produk.
        if not exact and any(contains_phrase(text, p) for p in protected):
            return True
        return any(contains_phrase(text, e) for e in existing)

    candidates: dict[str, dict[str, Any]] = {}
    for n in (1, 2):
        for g in ngram_table(rows, n, term_key, min_cost):
            text = g["ngram"]
            # PHRASE hanya untuk pola yang berulang; istilah tunggal ditangani sebagai EXACT di bawah.
            if g["konversi"] > 0 or g["istilah"] < 2 or blocked(text):
                continue
            if n == 1 and (text in STOPWORDS or len(text) < 3 or text.isdigit()):
                continue
            candidates[text] = {"text": text, "match_type": "PHRASE", "alasan": "n-gram tanpa konversi", **g}
    for r in rows:
        text = " ".join(tokens(str(r.get(term_key, ""))))
        cost = _f(r, "metrics.cost")
        if not text or cost < min_cost or _f(r, "metrics.conversions") > 0 or blocked(text, exact=True):
            continue
        if any(contains_phrase(text, c) for c in candidates):  # sudah tertutup n-gram
            continue
        candidates[text] = {"text": text, "match_type": "EXACT", "alasan": "istilah tanpa konversi",
                            "ngram": text, "istilah": 1, "biaya": round(cost, 2),
                            "klik": int(_f(r, "metrics.clicks")), "konversi": 0.0, "biaya_per_konversi": None}
    # Buang n-gram yang sudah tercakup n-gram lain yang lebih pendek.
    final = []
    for text, c in sorted(candidates.items(), key=lambda kv: -kv[1]["biaya"]):
        if any(o != text and contains_phrase(text, o) and candidates[o]["match_type"] == "PHRASE" for o in candidates):
            continue
        final.append(c)
    return final[:max_suggestions]


def estimated_savings(rows: list[dict[str, Any]], suggestions: list[dict[str, Any]],
                      term_key: str = "search_term_view.search_term") -> float:
    """Total biaya istilah unik yang akan terblokir oleh usulan (tanpa hitung ganda)."""
    phrases = [s["text"] for s in suggestions if s["match_type"] == "PHRASE"]
    exact = {s["text"] for s in suggestions if s["match_type"] == "EXACT"}
    total = 0.0
    for r in rows:
        text = " ".join(tokens(str(r.get(term_key, ""))))
        if text in exact or any(contains_phrase(text, p) for p in phrases):
            total += _f(r, "metrics.cost")
    return round(total, 2)


# ---------------------------------------------------------------------------
# Anomali & pacing
# ---------------------------------------------------------------------------

def detect_anomalies(daily: list[dict[str, Any]], z: float = 2.5, min_history: int = 7) -> list[dict[str, Any]]:
    """Bandingkan hari terakhir dengan rata-rata hari-hari sebelumnya untuk biaya, klik, konversi, CPC."""
    if len(daily) < min_history + 1:
        return []
    series = sorted(daily, key=lambda r: r["date"])
    hist, last = series[:-1], series[-1]
    out = []

    def check(name: str, values: list[float], value: float):
        mu = mean(values)
        sd = pstdev(values)
        if sd == 0:
            if mu > 0 and value == 0:
                out.append({"metrik": name, "tanggal": last["date"], "nilai": value, "rata2": round(mu, 2),
                            "arah": "turun", "skor_z": None})
            return
        score = (value - mu) / sd
        if abs(score) >= z:
            out.append({"metrik": name, "tanggal": last["date"], "nilai": round(value, 2), "rata2": round(mu, 2),
                        "arah": "naik" if score > 0 else "turun", "skor_z": round(score, 2)})

    for key, name in (("cost", "biaya"), ("clicks", "klik"), ("conversions", "konversi")):
        check(name, [float(r.get(key) or 0) for r in hist], float(last.get(key) or 0))
    cpcs = [float(r["cost"]) / float(r["clicks"]) for r in hist if r.get("clicks")]
    if cpcs and last.get("clicks"):
        check("cpc", cpcs, float(last["cost"]) / float(last["clicks"]))
    return out


def zero_conversion_streak(daily: list[dict[str, Any]]) -> int:
    """Jumlah hari terakhir berturut-turut dengan biaya > 0 tapi konversi 0."""
    streak = 0
    for r in sorted(daily, key=lambda r: r["date"], reverse=True):
        if float(r.get("cost") or 0) > 0 and float(r.get("conversions") or 0) == 0:
            streak += 1
        else:
            break
    return streak


def budget_pacing(mtd_cost: float, monthly_budget: float, today: date) -> dict[str, Any]:
    """Proyeksi biaya akhir bulan dari biaya bulan berjalan (sampai kemarin)."""
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed = max(today.day - 1, 1)
    projected = mtd_cost / elapsed * days_in_month
    ratio = projected / monthly_budget if monthly_budget else math.inf
    if ratio > 1.1:
        status = "BOROS"
    elif ratio < 0.8:
        status = "KURANG"
    else:
        status = "SESUAI"
    remaining_days = days_in_month - elapsed
    return {
        "biaya_bulan_ini": round(mtd_cost, 2),
        "budget_bulanan": monthly_budget,
        "proyeksi_akhir_bulan": round(projected, 2),
        "persen_proyeksi": round(ratio * 100, 1),
        "status": status,
        "saran_budget_harian": round(max(monthly_budget - mtd_cost, 0) / remaining_days, 0) if remaining_days else None,
        "catatan": "Google boleh membelanjakan hingga 2x budget harian pada satu hari dan menyeimbangkannya "
                   "dalam sebulan; lonjakan satu hari belum tentu masalah.",
    }


# ---------------------------------------------------------------------------
# Aturan alert
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Alert:
    level: str   # KRITIS | PERINGATAN | INFO
    kode: str
    pesan: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


LEVEL_ORDER = {"KRITIS": 0, "PERINGATAN": 1, "INFO": 2}


def _money_or_num(metric: str, value: float) -> str:
    return rupiah(value) if metric in ("biaya", "cpc") else number(value, 1 if metric == "konversi" else 0)


def evaluate_alerts(snapshot: dict[str, Any], client: Any) -> list[Alert]:
    """snapshot berasal dari collect.collect_snapshot(); client dari clients.yaml."""
    th = client.thresholds
    alerts: list[Alert] = []

    for c in snapshot.get("kampanye_aktif_tidak_tayang", []):
        alerts.append(Alert("KRITIS", "TIDAK_TAYANG",
                            f"Kampanye '{c['nama']}' aktif tapi 0 tayangan 3 hari terakhir "
                            f"({c.get('alasan') or 'cek saldo/pembayaran'})."))

    rejected = snapshot.get("iklan_ditolak", [])
    if rejected:
        sample = ", ".join(f"{r['kampanye']} / {r['grup']}" for r in rejected[:3])
        alerts.append(Alert("KRITIS" if len(rejected) >= 3 else "PERINGATAN", "IKLAN_DITOLAK",
                            f"{len(rejected)} iklan aktif DITOLAK Google ({sample}). Cek kebijakan di UI."))

    for r in snapshot.get("pengaturan_berisiko", []):
        alerts.append(Alert("INFO", "PENGATURAN_BERISIKO",
                            f"Kampanye '{r['kampanye']}' memakai {', '.join(r['masalah'])} — sering jadi sumber "
                            "trafik kurang relevan (cek campaign_settings)."))

    daily = snapshot.get("harian", [])
    streak = zero_conversion_streak(daily)
    if streak >= th.zero_conversion_days:
        alerts.append(Alert("KRITIS", "KONVERSI_NOL",
                            f"{streak} hari berturut-turut ada biaya tapi 0 konversi. Cek form/WA/tag konversi."))

    for a in detect_anomalies(daily, th.anomaly_z):
        level = "PERINGATAN"
        if a["metrik"] in ("biaya", "cpc") and a["arah"] == "naik":
            level = "KRITIS" if (a["skor_z"] or 0) >= th.anomaly_z * 1.5 else "PERINGATAN"
        alerts.append(Alert(level, f"ANOMALI_{a['metrik'].upper()}",
                            f"{a['metrik'].upper()} {a['tanggal']} {a['arah']} tajam: "
                            f"{_money_or_num(a['metrik'], a['nilai'])} (rata-rata {_money_or_num(a['metrik'], a['rata2'])})."))

    week, prev = snapshot.get("minggu_ini", {}), snapshot.get("minggu_lalu", {})
    cpa, cpa_prev = week.get("biaya_per_konversi"), prev.get("biaya_per_konversi")
    if cpa and cpa_prev and (cpa - cpa_prev) / cpa_prev * 100 > th.cpa_increase_pct:
        alerts.append(Alert("PERINGATAN", "CPA_NAIK",
                            f"Biaya/konversi naik dari {rupiah(cpa_prev)} ke {rupiah(cpa)} dibanding minggu lalu."))
    if client.target_cpa and cpa and cpa > client.target_cpa * 1.3:
        alerts.append(Alert("PERINGATAN", "DI_ATAS_TARGET",
                            f"Biaya/konversi {rupiah(cpa)} di atas target {rupiah(client.target_cpa)}."))

    pacing = snapshot.get("pacing")
    if pacing and pacing["status"] == "BOROS":
        alerts.append(Alert("PERINGATAN", "PACING_BOROS",
                            f"Proyeksi biaya bulan ini {rupiah(pacing['proyeksi_akhir_bulan'])} "
                            f"({number(pacing['persen_proyeksi'], 1)}% dari budget {rupiah(pacing['budget_bulanan'])})."))
    elif pacing and pacing["status"] == "KURANG":
        alerts.append(Alert("INFO", "PACING_KURANG",
                            f"Proyeksi biaya bulan ini hanya {number(pacing['persen_proyeksi'], 1)}% dari budget."))

    n_primary = snapshot.get("jumlah_konversi_utama", 0)
    if n_primary > 3:
        alerts.append(Alert("PERINGATAN", "KONVERSI_UTAMA_BERLEBIH",
                            f"{n_primary} konversi berstatus Utama; angka konversi bisa menggelembung."))

    for u in snapshot.get("impor_offline_bermasalah", []):
        alerts.append(Alert("PERINGATAN", "IMPOR_GAGAL",
                            f"Impor konversi offline '{u['nama']}' sukses hanya {u['rate']:.0%}."))

    return sorted(alerts, key=lambda a: LEVEL_ORDER.get(a.level, 9))
