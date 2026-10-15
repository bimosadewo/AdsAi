"""Riset kata kunci lewat Keyword Planner (KeywordPlanIdeaService) — untuk pasar Indonesia.

Dibuat setelah uji 15 Okt 2026: `research_keywords` Adspirer untuk "pipa hdpe", "pipa drainase", dst. di
Indonesia mengembalikan 0 hasil (dan hanya "geopipe" pada percobaan lain) — pemetaan lokasi/bahasa mereka
tidak andal untuk Indonesia, dan ambang volume minimum membuang kata kunci niche B2B.

Prinsip AdsAi:
- Lokasi & bahasa eksplisit: default Indonesia (geo 2360) + bahasa Indonesia (1025).
- Kata kunci awal (seed) SELALU ikut ditampilkan dengan metriknya, sekecil apa pun volumenya.
- Tidak ada ambang volume yang mengosongkan hasil — volume < 100 diberi label, bukan dibuang.
- Niat dibaca dari persentil bid (≥P75 TINGGI, P25–P75 SEDANG, <P25 RENDAH) + kata informasional.
- Budget dalam Rupiah untuk 10 / 20 / 40 klik per hari — angka yang realistis untuk UKM.
"""

from __future__ import annotations

import re
from statistics import median
from typing import Any, Iterable

LANGUAGES = {"id": "1025", "indonesia": "1025", "indonesian": "1025", "en": "1000", "english": "1000"}
DEFAULT_GEO = ("2360",)  # Indonesia
INFO_WORDS = ("adalah", "apa itu", "pengertian", "fungsi", "cara", "jenis jenis", "jenis-jenis", "contoh",
              "gambar", "pdf", "jurnal", "skripsi", "definisi", "kegunaan", "perbedaan")
BUY_WORDS = ("harga", "jual", "beli", "distributor", "supplier", "agen", "toko", "pabrik", "terdekat", "murah",
             "grosir", "per meter", "per roll", "price")
CLICK_TIERS = (10, 20, 40)


def language_id(value: str | None) -> str:
    v = (value or "id").strip().lower()
    if v.isdigit():
        return v
    if v not in LANGUAGES:
        raise ValueError(f"Bahasa tidak dikenal: {value!r}. Pakai 'id', 'en', atau ID bahasa Google (mis. 1025).")
    return LANGUAGES[v]


def _rp(micros: Any) -> float | None:
    try:
        v = int(micros or 0)
    except (TypeError, ValueError):
        return None
    return round(v / 1_000_000, 0) if v else None


def _enum_name(v: Any) -> str:
    return getattr(v, "name", None) or str(v or "UNSPECIFIED")


def normalize(results: Iterable[Any]) -> list[dict[str, Any]]:
    """Hasil API (GenerateKeywordIdeaResult / historical metrics) → dict sederhana, tanpa duplikat."""
    seen: dict[str, dict[str, Any]] = {}
    for r in results:
        text = " ".join(str(getattr(r, "text", "") or "").lower().split())
        if not text or text in seen:
            continue
        m = getattr(r, "keyword_idea_metrics", None) or getattr(r, "keyword_metrics", None)
        low, high = _rp(getattr(m, "low_top_of_page_bid_micros", 0)), _rp(getattr(m, "high_top_of_page_bid_micros", 0))
        seen[text] = {
            "kata_kunci": text,
            "pencarian_bulanan": int(getattr(m, "avg_monthly_searches", 0) or 0),
            "persaingan": _enum_name(getattr(m, "competition", None)).replace("UNSPECIFIED", "-"),
            "bid_rendah": low,
            "bid_tinggi": high,
            "cpc_rata2": _rp(getattr(m, "average_cpc_micros", 0)),
        }
    return list(seen.values())


def _cpc(row: dict[str, Any]) -> float | None:
    if row.get("cpc_rata2"):
        return row["cpc_rata2"]
    lo, hi = row.get("bid_rendah"), row.get("bid_tinggi")
    if lo and hi:
        return (lo + hi) / 2
    return hi or lo


