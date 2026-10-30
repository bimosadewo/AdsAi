"""Meta Ads (Facebook & Instagram) lewat Marketing API — tahap 1: BACA SAJA.

Login memakai token System User dari Business Manager (META_ACCESS_TOKEN, izin ads_read). Token ini bisa dibuat
tanpa kedaluwarsa dan tidak terikat ke akun Facebook pribadi siapa pun. Akun iklan yang bisa dibaca = akun yang
ditugaskan (assign) ke System User tersebut. Panduan: docs/META-ADS.md.

Satuan uang:
- insights (spend, cpc, cpm) → string dalam mata uang akun (mis. "125000" = Rp125.000)
- budget kampanye/ad set → satuan terkecil mata uang ("sen"); IDR, JPY, dll. tidak punya sen (offset 1)
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v26.0"

# Mata uang tanpa satuan sen di Meta (budget sudah dalam satuan utuh). Selain ini dibagi 100.
NO_CENTS = {"CLP", "COP", "CRC", "HUF", "IDR", "ISK", "JPY", "KRW", "PYG", "TWD", "VND"}

INSIGHT_FIELDS = ("spend", "impressions", "reach", "frequency", "clicks", "inline_link_clicks", "ctr", "cpc", "cpm",
                  "actions")
# Hasil yang dihitung, urut prioritas. Nilai = action_type Meta; yang pertama ada dipakai (hindari dobel hitung).
RESULTS = {
    "percakapan_wa": ("onsite_conversion.messaging_conversation_started_7d",),
    "prospek": ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"),
    "pembelian": ("purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase"),
    "kontak": ("contact_total", "offsite_conversion.fb_pixel_contact", "onsite_web_contact"),
}
ACCOUNT_STATUS = {1: "AKTIF", 2: "NONAKTIF", 3: "TUNGGAKAN", 7: "DITINJAU (RISIKO)", 8: "MENUNGGU PELUNASAN",
                  9: "MASA TENGGANG", 100: "MENUNGGU DITUTUP", 101: "DITUTUP"}
AD_FIELDS = ("id,name,effective_status,adset_id,campaign_id,adset{name},campaign{name},"
             "creative{id,name,body,title,object_story_id,effective_object_story_id,thumbnail_url}")
BREAKDOWNS = ("age", "gender", "publisher_platform", "platform_position", "device_platform", "region", "country")


class MetaError(RuntimeError):
    """Error Meta yang sudah diterjemahkan untuk pengguna."""


SETUP_HINT = ("META_ACCESS_TOKEN belum diisi. Buat token System User (izin ads_read) di Business Manager, simpan "
              "di .env / Secret Manager. Langkahnya: docs/META-ADS.md.")


def explain(status: int, body: Any) -> str:
    err = body.get("error", {}) if isinstance(body, dict) else {}
    code, sub = err.get("code"), err.get("error_subcode")
    msg = err.get("error_user_msg") or err.get("message") or (body if isinstance(body, str) else f"HTTP {status}")
    text = f"Meta menolak permintaan (kode {code or status}): {msg}"
    if code == 190:
        hint = ("Token tidak valid/kedaluwarsa/dicabut. Buat ulang token System User di Business Manager dan "
                "perbarui META_ACCESS_TOKEN.")
    elif code in (10, 200, 294) or (code and 200 <= int(code) <= 299):
        hint = ("Izin kurang: token perlu ads_read (baca) atau ads_management (membuat/mengubah iklan), dan akun "
                "iklan harus ditugaskan ke System User dengan izin yang sesuai (Lihat performa / Kelola kampanye) — "
                "Business Manager → Pengguna → Pengguna sistem → Tetapkan aset. Lihat docs/META-ADS.md.")
    elif code in (4, 17, 32, 613, 80000, 80004):
        hint = "Batas panggilan Meta tercapai. Tunggu beberapa menit atau persempit rentang tanggal."
    elif code == 100:
        hint = "Parameter tidak dikenal Meta (ID akun/field/breakdown). Cek ID dengan meta_status."
    elif code == 2635 or "deprecated" in str(msg).lower():
        hint = "Versi API sudah dihentikan Meta. Naikkan META_API_VERSION (mis. v26.0)."
    else:
        hint = ""
    if sub:
        text += f" (subkode {sub})"
    return text + (f" || Saran: {hint}" if hint else "")


def account_id(value: str | int) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        raise MetaError(f"ID akun iklan Meta tidak valid: {value!r}")
    return f"act_{digits}"


class Meta:
    def __init__(self, session: Any, token: str, version: str = DEFAULT_VERSION):
        self.session, self.token, self.version = session, token, version

    @classmethod
    def from_settings(cls, settings: Any) -> "Meta":
        token = getattr(settings, "meta_access_token", "")
        if not token:
            raise MetaError(SETUP_HINT)
        import requests
        return cls(requests.Session(), token, getattr(settings, "meta_api_version", "") or DEFAULT_VERSION)

    def _get(self, path_or_url: str, params: dict | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("https://") else f"{GRAPH}/{self.version}/{path_or_url}"
        # paging.next sudah memuat token & parameter; selain itu token dikirim sebagai parameter.
        query = None if path_or_url.startswith("https://") else {**(params or {}), "access_token": self.token}
        return self._send("GET", url, params=query)

    def _post(self, path: str, fields: dict[str, Any], method: str = "POST") -> dict[str, Any]:
        """Tulis ke Meta. Nilai bertingkat (targeting, promoted_object, ...) dikirim sebagai JSON string."""
        form = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in fields.items() if v is not None}
        return self._send(method, f"{GRAPH}/{self.version}/{path}", data={**form, "access_token": self.token})

    def _send(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        try:
            resp = self.session.request(method, url, timeout=60, **kw)
        except Exception as ex:  # noqa: BLE001
            raise MetaError(f"Tidak bisa menghubungi Meta: {type(ex).__name__}") from ex
        try:
            data = resp.json()
        except ValueError:
            data = resp.text
        if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            raise MetaError(explain(resp.status_code, data))
        return data if isinstance(data, dict) else {}

    def _all(self, path: str, params: dict, limit: int = 5000) -> list[dict]:
        data = self._get(path, params)
        out = list(data.get("data", []))
        while (nxt := (data.get("paging") or {}).get("next")) and len(out) < limit:
            data = self._get(nxt)
            out += data.get("data", [])
        return out[:limit]

    def ad_accounts(self) -> list[dict[str, Any]]:
        rows = self._all("me/adaccounts", {"fields": "id,account_id,name,account_status,currency,timezone_name,"
                                                     "business_name,amount_spent", "limit": 200})
        return [{"akun": r.get("id"), "nama": r.get("name"), "bisnis": r.get("business_name"),
                 "status": ACCOUNT_STATUS.get(r.get("account_status"), r.get("account_status")),
                 "mata_uang": r.get("currency"), "zona_waktu": r.get("timezone_name")} for r in rows]

    def campaigns(self, act: str) -> list[dict[str, Any]]:
        return self._all(f"{account_id(act)}/campaigns",
                         {"fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget,"
                                    "bid_strategy", "limit": 200})

    def insights(self, act: str, since: str, until: str, level: str = "account",
                 fields: tuple[str, ...] | list[str] = INSIGHT_FIELDS, breakdowns: list[str] | None = None,
                 daily: bool = False) -> list[dict[str, Any]]:
        if level not in ("account", "campaign", "adset", "ad"):
            raise MetaError("level harus account | campaign | adset | ad.")
        ids = {"campaign": ["campaign_id", "campaign_name"], "adset": ["adset_id", "adset_name", "campaign_name"],
               "ad": ["ad_id", "ad_name", "adset_name", "campaign_name"]}.get(level, [])
        params = {"level": level, "fields": ",".join([*ids, *fields]), "limit": 500,
                  "time_range": json.dumps({"since": since, "until": until})}
        if breakdowns:
            bad = [b for b in breakdowns if b not in BREAKDOWNS]
            if bad:
                raise MetaError(f"Breakdown tidak didukung: {', '.join(bad)}. Pilihan: {', '.join(BREAKDOWNS)}")
            params["breakdowns"] = ",".join(breakdowns)
        if daily:
            params["time_increment"] = 1
        return self._all(f"{account_id(act)}/insights", params)


    def get(self, object_id: str, fields: str) -> dict[str, Any]:
        return self._get(str(object_id), {"fields": fields})

    def ads(self, act: str, campaign_id: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"fields": AD_FIELDS, "limit": 200}
        # Default Meta menyembunyikan iklan yang diarsipkan — padahal iklan lama yang terbukti sering diarsip.
        filters = [{"field": "effective_status", "operator": "IN",
                    "value": ["ACTIVE", "PAUSED", "ARCHIVED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "IN_PROCESS",
                              "WITH_ISSUES", "DISAPPROVED", "PENDING_REVIEW"]}]
        if campaign_id:
            filters.append({"field": "campaign.id", "operator": "EQUAL", "value": str(campaign_id)})
        params["filtering"] = json.dumps(filters)
        return self._all(f"{account_id(act)}/ads", params, limit=500)

    def create(self, act: str, edge: str, fields: dict[str, Any], validate_only: bool = False) -> dict[str, Any]:
        """edge: campaigns | adsets | ads. validate_only=True → Meta memeriksa tanpa membuat apa pun."""
        if validate_only:
            fields = {**fields, "execution_options": ["validate_only"]}
        return self._post(f"{account_id(act)}/{edge}", fields)

    def update(self, object_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._post(str(object_id), fields)

    def delete(self, object_id: str) -> dict[str, Any]:
        return self._post(str(object_id), {}, method="DELETE")


# --- olah data ---------------------------------------------------------------------------------------------

def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def results(actions: list[dict] | None) -> dict[str, float]:
    by_type = {a.get("action_type"): _num(a.get("value")) for a in actions or []}
    out = {}
    for name, types in RESULTS.items():
        for t in types:
            if t in by_type:
                out[name] = by_type[t]
                break
    return out


def clean(row: dict[str, Any]) -> dict[str, Any]:
    """Baris insights Meta → angka siap baca + hasil (WA/prospek/pembelian/kontak) + biaya per hasil."""
    out: dict[str, Any] = {k: v for k, v in row.items() if k.endswith(("_id", "_name")) or k in BREAKDOWNS
                           or k in ("date_start",)}
    spend = _num(row.get("spend"))
    out.update(biaya=round(spend), tayangan=int(_num(row.get("impressions"))), jangkauan=int(_num(row.get("reach"))),
               frekuensi=round(_num(row.get("frequency")), 2), klik=int(_num(row.get("clicks"))),
               klik_link=int(_num(row.get("inline_link_clicks"))), ctr=round(_num(row.get("ctr")), 2),
               cpc=round(_num(row.get("cpc"))), cpm=round(_num(row.get("cpm"))))
    res = results(row.get("actions"))
    out["hasil"] = {k: int(v) for k, v in res.items()}
    out["biaya_per_hasil"] = {k: round(spend / v) for k, v in res.items() if v}
    if row.get("date_start") and row.get("date_stop") and row["date_start"] != row["date_stop"]:
        out.pop("date_start", None)
    return out


def budget(c: dict, currency: str) -> dict[str, Any]:
    div = 1 if (currency or "").upper() in NO_CENTS else 100
    out = {}
    if c.get("daily_budget"):
        out["harian"] = _num(c["daily_budget"]) / div
    if c.get("lifetime_budget"):
        out["total"] = _num(c["lifetime_budget"]) / div
    return out


def pick_account(accounts: list[dict], query: str | None) -> dict:
    if not accounts:
        raise MetaError("Token ini tidak punya akses ke akun iklan mana pun. Tetapkan akun iklan ke System User di "
                        "Business Manager.")
    if query:
        q = str(query).strip().lower()
        digits = "".join(ch for ch in q if ch.isdigit())
        hits = [a for a in accounts if q in (a["nama"] or "").lower() or (digits and a["akun"] == f"act_{digits}")]
        if len(hits) == 1:
            return hits[0]
        names = ", ".join(f"{a['nama']} ({a['akun']})" for a in (hits or accounts))
        raise MetaError(f"Akun '{query}' {'ambigu' if hits else 'tidak ditemukan'}. Pilihan: {names}")
    if len(accounts) == 1:
        return accounts[0]
    names = ", ".join(f"{a['nama']} ({a['akun']})" for a in accounts)
    raise MetaError(f"Ada beberapa akun iklan Meta, sebutkan lewat 'account': {names}")


def ranges(days: int, today: date) -> tuple[tuple[str, str], tuple[str, str]]:
    """(N hari sampai kemarin, N hari sebelumnya)."""
    if not 1 <= days <= 90:
        raise MetaError("days harus 1–90.")
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    return (start.isoformat(), end.isoformat()), ((prev_end - timedelta(days=days - 1)).isoformat(),
                                                  prev_end.isoformat())


def change(now: float, before: float) -> str | None:
    if not before:
        return None
    return f"{(now - before) / before:+.0%}"


def findings(campaigns: list[dict], placements: list[dict]) -> list[str]:
    out: list[str] = []
    for c in campaigns:
        name = c.get("campaign_name")
        if c["biaya"] >= 50_000 and not c["hasil"]:
            out.append(f"'{name}' menghabiskan {c['biaya']:,.0f} tanpa hasil (WA/prospek/pembelian) — cek tujuan "
                       "kampanye, pelacakan, atau jeda.".replace(",", "."))
        if c["frekuensi"] >= 3.5:
            out.append(f"'{name}' frekuensi {c['frekuensi']} — orang yang sama melihat iklan terlalu sering, materi "
                       "iklan mulai jenuh. Ganti/variasikan materi atau perluas audiens.")
    total = sum(p["biaya"] for p in placements) or 0
    an = sum(p["biaya"] for p in placements if p.get("publisher_platform") == "audience_network")
    if total and an / total >= 0.15:
        out.append(f"{an / total:.0%} biaya tayang di Audience Network (aplikasi/game pihak ketiga). Biasanya "
                   "kualitasnya rendah untuk bisnis lokal — pertimbangkan penempatan manual tanpa Audience Network.")
    paid = [c for c in campaigns if c["biaya_per_hasil"]]
    if len(paid) >= 2:
        best = min(paid, key=lambda c: min(c["biaya_per_hasil"].values()))
        kind, cost = min(best["biaya_per_hasil"].items(), key=lambda kv: kv[1])
        out.append(f"Biaya per hasil termurah: '{best.get('campaign_name')}' ({kind} {cost:,.0f} per hasil)."
                   .replace(",", "."))
    return out
