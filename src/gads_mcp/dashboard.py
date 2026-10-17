"""Dashboard performa interaktif (MCP Apps) — kartu yang dirender langsung di Claude, seperti kartu Adspirer,
tetapi datanya dibaca langsung dari Google Ads (tanpa cache), berbahasa Indonesia, dalam Rupiah.

Bagian:
- `build_data()`  : murni — baris GAQL → JSON dashboard (KPI + perbandingan periode setara, harian, per
                    kampanye, insight). Dipakai juga oleh klien yang tidak mendukung MCP Apps (teks).
- `fetch()`       : ambil data dari Google Ads untuk N hari sampai kemarin (hari ini selalu belum lengkap).
- `html()`        : dokumen `ui://adsai/dashboard.html` — memuat SDK resmi @modelcontextprotocol/ext-apps
                    (dibundel lokal, tanpa CDN, lisensi di ui/ext-apps-LICENSE.txt) dan memanggil ulang tool
                    ini lewat host saat tab rentang tanggal diklik.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .collect import today_local
from .fmt import number, rupiah

RESOURCE_URI = "ui://adsai/dashboard.html"
RANGES = (7, 14, 30, 60, 90)
_UI_DIR = Path(__file__).parent / "ui"
_BUNDLE = "ext-apps-2.0.3.js"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def period(days: int, today: date | None = None, start: str | None = None,
           end: str | None = None) -> tuple[date, date, date, date]:
    """(awal, akhir, awal_lalu, akhir_lalu). Default: N hari sampai kemarin + N hari sebelumnya."""
    if start or end:
        if not (start and end):
            raise ValueError("start dan end harus diisi bersamaan (YYYY-MM-DD).")
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        if s > e:
            raise ValueError("start tidak boleh setelah end.")
    else:
        if not 1 <= int(days) <= 365:
            raise ValueError("days harus 1–365.")
        e = (today or today_local()) - timedelta(days=1)
        s = e - timedelta(days=int(days) - 1)
    n = (e - s).days + 1
    return s, e, s - timedelta(days=n), s - timedelta(days=1)


def _f(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _totals(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    cost = clicks = impr = conv = 0.0
    for r in rows:
        cost += _f(r, "metrics.cost")
        clicks += _f(r, "metrics.clicks")
        impr += _f(r, "metrics.impressions")
        conv += _f(r, "metrics.conversions")
    return {
        "biaya": round(cost),
        "klik": int(clicks),
        "tayangan": int(impr),
        "konversi": round(conv, 2),
        "ctr": round(clicks / impr * 100, 2) if impr else None,
        "cpc": round(cost / clicks) if clicks else None,
        "biaya_per_konversi": round(cost / conv) if conv else None,
        "tingkat_konversi": round(conv / clicks * 100, 2) if clicks else None,
    }


def _delta(cur: Any, prev: Any) -> float | None:
    if cur is None or not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def build_data(rows: list[dict[str, Any]], prev_rows: list[dict[str, Any]], camp_rows: list[dict[str, Any]],
               s: date, e: date, ps: date, pe: date, customer_id: str, client_name: str | None = None,
               alerts: list[dict[str, Any]] | None = None, target_cpa: float | None = None,
               kw_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cur, prev = _totals(rows), _totals(prev_rows)
    kpi = {k: {"nilai": cur[k], "sebelumnya": prev[k], "perubahan_pct": _delta(cur[k], prev[k])}
           for k in ("biaya", "klik", "tayangan", "konversi", "biaya_per_konversi", "ctr", "cpc", "tingkat_konversi")}

    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(str(r.get("segments.date")), []).append(r)
    daily = []
    d = s
    while d <= e:
        t = _totals(by_day.get(d.isoformat(), []))
        daily.append({"tanggal": d.isoformat(), "biaya": t["biaya"], "klik": t["klik"], "konversi": t["konversi"]})
        d += timedelta(days=1)

    camps: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for r in rows:
        cid = str(r.get("campaign.id"))
        camps.setdefault(cid, []).append(r)
        names[cid] = r.get("campaign.name") or cid
    share = {str(r.get("campaign.id")): r for r in camp_rows}
    campaigns = []
    for cid, rs in camps.items():
        t = _totals(rs)
        if not (t["biaya"] or t["tayangan"]):
            continue
        sh = share.get(cid, {})
        lost = sh.get("metrics.search_budget_lost_impression_share")
        campaigns.append({"id": cid, "nama": names[cid], "status": sh.get("campaign.status"), **t,
                          "pangsa_tayang": sh.get("metrics.search_impression_share"),
                          "hilang_karena_budget": lost,
                          "ai_max": bool(sh.get("campaign.ai_max_setting.enable_ai_max"))})
    campaigns.sort(key=lambda c: -c["biaya"])

    insights = list(_insights(cur, prev, campaigns, target_cpa))
    insights += keyword_insights(kw_rows or [], campaigns)
    for a in alerts or []:
        insights.append({"level": a.get("level", "INFO"), "pesan": a.get("pesan", "")})
    insights.sort(key=lambda i: LEVELS.get(i["level"], 9))

    n = (e - s).days + 1
    return {
        "customer_id": customer_id,
        "klien": client_name,
        "mata_uang": "IDR",
        "periode": {"mulai": s.isoformat(), "selesai": e.isoformat(), "hari": n,
                    "pembanding": {"mulai": ps.isoformat(), "selesai": pe.isoformat()}},
        "kpi": kpi,
        "harian": daily,
        "kampanye": campaigns,
        "insight": insights,
        "sorotan": highlight(insights),
        "sumber": "Google Ads API langsung (bukan cache); hari ini tidak disertakan karena belum lengkap.",
    }


LEVELS = {"KRITIS": 0, "PERINGATAN": 1, "INFO": 2}
QS_PARTS = {"ad_group_criterion.quality_info.search_predicted_ctr": "perkiraan CTR",
            "ad_group_criterion.quality_info.creative_quality_score": "relevansi iklan",
            "ad_group_criterion.quality_info.post_click_quality_score": "landing page"}


def highlight(insights: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Satu temuan untuk disorot di atas kartu: yang paling mendesak, utamakan yang punya aksi."""
    urgent = [i for i in insights if i["level"] != "INFO"]
    pool = urgent or [i for i in insights if i.get("aksi")]
    return min(pool, key=lambda i: (LEVELS.get(i["level"], 9), not i.get("aksi"))) if pool else None


