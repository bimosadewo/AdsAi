"""Monitor multi-syarat + pemindaian akun (setara Watch Agent Adspirer, tanpa kelemahannya).

Temuan dari Adspirer (Plus): monitor hanya SATU syarat, dan `test_monitor` mengabaikan "N hari berturut-turut"
sehingga uji coba tidak mencerminkan alarm sungguhan. Di AdsAi:
- satu monitor bisa berisi beberapa syarat (semua harus terpenuhi pada hari yang sama, logika DAN);
- `berturut_hari` dihitung dari data harian sungguhan — uji coba memakai logika yang sama persis;
- dievaluasi per kampanye (atau total akun), sehingga alarm menyebut kampanye mana yang memicu;
- CPA dengan 0 konversi dihitung sebagai "tak terhingga" bila ada biaya — kondisi terburuk, bukan diabaikan.

Monitor disimpan di berkas JSON (ADSAI_MONITORS_FILE; di server online /data/monitors.json).
Modul ini tidak mengubah akun Google Ads sama sekali.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from .config import PROJECT_ROOT
from .fmt import number, rupiah

METRICS = {
    "biaya": "biaya (Rp)", "klik": "klik", "tayangan": "tayangan", "konversi": "konversi",
    "cpa": "biaya per konversi (Rp)", "ctr": "CTR (%)", "cpc": "CPC (Rp)", "tingkat_konversi": "tingkat konversi (%)",
}
OPERATORS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b,
             "<=": lambda a, b: a <= b, "=": lambda a, b: a == b}
MAX_LOOKBACK = 30


@dataclass
class Condition:
    metrik: str
    operator: str
    nilai: float

    def check(self, row: dict[str, float]) -> bool:
        v = row.get(self.metrik)
        if v is None:
            return False
        return OPERATORS[self.operator](v, self.nilai)

    def text(self) -> str:
        v = rupiah(self.nilai) if self.metrik in ("biaya", "cpa", "cpc") else number(self.nilai, 2)
        return f"{METRICS[self.metrik]} {self.operator} {v}"


@dataclass
class Monitor:
    nama: str
    kondisi: list[Condition]
    customer_id: str
    berturut_hari: int = 1
    kampanye: str | None = None          # None = tiap kampanye aktif dinilai sendiri; "AKUN" = total akun
    aktif: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ringkas"] = describe(self)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Monitor":
        return Monitor(nama=d["nama"], kondisi=[Condition(**c) for c in d["kondisi"]], customer_id=d["customer_id"],
                       berturut_hari=int(d.get("berturut_hari", 1)), kampanye=d.get("kampanye"),
                       aktif=bool(d.get("aktif", True)), id=d.get("id") or uuid.uuid4().hex[:8])


def describe(m: Monitor) -> str:
    scope = "total akun" if m.kampanye == "AKUN" else (f"kampanye '{m.kampanye}'" if m.kampanye else "tiap kampanye")
    days = f"{m.berturut_hari} hari berturut-turut" if m.berturut_hari > 1 else "1 hari"
    return f"{' DAN '.join(c.text() for c in m.kondisi)} — {scope}, {days}"


_ALIASES = {"cost": "biaya", "spend": "biaya", "clicks": "klik", "impressions": "tayangan", "conversions": "konversi",
            "cost_per_conversion": "cpa", "biaya_per_konversi": "cpa", "conversion_rate": "tingkat_konversi"}


def _num(s: str) -> float:
    """'150.000' → 150000, '1,5' / '1.5' → 1.5, '150000' → 150000."""
    s = str(s).strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return float(s)


def parse_conditions(raw: Iterable[Any]) -> list[Condition]:
    """Terima [{"metrik","operator","nilai"}] atau teks "cpa > 150000"."""
    out = []
    for item in raw:
        if isinstance(item, str):
            m = re.fullmatch(r"\s*([a-z_]+)\s*(>=|<=|>|<|=)\s*([\d.,]+)\s*", item.lower())
            if not m:
                raise ValueError(f"Syarat tidak dikenali: {item!r}. Contoh: 'cpa > 150000', 'konversi = 0'.")
            metric, op, val = m.group(1), m.group(2), _num(m.group(3))
            item = {"metrik": metric, "operator": op, "nilai": val}
        metric = _ALIASES.get(str(item.get("metrik", "")).lower(), str(item.get("metrik", "")).lower())
        if metric not in METRICS:
            raise ValueError(f"Metrik '{item.get('metrik')}' tidak dikenal. Pilihan: {', '.join(METRICS)}.")
        op = str(item.get("operator", "")).strip()
        if op not in OPERATORS:
            raise ValueError(f"Operator '{op}' tidak dikenal. Pilihan: {', '.join(OPERATORS)}.")
        out.append(Condition(metric, op, _num(item["nilai"])))
    if not out:
        raise ValueError("Isi minimal satu syarat.")
    if len(out) > 5:
        raise ValueError("Maksimal 5 syarat per monitor.")
    return out


# ---------------------------------------------------------------------------
# Evaluasi (murni)
# ---------------------------------------------------------------------------

def _metrics(cost: float, clicks: float, impr: float, conv: float) -> dict[str, float]:
    return {"biaya": cost, "klik": clicks, "tayangan": impr, "konversi": conv,
            "cpa": (cost / conv) if conv else (math.inf if cost else 0.0),
            "ctr": (clicks / impr * 100) if impr else 0.0,
            "cpc": (cost / clicks) if clicks else 0.0,
            "tingkat_konversi": (conv / clicks * 100) if clicks else 0.0}


def daily_series(rows: list[dict[str, Any]], start: date, end: date, scope: str | None) -> dict[str, list[dict]]:
    """Baris GAQL harian per kampanye → {nama_entitas: [metrik per hari, urut tanggal]}."""
    def f(r, k):
        try:
            return float(r.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0

    acc: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        name = str(r.get("campaign.name") or r.get("campaign.id"))
        if scope and scope != "AKUN" and scope.lower() not in name.lower() and scope != str(r.get("campaign.id")):
            continue
        key = "Total akun" if scope == "AKUN" else name
        day = acc.setdefault(key, {}).setdefault(str(r.get("segments.date")), [0.0, 0.0, 0.0, 0.0])
        for i, k in enumerate(("metrics.cost", "metrics.clicks", "metrics.impressions", "metrics.conversions")):
            day[i] += f(r, k)
    out = {}
    for key, days in acc.items():
        series, d = [], start
        while d <= end:
            c, k, i, v = days.get(d.isoformat(), [0.0, 0.0, 0.0, 0.0])
            series.append({"tanggal": d.isoformat(), **_metrics(c, k, i, v)})
            d += timedelta(days=1)
        out[key] = series
    return out


def evaluate(m: Monitor, series: dict[str, list[dict]]) -> dict[str, Any]:
    """Hitung, per entitas, berapa hari terakhir BERTURUT-TURUT semua syarat terpenuhi (dari hari terbaru mundur)."""
    hits = []
    for name, days in series.items():
        streak = 0
        for row in reversed(days):
            if all(c.check(row) for c in m.kondisi):
                streak += 1
            else:
                break
        last = days[-1] if days else {}
        if streak >= m.berturut_hari:
            hits.append({"entitas": name, "hari_berturut": streak,
                         "kemarin": {k: (None if isinstance(v, float) and math.isinf(v) else
                                         round(v, 2) if isinstance(v, float) else v) for k, v in last.items()}})
    hits.sort(key=lambda h: (-h["hari_berturut"], -(h["kemarin"].get("biaya") or 0)))
    return {"id": m.id, "nama": m.nama, "aturan": describe(m), "terpicu": bool(hits), "pemicu": hits,
            "dinilai": len(series)}


# ---------------------------------------------------------------------------
# Penyimpanan
# ---------------------------------------------------------------------------

def store_path() -> Path:
    return Path(os.getenv("ADSAI_MONITORS_FILE") or PROJECT_ROOT / "monitors.json")


def load(path: Path | None = None) -> list[Monitor]:
    p = path or store_path()
    if not p.exists():
        return []
    return [Monitor.from_dict(d) for d in json.loads(p.read_text(encoding="utf-8")).get("monitor", [])]


def save(monitors: list[Monitor], path: Path | None = None) -> None:
    p = path or store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"monitor": [asdict(m) for m in monitors]}, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=p.parent, delete=False, suffix=".tmp") as t:
        t.write(data)
    os.replace(t.name, p)


# ---------------------------------------------------------------------------
# Data dari Google Ads
# ---------------------------------------------------------------------------

def q_daily(start: date, end: date) -> str:
    return ("SELECT campaign.id, campaign.name, segments.date, metrics.cost_micros, metrics.clicks, "
            "metrics.impressions, metrics.conversions FROM campaign "
            f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' "
            "AND campaign.status = 'ENABLED'")


def run(ads: Any, monitors: list[Monitor], today: date) -> list[dict[str, Any]]:
    """Nilai monitor terhadap data sampai kemarin (hari ini belum lengkap). Satu query per akun."""
    end = today - timedelta(days=1)
    by_cid: dict[str, list[Monitor]] = {}
    for m in monitors:
        if m.aktif:
            by_cid.setdefault(m.customer_id, []).append(m)
    out = []
    for cid, ms in by_cid.items():
        look = min(max(m.berturut_hari for m in ms) + 1, MAX_LOOKBACK)
        rows = ads.search(cid, q_daily(end - timedelta(days=look - 1), end))
        for m in ms:
            res = evaluate(m, daily_series(rows, end - timedelta(days=look - 1), end, m.kampanye))
            out.append({"customer_id": cid, "periode_sampai": end.isoformat(), **res})
    return out
