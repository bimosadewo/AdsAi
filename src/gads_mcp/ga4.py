"""Google Analytics 4: pengunjung website, sumber trafik, halaman masuk, dan klik WA/telepon (key event).

Berbeda dengan Google Business Profile, GA4 Data API TIDAK perlu persetujuan Google: cukup aktifkan
"Google Analytics Data API" dan "Google Analytics Admin API" di project Cloud, lalu buat token baca
(GA4_REFRESH_TOKEN, scope analytics.readonly) dengan akun Google yang punya akses ke properti GA4.
Kuota gratis per properti cukup untuk laporan harian/bulanan.

Hanya membaca — AdsAi tidak pernah mengubah pengaturan GA4.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
TOKEN_URI = "https://oauth2.googleapis.com/token"
DATA = "https://analyticsdata.googleapis.com/v1beta"
ADMIN = "https://analyticsadmin.googleapis.com/v1beta"

PAID = ("Paid Search", "Paid Shopping", "Paid Other", "Cross-network", "Paid Social", "Paid Video", "Display")


class Ga4Error(RuntimeError):
    """Error GA4 yang sudah diterjemahkan untuk pengguna."""


SETUP_HINT = ("GA4_REFRESH_TOKEN belum diisi. Aktifkan Google Analytics Data API & Admin API di project Cloud, lalu "
              "jalankan scripts/generate_refresh_token.py --ga4 (lihat docs/FASE-3-GA4.md).")


def explain(status: int, body: Any) -> str:
    err = body.get("error", {}) if isinstance(body, dict) else {}
    msg = err.get("message") or (body if isinstance(body, str) else "") or f"HTTP {status}"
    raw = str(body)
    text = f"Google Analytics menolak permintaan (HTTP {status}): {msg}"
    if "SERVICE_DISABLED" in raw or "has not been used in project" in raw:
        hint = ("API belum aktif. Cloud Console → APIs & Services → Library → aktifkan 'Google Analytics Data API' "
                "dan 'Google Analytics Admin API'.")
    elif status == 401 or "invalid_grant" in raw:
        hint = "Token GA4 kedaluwarsa/dicabut. Buat ulang: scripts/generate_refresh_token.py --ga4."
    elif status == 403:
        hint = ("Akun Google ini tidak punya akses ke properti tersebut. Di GA4: Admin → Akses properti → tambahkan "
                "email login dengan peran Viewer (Pembaca).")
    elif status == 429 or "RESOURCE_EXHAUSTED" in raw:
        hint = "Kuota token GA4 per jam habis. Coba lagi nanti atau persempit rentang/dimensi."
    elif status == 400 and ("Field" in msg or "dimension" in msg.lower() or "metric" in msg.lower()):
        hint = "Nama dimensi/metrik tidak dikenal GA4. Contoh valid: sessions, totalUsers, keyEvents, eventName."
    else:
        hint = ""
    return text + (f" || Saran: {hint}" if hint else "")


def property_name(value: str | int) -> str:
    v = str(value).strip()
    return v if v.startswith("properties/") else f"properties/{''.join(ch for ch in v if ch.isdigit())}"


class Ga4:
    def __init__(self, session: Any):
        self.session = session

    @classmethod
    def from_settings(cls, settings: Any) -> "Ga4":
        token = getattr(settings, "ga4_refresh_token", "")
        if not token:
            raise Ga4Error(SETUP_HINT)
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.credentials import Credentials
        creds = Credentials(None, refresh_token=token, client_id=settings.client_id,
                            client_secret=settings.client_secret, token_uri=TOKEN_URI, scopes=[SCOPE])
        return cls(AuthorizedSession(creds))

    def _call(self, method: str, url: str, params: dict | None = None, body: dict | None = None) -> dict:
        try:
            resp = self.session.request(method, url, params=params, json=body, timeout=60)
        except Exception as ex:  # noqa: BLE001
            raise Ga4Error(explain(401 if "invalid_grant" in str(ex) else 0, str(ex))) from ex
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = resp.text
        if resp.status_code >= 400:
            raise Ga4Error(explain(resp.status_code, data))
        return data if isinstance(data, dict) else {}

    def properties(self) -> list[dict[str, Any]]:
        out, params = [], {"pageSize": 200}
        while True:
            data = self._call("GET", f"{ADMIN}/accountSummaries", params=params)
            for acc in data.get("accountSummaries", []):
                for p in acc.get("propertySummaries", []):
                    out.append({"properti": p.get("property"), "nama": p.get("displayName"),
                                "akun": acc.get("displayName")})
            if not data.get("nextPageToken"):
                return out
            params["pageToken"] = data["nextPageToken"]

    def run_report(self, prop: str, start: str, end: str, dimensions: list[str], metrics: list[str],
                   limit: int = 50, order_metric: str | None = None, dimension_filter: dict | None = None) -> list[dict]:
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": start, "endDate": end}],
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [{"name": m} for m in metrics],
            "limit": limit,
        }
        if order_metric:
            body["orderBys"] = [{"metric": {"metricName": order_metric}, "desc": True}]
        if dimension_filter:
            body["dimensionFilter"] = dimension_filter
        return rows(self._call("POST", f"{DATA}/{property_name(prop)}:runReport", body=body))


def rows(report: dict) -> list[dict[str, Any]]:
    """Respons runReport → list dict {dimensi: nilai, metrik: angka}."""
    dims = [h["name"] for h in report.get("dimensionHeaders", [])]
    mets = [(h["name"], h.get("type", "")) for h in report.get("metricHeaders", [])]
    out = []
    for r in report.get("rows", []):
        item: dict[str, Any] = {d: v.get("value") for d, v in zip(dims, r.get("dimensionValues", []))}
        for (name, kind), v in zip(mets, r.get("metricValues", [])):
            raw = v.get("value", "0")
            try:
                num = float(raw)
                item[name] = int(num) if kind == "TYPE_INTEGER" or (num.is_integer() and "Rate" not in name) else num
            except ValueError:
                item[name] = raw
        out.append(item)
    return out


def pick_property(props: list[dict], query: str | None) -> dict:
    if not props:
        raise Ga4Error("Login ini tidak punya akses ke properti GA4 mana pun. Tambahkan emailnya di GA4 → Admin → "
                       "Akses properti (peran Viewer).")
    if query:
        q = str(query).strip().lower()
        hits = [p for p in props if q in (p["nama"] or "").lower() or property_name(q) == p["properti"]
                or q in (p["akun"] or "").lower()]
        if len(hits) == 1:
            return hits[0]
        names = ", ".join(f"{p['nama']} ({p['properti']})" for p in (hits or props))
        raise Ga4Error(f"Properti '{query}' {'ambigu' if hits else 'tidak ditemukan'}. Pilihan: {names}")
    if len(props) == 1:
        return props[0]
    names = ", ".join(f"{p['nama']} ({p['properti']})" for p in props)
    raise Ga4Error(f"Ada beberapa properti GA4, sebutkan lewat 'property': {names}")


def date_range(days: int, today: date | None = None) -> tuple[str, str]:
    """N hari sampai kemarin (hari ini belum lengkap), sama seperti dashboard Google Ads."""
    if not 1 <= days <= 365:
        raise Ga4Error("days harus 1–365.")
    end = (today or date.today()) - timedelta(days=1)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


def overview_findings(channels: list[dict], pages: list[dict], events: list[dict]) -> list[str]:
    out: list[str] = []
    total = sum(c.get("sessions", 0) for c in channels)
    key_total = sum(c.get("keyEvents", 0) for c in channels)
    if total:
        paid = sum(c.get("sessions", 0) for c in channels if c.get("sessionDefaultChannelGroup") in PAID)
        out.append(f"Iklan berbayar menyumbang {paid / total:.0%} kunjungan.")
    if total and not key_total:
        out.append("Tidak ada key event (konversi) tercatat. Tandai event klik WhatsApp/telepon sebagai key event "
                   "di GA4 → Admin → Events, supaya hasil website terukur.")
    best = [c for c in channels if c.get("sessions", 0) >= 20 and c.get("keyEvents", 0)]
    if best:
        top = max(best, key=lambda c: c["keyEvents"] / c["sessions"])
        out.append(f"Saluran paling efektif: {top['sessionDefaultChannelGroup']} "
                   f"({top['keyEvents'] / top['sessions']:.1%} kunjungan berujung key event).")
    weak = [p for p in pages if p.get("sessions", 0) >= 30 and p.get("engagementRate", 1) < 0.4]
    for p in weak[:3]:
        out.append(f"Halaman {p['landingPagePlusQueryString']} ramai ({p['sessions']} kunjungan) tapi keterlibatan "
                   f"rendah ({p['engagementRate']:.0%}) — cek kecepatan & isi halamannya.")
    wa = [e for e in events if any(k in (e.get("eventName") or "").lower() for k in ("whatsapp", "wa_", "klik", "call",
                                                                                      "phone", "telp"))]
    if wa:
        out.append("Event kontak: " + ", ".join(f"{e['eventName']} {e['eventCount']}x" for e in wa[:5]) + ".")
    return out