def _n(v: float) -> str:
    return number(v, 0 if float(v).is_integer() else 1)


def _weak(r: dict[str, Any]) -> list[str]:
    return [label for key, label in QS_PARTS.items() if str(r.get(key) or "").endswith("BELOW_AVERAGE")]


def keyword_insights(kw_rows: list[dict[str, Any]], campaigns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Murni: Quality Score per kata kunci + biaya yang tidak lewat kata kunci (AI Max / pencocokan luas).

    Beda dengan sekadar "QS rendah": AdsAi menyebut KOMPONEN yang lemah, karena perbaikannya berbeda —
    landing page (halaman), relevansi iklan (teks iklan), perkiraan CTR (judul/penawaran)."""
    out: list[dict[str, Any]] = []
    kws = []
    for r in kw_rows:
        qs = r.get("ad_group_criterion.quality_info.quality_score")
        kws.append({"teks": r.get("ad_group_criterion.keyword.text"),
                    "match": str(r.get("ad_group_criterion.keyword.match_type") or ""),
                    "kampanye_id": str(r.get("campaign.id")), "kampanye": r.get("campaign.name"),
                    "qs": int(qs) if qs else None, "lemah": _weak(r),
                    "biaya": _f(r, "metrics.cost"), "klik": _f(r, "metrics.clicks"),
                    "konversi": _f(r, "metrics.conversions")})

    low = sorted((k for k in kws if k["qs"] and k["qs"] <= 4 and k["biaya"] > 0), key=lambda k: -k["biaya"])
    if low:
        k = low[0]
        why = f" Lemah di: {', '.join(k['lemah'])}." if k["lemah"] else ""
        more = f" (+{len(low) - 1} kata kunci QS rendah lain)" if len(low) > 1 else ""
        out.append({"level": "PERINGATAN", "judul": "Kata kunci berkualitas rendah memakan budget",
                    "pesan": f"\"{k['teks']}\" ({k['match']}): Quality Score {k['qs']}/10, biaya {rupiah(k['biaya'])}, "
                             f"{_n(k['konversi'])} konversi.{why}{more}",
                    "lencana": f"QS {k['qs']}",
                    "aksi": {"label": "Analisis & perbaiki",
                             "prompt": f"Analisis kata kunci \"{k['teks']}\" ({k['match']}) di kampanye {k['kampanye']}: "
                                       f"Quality Score {k['qs']}{', lemah di ' + ', '.join(k['lemah']) if k['lemah'] else ''}. "
                                       "Jelaskan penyebabnya dan usulkan perbaikan (teks iklan, landing page) atau jeda. "
                                       "Jangan ubah apa pun tanpa pratinjau dan persetujuan saya."}})

    spent = [k for k in kws if k["biaya"] > 0]
    lp_bad = [k for k in spent if "landing page" in k["lemah"]]
    kw_cost = sum(k["biaya"] for k in spent)
    if len(lp_bad) >= 2 and sum(k["biaya"] for k in lp_bad) >= 0.5 * kw_cost:
        share = sum(k["biaya"] for k in lp_bad) / kw_cost * 100
        out.append({"level": "PERINGATAN", "judul": "Landing page menurunkan Quality Score",
                    "pesan": f"Google menilai landing page di bawah rata-rata untuk {len(lp_bad)} dari {len(spent)} kata "
                             f"kunci yang berbiaya ({number(share, 0)}% biaya kata kunci). Satu perbaikan halaman "
                             "menaikkan QS semuanya sekaligus.",
                    "aksi": {"label": "Checklist landing page",
                             "prompt": "Buat checklist perbaikan landing page untuk kata kunci yang landing page-nya dinilai "
                                       "di bawah rata-rata (cek URL tujuan iklannya): kecepatan di HP, kecocokan judul dengan "
                                       "kata kunci, harga/penawaran, dan tombol WA di atas."}})

    by_camp: dict[str, dict[str, float]] = {}
    for k in kws:
        t = by_camp.setdefault(k["kampanye_id"], {"biaya": 0.0, "klik": 0.0, "konversi": 0.0})
        for f in ("biaya", "klik", "konversi"):
            t[f] += k[f]
    total = sum(c["biaya"] for c in campaigns) or 1
    for c in campaigns:
        if c["id"] not in by_camp:  # kampanye tanpa data kata kunci (mis. PMax) — bukan temuan
            continue
        rest = c["biaya"] - by_camp[c["id"]]["biaya"]
        if c["biaya"] and rest / c["biaya"] >= 0.3 and rest >= 0.1 * total:
            conv = max(c["konversi"] - by_camp[c["id"]]["konversi"], 0)
            src = "AI Max aktif" if c.get("ai_max") else "pencocokan luas/otomatis"
            out.append({"level": "PERINGATAN" if not conv else "INFO",
                        "judul": "Sebagian besar biaya di luar daftar kata kunci",
                        "pesan": f"{c['nama']}: {number(rest / c['biaya'] * 100, 0)}% biaya ({rupiah(rest)}, "
                                 f"{_n(c['klik'] - by_camp[c['id']]['klik'])} klik, {_n(conv)} konversi) dari "
                                 f"pencarian yang tidak cocok dengan kata kunci Anda ({src}).",
                        "aksi": {"label": "Cek search terms",
                                 "prompt": f"Tampilkan search terms kampanye {c['nama']} periode ini yang bukan dari daftar "
                                           "kata kunci, tandai yang tidak relevan, dan siapkan pratinjau negative keyword."}})
    return out


def _insights(cur: dict, prev: dict, campaigns: list[dict], target_cpa: float | None) -> Iterable[dict[str, str]]:
    if cur["biaya"] and not cur["konversi"]:
        yield {"level": "KRITIS", "pesan": f"Biaya {rupiah(cur['biaya'])} tanpa satu pun konversi — cek tracking "
                                           "(campaign_conversion_goals) dan search terms."}
    if target_cpa and cur["biaya_per_konversi"] and cur["biaya_per_konversi"] > target_cpa:
        yield {"level": "PERINGATAN", "pesan": f"Biaya/konversi {rupiah(cur['biaya_per_konversi'])} di atas target "
                                               f"{rupiah(target_cpa)}."}
    if prev["konversi"] and cur["konversi"] is not None:
        ch = _delta(cur["konversi"], prev["konversi"])
        if ch is not None and abs(ch) >= 20:
            yield {"level": "INFO" if ch > 0 else "PERINGATAN",
                   "pesan": f"Konversi {'naik' if ch > 0 else 'turun'} {number(abs(ch), 0)}% dibanding periode sebelumnya."}
    for c in campaigns:
        lost = c.get("hilang_karena_budget")
        try:
            lost_f = float(lost) if lost is not None else None
        except (TypeError, ValueError):
            lost_f = None
        if lost_f is not None and lost_f >= 0.5:
            yield {"level": "INFO", "pesan": f"{c['nama']}: {number(lost_f * 100, 0)}% tayangan hilang karena budget "
                                             "— ruang tumbuh bila biaya/konversi sehat."}
        if c["biaya"] and not c["konversi"] and c["biaya"] >= 0.3 * max(cur["biaya"], 1):
            yield {"level": "PERINGATAN", "pesan": f"{c['nama']} memakai {rupiah(c['biaya'])} tanpa konversi."}
    yield {"level": "INFO", "pesan": "Akun prospek: ukur dengan biaya per prospek, bukan ROAS (nilai konversi 0/1 "
                                     "hanya penanda)."}


def q_daily(s: date, e: date) -> str:
    return ("SELECT campaign.id, campaign.name, segments.date, metrics.cost_micros, metrics.clicks, "
            "metrics.impressions, metrics.conversions FROM campaign "
            f"WHERE segments.date BETWEEN '{s.isoformat()}' AND '{e.isoformat()}' AND metrics.impressions > 0")


def q_keywords(s: date, e: date) -> str:
    return ("SELECT campaign.id, campaign.name, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
            "ad_group_criterion.quality_info.quality_score, ad_group_criterion.quality_info.search_predicted_ctr, "
            "ad_group_criterion.quality_info.creative_quality_score, "
            "ad_group_criterion.quality_info.post_click_quality_score, metrics.cost_micros, metrics.clicks, "
            "metrics.conversions FROM keyword_view "
            f"WHERE segments.date BETWEEN '{s.isoformat()}' AND '{e.isoformat()}' AND metrics.impressions > 0")


def q_share(s: date, e: date) -> str:
    return ("SELECT campaign.id, campaign.status, campaign.ai_max_setting.enable_ai_max, metrics.search_impression_share, "
            "metrics.search_budget_lost_impression_share FROM campaign "
            f"WHERE segments.date BETWEEN '{s.isoformat()}' AND '{e.isoformat()}' AND metrics.impressions > 0")


def fetch(ads: Any, customer_id: str, days: int = 7, start: str | None = None, end: str | None = None,
          client: Any = None, alerts: list[dict[str, Any]] | None = None, today: date | None = None) -> dict:
    s, e, ps, pe = period(days, today, start, end)
    rows = ads.search(customer_id, q_daily(s, e))
    prev = ads.search(customer_id, q_daily(ps, pe))
    share = ads.search(customer_id, q_share(s, e))
    try:
        kws = ads.search(customer_id, q_keywords(s, e))
    except Exception:  # noqa: BLE001 — temuan kata kunci pelengkap; dashboard tetap tampil tanpanya
        kws = []
    return build_data(rows, prev, share, s, e, ps, pe, customer_id,
                      getattr(client, "name", None), alerts, getattr(client, "target_cpa", None), kws)


def text_summary(d: dict[str, Any]) -> str:
    k = d["kpi"]
    p = d["periode"]

    def ch(key):
        v = k[key]["perubahan_pct"]
        return "" if v is None else f" ({'+' if v >= 0 else ''}{number(v, 1)}%)"

    lines = [f"Performa {d.get('klien') or d['customer_id']} {p['mulai']} s.d. {p['selesai']} ({p['hari']} hari):",
             f"biaya {rupiah(k['biaya']['nilai'])}{ch('biaya')}, klik {number(k['klik']['nilai'])}{ch('klik')}, "
             f"konversi {number(k['konversi']['nilai'], 1)}{ch('konversi')}, biaya/konversi "
             f"{rupiah(k['biaya_per_konversi']['nilai'])}{ch('biaya_per_konversi')}."]
    lines += [f"[{i['level']}] {i['pesan']}" for i in d["insight"][:6]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _bundle() -> str:
    js = (_UI_DIR / _BUNDLE).read_text(encoding="utf-8")
    m = re.search(r"export\{([^}]*)\};?\s*$", js)
    if not m:
        raise RuntimeError("Bundle ext-apps tidak dikenali (pernyataan export tidak ditemukan).")
    local = re.search(r"([A-Za-z0-9_$]+) as App(?:\s*[,}]|$)", m.group(1) + "}")
    if not local:
        raise RuntimeError("Bundle ext-apps tidak mengekspor App.")
    return js[: m.start()] + f"\nconst __ExtApp = {local.group(1)};\n"


_CSS = """
:root{--bg:#ffffff;--card:#f7f7f5;--line:#e6e4df;--ink:#1f1e1c;--mute:#6b6862;--accent:#2f6fed;
--good:#1f8a4c;--bad:#c2410c;--warn:#b45309;--crit:#b91c1c;--bar:#9db8f5;--bar2:#2f6fed}
:root[data-theme="dark"]{--bg:#1d1c1a;--card:#262522;--line:#3a3834;--ink:#ecebe8;--mute:#a5a29b;
--accent:#7aa2ff;--good:#4ade80;--bad:#fb923c;--warn:#fbbf24;--crit:#f87171;--bar:#3b4f80;--bar2:#7aa2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{padding:16px}h1{font-size:17px;margin:0}.sub{color:var(--mute);font-size:12.5px;margin-top:2px}
.seg{display:flex;gap:4px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:3px;
margin:12px 0;flex-wrap:wrap}.seg button{flex:1;min-width:44px;border:0;background:transparent;color:var(--mute);
padding:6px 8px;border-radius:7px;font:inherit;cursor:pointer}.seg button.on{background:var(--bg);color:var(--ink);
font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
@media(min-width:620px){.grid{grid-template-columns:repeat(4,minmax(0,1fr))}}
.k{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.k .l{color:var(--mute);font-size:12.5px}.k .v{font-size:21px;font-weight:700;margin-top:4px;white-space:nowrap}
.k .d{font-size:12px;margin-top:2px}.up{color:var(--good)}.down{color:var(--bad)}.flat{color:var(--mute)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin-top:12px}
.panel h2{font-size:13.5px;margin:0 0 8px}svg{display:block;width:100%;height:auto}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 6px;border-bottom:1px solid var(--line);
text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left;white-space:normal}
th{color:var(--mute);font-weight:600;font-size:12px}.scroll{overflow-x:auto}
.ins{display:flex;gap:8px;padding:8px 0;border-bottom:1px solid var(--line)}.ins:last-child{border:0}
.tag{font-size:11px;font-weight:700;padding:2px 6px;border-radius:6px;height:fit-content;border:1px solid}
.KRITIS{color:var(--crit)}.PERINGATAN{color:var(--warn)}.INFO{color:var(--accent)}
.foot{color:var(--mute);font-size:11.5px;margin-top:10px}.hide{display:none}.load{opacity:.5}
.legend{display:flex;gap:14px;color:var(--mute);font-size:12px;margin-top:6px}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.hl{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:12px;
padding:12px 14px;margin-bottom:12px}.hl.KRITIS{border-left-color:var(--crit)}.hl.INFO{border-left-color:var(--accent)}
.hl .t{display:flex;justify-content:space-between;gap:8px;align-items:flex-start;font-weight:700;color:var(--ink)}
.hl .m{color:var(--mute);margin-top:4px;font-size:13px}.pill{font-size:11.5px;font-weight:700;padding:2px 8px;
border-radius:999px;background:var(--line);color:var(--ink);white-space:nowrap}
.act{margin-top:10px;border:1px solid var(--accent);color:var(--accent);background:transparent;border-radius:8px;
padding:6px 12px;font:inherit;font-weight:600;cursor:pointer}.act:disabled{opacity:.5;cursor:default}
.badge{display:inline-block;min-width:18px;padding:0 5px;margin-left:6px;border-radius:999px;background:var(--crit);
color:#fff;font-size:11px;font-weight:700;line-height:18px;text-align:center}
.k .l{display:flex;align-items:center;gap:7px}.ic{display:inline-grid;place-items:center;width:24px;height:24px;
border-radius:7px;flex:none}.ic svg{width:15px;height:15px}
.ins .b{flex:1}.ins .h{font-weight:600}
"""

_APP_JS = r"""
const $ = (s) => document.querySelector(s);
const nf = new Intl.NumberFormat("id-ID");
const rp = (v) => v == null ? "–" : "Rp" + nf.format(Math.round(v));
const rpShort = (v) => v == null ? "–" : v >= 1e9 ? "Rp" + (v/1e9).toFixed(1).replace(".", ",") + " M"
  : v >= 1e6 ? "Rp" + (v/1e6).toFixed(1).replace(".", ",") + " jt" : v >= 1e4 ? "Rp" + Math.round(v/1e3) + " rb" : rp(v);
const num = (v, d=0) => v == null ? "–" : new Intl.NumberFormat("id-ID", {maximumFractionDigits: d}).format(v);
const pct = (v) => v == null ? "–" : num(v, 2) + "%";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let DATA = null, TAB = "ringkasan";

const ICONS = {
  uang: '<path d="M12 2v20M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  klik: '<path d="M9 9l11 4-5 2-2 5z"/><path d="M5 3v3M3 5h3M8 4l1 2M4 8l2 1"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  mata: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  cek: '<path d="M5 13l4 4L19 7"/>',
  koin: '<circle cx="12" cy="12" r="9"/><path d="M9 12h6M12 9v6"/>',
  naik: '<path d="M4 17l6-6 4 4 6-8"/><path d="M14 7h6v6"/>',
};
const icon = (k, c) => `<span class="ic" style="background:color-mix(in srgb, ${c} 14%, transparent);color:${c}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[k]}</svg></span>`;

const KPI = [
  ["biaya", "Biaya", rpShort, "lower", "uang", "#2f6fed"], ["klik", "Klik", (v)=>num(v), "higher", "klik", "#ea7a2b"],
  ["konversi", "Konversi", (v)=>num(v,1), "higher", "target", "#1f8a4c"],
  ["biaya_per_konversi", "Biaya/konversi", rpShort, "lower", "tag", "#9b51e0"],
  ["tayangan", "Tayangan", (v)=>num(v), "higher", "mata", "#7c5cff"], ["ctr", "CTR", pct, "higher", "cek", "#1f8a4c"],
  ["cpc", "CPC", rpShort, "lower", "koin", "#c08a00"], ["tingkat_konversi", "Tingkat konversi", pct, "higher", "naik", "#0e9f9a"],
];
const tile = ([k, label, f, better, ic, col]) => `<div class="k"><div class="l">${icon(ic, col)}${label}</div><div class="v">${f(DATA.kpi[k].nilai)}</div>${deltaHtml(DATA.kpi[k].perubahan_pct, better)}</div>`;
const ACTIONS = [];
const actBtn = (a) => { if (!a) return ""; ACTIONS.push(a.prompt); return `<button class="act" data-act="${ACTIONS.length - 1}">${esc(a.label)}</button>`; };

function deltaHtml(ch, better) {
  if (ch == null) return '<div class="d flat">— vs periode lalu</div>';
  if (better === "neutral") return `<div class="d flat">${ch >= 0 ? "▲" : "▼"} ${num(Math.abs(ch),1)}%</div>`;
  const good = better === "higher" ? ch >= 0 : ch <= 0;
  return `<div class="d ${good ? "up" : "down"}">${ch >= 0 ? "▲" : "▼"} ${num(Math.abs(ch),1)}% vs periode lalu</div>`;
}

function chart(days) {
  const W = 640, H = 170, pl = 6, pr = 6, pt = 10, pb = 22;
  const n = days.length || 1, bw = (W - pl - pr) / n;
  const maxC = Math.max(1, ...days.map(d => d.biaya)), maxV = Math.max(1, ...days.map(d => d.konversi));
  let bars = "", pts = [], labels = "";
  days.forEach((d, i) => {
    const h = (H - pt - pb) * d.biaya / maxC, x = pl + i * bw;
    bars += `<rect x="${x + bw*0.15}" y="${H - pb - h}" width="${bw*0.7}" height="${h}" rx="2" fill="var(--bar)"><title>${d.tanggal}: ${rp(d.biaya)}, ${num(d.klik)} klik, ${num(d.konversi,1)} konversi</title></rect>`;
    pts.push(`${x + bw/2},${H - pb - (H - pt - pb) * d.konversi / maxV}`);
    if (n <= 14 || i % Math.ceil(n / 10) === 0)
      labels += `<text x="${x + bw/2}" y="${H - 6}" font-size="10" text-anchor="middle" fill="var(--mute)">${d.tanggal.slice(8)}/${d.tanggal.slice(5,7)}</text>`;
  });
  const line = days.some(d => d.konversi) ? `<polyline points="${pts.join(" ")}" fill="none" stroke="var(--bar2)" stroke-width="2.2"/>` +
    days.map((d, i) => d.konversi ? `<circle cx="${pl + i*bw + bw/2}" cy="${H - pb - (H - pt - pb) * d.konversi / maxV}" r="3.2" fill="var(--bar2)"/>` : "").join("") : "";
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Biaya dan konversi harian">${bars}${line}${labels}</svg>
    <div class="legend"><span><span class="dot" style="background:var(--bar)"></span>Biaya harian</span>
    <span><span class="dot" style="background:var(--bar2)"></span>Konversi</span></div>`;
}

function render() {
  if (!DATA) return;
  const d = DATA, p = d.periode;
  $("#title").textContent = "Performa Google Ads" + (d.klien ? " — " + d.klien : "");
  $("#sub").textContent = `${p.mulai} s.d. ${p.selesai} (${p.hari} hari) · dibanding ${p.pembanding.mulai} s.d. ${p.pembanding.selesai}`;
  document.querySelectorAll("#ranges button").forEach(b => b.classList.toggle("on", +b.dataset.days === p.hari));
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === TAB));
  ACTIONS.length = 0;
  const h = d.sorotan;
  $("#hl").innerHTML = h ? `<div class="hl ${h.level}"><div class="t"><span>${esc(h.judul || "Temuan utama")}</span>${h.lencana ? `<span class="pill">${esc(h.lencana)}</span>` : ""}</div><div class="m">${esc(h.pesan)}</div>${actBtn(h.aksi)}</div>` : "";
  const urgent = d.insight.filter(i => i.level !== "INFO").length;
  $("#b-insight").innerHTML = urgent ? `<span class="badge">${urgent}</span>` : "";
  $("#b-ringkasan").innerHTML = h && h.level !== "INFO" ? '<span class="badge">1</span>' : "";
  $("#kpi").innerHTML = KPI.slice(0, 4).map(tile).join("");
  $("#kpi2").innerHTML = KPI.slice(4).map(tile).join("");
  $("#chart").innerHTML = chart(d.harian);
  $("#camps").innerHTML = `<div class="scroll"><table><tr><th>Kampanye</th><th>Biaya</th><th>Klik</th><th>CTR</th><th>Konv.</th><th>Biaya/konv.</th><th>Hilang krn budget</th></tr>` +
    (d.kampanye.length ? d.kampanye.map(c => `<tr><td>${esc(c.nama)}</td><td>${rp(c.biaya)}</td><td>${num(c.klik)}</td><td>${pct(c.ctr)}</td><td>${num(c.konversi,1)}</td><td>${rp(c.biaya_per_konversi)}</td><td>${c.hilang_karena_budget == null ? "–" : num(c.hilang_karena_budget*100,0) + "%"}</td></tr>`).join("")
      : `<tr><td colspan="7">Tidak ada kampanye yang tayang di periode ini.</td></tr>`) + "</table></div>";
  $("#ins").innerHTML = d.insight.map(i => `<div class="ins"><span class="tag ${i.level}">${i.level}</span><div class="b">${i.judul ? `<div class="h">${esc(i.judul)}</div>` : ""}<div>${esc(i.pesan)}</div>${actBtn(i.aksi)}</div></div>`).join("");
  document.querySelectorAll("button.act").forEach(b => b.onclick = () => ask(ACTIONS[+b.dataset.act], b));
  $("#foot").textContent = d.sumber;
  ["ringkasan", "kampanye", "insight"].forEach(t => $("#p-" + t).classList.toggle("hide", t !== TAB));
}

function readResult(r) {
  if (!r) return null;
  if (r.structuredContent && r.structuredContent.kpi) return r.structuredContent;
  if (r.structuredContent && r.structuredContent.result && r.structuredContent.result.kpi) return r.structuredContent.result;
  for (const c of r.content || []) {
    if (c.type === "text") { try { const j = JSON.parse(c.text); if (j.kpi) return j; } catch (e) {} }
  }
  return null;
}

const app = new __ExtApp({ name: "AdsAi Dashboard", version: "1.0.0" }, {});
function applyTheme(ctx) {
  const t = ctx && ctx.theme;
  if (t) document.documentElement.dataset.theme = t;
}
app.ontoolresult = (params) => { const j = readResult(params); if (j) { DATA = j; render(); } };
app.onhostcontextchanged = (ctx) => applyTheme(ctx);

async function ask(prompt, btn) {
  btn.disabled = true;
  try {
    await app.sendMessage({ role: "user", content: [{ type: "text", text: prompt }] });
    btn.textContent = "Terkirim ke Claude ✓";
  } catch (e) {
    try { await navigator.clipboard.writeText(prompt); btn.textContent = "Disalin — tempel ke chat"; }
    catch (e2) { btn.textContent = "Ketik ke Claude: " + prompt.slice(0, 60) + "…"; }
  }
}

document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => { TAB = b.dataset.tab; render(); });
document.querySelectorAll("#ranges button").forEach(b => b.onclick = async () => {
  if (!DATA) return;
  document.body.classList.add("load");
  try {
    const r = await app.callServerTool({ name: "performance_dashboard",
      arguments: { customer_id: DATA.customer_id, days: +b.dataset.days } });
    const j = readResult(r); if (j) { DATA = j; render(); }
  } catch (e) { $("#foot").textContent = "Gagal memuat ulang: " + e; }
  document.body.classList.remove("load");
});

await app.connect();
applyTheme(app.getHostContext && app.getHostContext());
"""


@lru_cache(maxsize=1)
def html() -> str:
    ranges = "".join(f'<button data-days="{n}" title="{n} hari">{n} hr</button>' for n in RANGES)
    return f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AdsAi — Dashboard</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1 id="title">Performa Google Ads</h1><div class="sub" id="sub">Memuat data…</div>
<div class="seg" id="ranges">{ranges}</div>
<div class="seg" id="tabs"><button data-tab="ringkasan" class="on">Ringkasan<span id="b-ringkasan"></span></button>
<button data-tab="kampanye">Kampanye</button><button data-tab="insight">Insight<span id="b-insight"></span></button></div>
<section id="p-ringkasan"><div id="hl"></div><div class="grid" id="kpi"></div><div class="grid" id="kpi2" style="margin-top:10px"></div>
<div class="panel"><h2>Harian</h2><div id="chart"></div></div></section>
<section id="p-kampanye" class="hide"><div class="panel"><h2>Per kampanye</h2><div id="camps"></div></div></section>
<section id="p-insight" class="hide"><div class="panel"><h2>Temuan</h2><div id="ins"></div></div></section>
<div class="foot" id="foot"></div>
</div>
<script type="module">
{_bundle()}
{_APP_JS}
</script></body></html>"""


def payload(d: dict[str, Any]) -> str:
    """Teks hasil tool: JSON lengkap (dibaca kartu & model) — ringkasan ada di kolom 'ringkasan'."""
    return json.dumps({**d, "ringkasan": text_summary(d)}, ensure_ascii=False)