def _percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def analyze(rows: list[dict[str, Any]], seeds: Iterable[str], include_terms: Iterable[str] = (),
            exclude_terms: Iterable[str] = (), limit: int = 50) -> dict[str, Any]:
    seeds = [" ".join(s.lower().split()) for s in seeds if s and s.strip()]
    include = [t.lower() for t in include_terms if t]
    exclude = [t.lower() for t in exclude_terms if t]

    def has(text: str, terms: Iterable[str]) -> bool:
        return any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text) for t in terms)

    kept, dropped = [], []
    for r in rows:
        t = r["kata_kunci"]
        if exclude and has(t, exclude):
            dropped.append({**r, "alasan": "memuat kata yang dikecualikan"})
            continue
        if include and t not in seeds and not has(t, include):
            dropped.append({**r, "alasan": "tidak memuat kata produk"})
            continue
        kept.append(r)

    cpcs = [c for c in (_cpc(r) for r in kept) if c]
    p25, p75 = _percentile(cpcs, 0.25), _percentile(cpcs, 0.75)
    for r in kept:
        t, c = r["kata_kunci"], _cpc(r)
        if has(t, INFO_WORDS):
            r["niat"] = "INFORMASI"
        elif has(t, BUY_WORDS):
            r["niat"] = "BELI"
        elif c is None:
            r["niat"] = "-"
        else:
            r["niat"] = "TINGGI" if c >= p75 else "SEDANG" if c >= p25 else "RENDAH"
        r["seed"] = t in seeds
        if r["pencarian_bulanan"] < 100:
            r["catatan"] = "volume rendah (<100/bln)" if r["pencarian_bulanan"] else "volume tidak terukur"

    kept.sort(key=lambda r: (not r["seed"], r["niat"] == "INFORMASI", -r["pencarian_bulanan"]))
    ads_ready = [r for r in kept if r["niat"] in ("BELI", "TINGGI", "SEDANG") or r["seed"]]
    med = median(cpcs) if cpcs else None
    budget = None
    if med:
        budget = {f"{n}_klik_per_hari": round(n * med, -3) for n in CLICK_TIERS}
        budget["cpc_median"] = round(med, 0)
    missing = [s for s in seeds if s not in {r["kata_kunci"] for r in kept}]
    out = {
        "jumlah_dari_google": len(rows),
        "jumlah_relevan": len(kept),
        "total_pencarian_relevan": sum(r["pencarian_bulanan"] for r in kept),
        "budget_harian_rp": budget,
        "kandidat_iklan": [r["kata_kunci"] for r in ads_ready][:20],
        "kandidat_negatif": [r["kata_kunci"] for r in kept if r["niat"] == "INFORMASI"][:20],
        "kata_kunci": kept[:limit],
    }
    if missing:
        out["seed_tanpa_data"] = missing
    if dropped:
        out["dibuang"] = dropped[:20]
    if not rows:
        out["catatan"] = ("Google tidak mengembalikan ide sama sekali. Coba kata kunci awal yang lebih umum, atau "
                          "cek lokasi/bahasa. Jangan simpulkan 'tidak ada pencarian' dari hasil kosong.")
    return out


class KeywordResearch:
    def __init__(self, ads: Any):
        self.ads = ads

    def run(self, cid: str, seeds: list[str], url: str | None = None, geo_ids: Iterable[str] | None = None,
            language: str | None = "id", include_terms: Iterable[str] = (), exclude_terms: Iterable[str] = (),
            limit: int = 50) -> dict[str, Any]:
        seeds = [s for s in (seeds or []) if s and s.strip()]
        if not seeds and not url:
            raise ValueError("Isi minimal satu seed_keywords atau url.")
        if len(seeds) > 20:
            raise ValueError("Maksimal 20 seed_keywords per riset.")
        gc = self.ads.client
        svc = gc.get_service("KeywordPlanIdeaService")
        lang = f"languageConstants/{language_id(language)}"
        geos = [f"geoTargetConstants/{''.join(ch for ch in str(g) if ch.isdigit())}" for g in (geo_ids or DEFAULT_GEO)]
        network = gc.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH

        from .client import with_retry  # impor lokal: hindari siklus saat modul dimuat

        req = gc.get_type("GenerateKeywordIdeasRequest")
        req.customer_id = cid
        req.language = lang
        req.geo_target_constants.extend(geos)
        req.keyword_plan_network = network
        req.page_size = 1000
        if seeds and url:
            req.keyword_and_url_seed.url = url
            req.keyword_and_url_seed.keywords.extend(seeds)
        elif seeds:
            req.keyword_seed.keywords.extend(seeds)
        else:
            req.url_seed.url = url
        ideas = list(with_retry(lambda: svc.generate_keyword_ideas(request=req)))

        rows = normalize(ideas)
        if seeds:  # metrik pasti untuk setiap seed, walau Google tidak memasukkannya ke ide
            hreq = gc.get_type("GenerateKeywordHistoricalMetricsRequest")
            hreq.customer_id = cid
            hreq.keywords.extend(seeds)
            hreq.language = lang
            hreq.geo_target_constants.extend(geos)
            hreq.keyword_plan_network = network
            hist = with_retry(lambda: svc.generate_keyword_historical_metrics(request=hreq))
            have = {r["kata_kunci"] for r in rows}
            rows += [r for r in normalize(getattr(hist, "results", []) or []) if r["kata_kunci"] not in have]
        result = analyze(rows, seeds, include_terms, exclude_terms, limit)
        return {"customer_id": cid, "lokasi": geos, "bahasa": lang, **result}
