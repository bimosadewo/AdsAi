"""Google Business Profile (GBP): daftar lokasi + audit kelengkapan, ulasan, postingan serentak, jam khusus.

Akses API GBP harus disetujui Google per project (lihat docs/FASE-1-GBP.md). Sebelum disetujui kuota = 0 dan
setiap panggilan ditolak (429/403) — pesan error di sini menjelaskan artinya dalam bahasa awam.

Login memakai OAuth client yang sama dengan Google Ads, tetapi refresh token TERPISAH (GBP_REFRESH_TOKEN, scope
business.manage) supaya token Google Ads yang sudah berjalan tidak perlu dibuat ulang.

API yang dipakai:
- Account Management v1   : daftar akun / grup lokasi
- Business Information v1 : data lokasi, jam khusus (PATCH, mendukung validateOnly → pratinjau tervalidasi Google)
- Google My Business v4   : ulasan dan postingan (localPosts) — belum ada penggantinya di API baru
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

SCOPE = "https://www.googleapis.com/auth/business.manage"
TOKEN_URI = "https://oauth2.googleapis.com/token"
ACCOUNTS = "https://mybusinessaccountmanagement.googleapis.com/v1"
INFO = "https://mybusinessbusinessinformation.googleapis.com/v1"
V4 = "https://mybusiness.googleapis.com/v4"

READ_MASK = ("name,title,storeCode,phoneNumbers,categories,storefrontAddress,websiteUri,regularHours,specialHours,"
             "serviceArea,openInfo,metadata,profile,labels")

POST_MAX = 1500            # batas karakter isi postingan
REPLY_MAX = 4096           # batas karakter balasan ulasan
DESCRIPTION_MIN = 250      # deskripsi lebih pendek dari ini dianggap belum optimal (maks Google 750)
CTA_TYPES = ("BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL")
TOPICS = ("STANDARD", "OFFER", "EVENT")
STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}


class GbpError(RuntimeError):
    """Error GBP yang sudah diterjemahkan untuk pengguna."""


SETUP_HINT = ("GBP_REFRESH_TOKEN belum diisi. Jalankan scripts/generate_refresh_token.py --gbp setelah akses API "
              "Google Business Profile disetujui (lihat docs/FASE-1-GBP.md).")


def explain(status: int, body: dict[str, Any] | str) -> str:
    """Terjemahkan respons error Google menjadi saran yang bisa ditindaklanjuti."""
    err = body.get("error", {}) if isinstance(body, dict) else {}
    msg = err.get("message") or (body if isinstance(body, str) else "") or f"HTTP {status}"
    text = f"Google Business Profile menolak permintaan (HTTP {status}): {msg}"
    raw = str(body)
    if status == 429 or "RATE_LIMIT_EXCEEDED" in raw or "Quota exceeded" in raw:
        hint = ("Kuota project masih 0 → akses API GBP belum disetujui Google. Cek di Cloud Console → APIs & "
                "Services → My Business Business Information API → Quotas: 0 QPM = belum, 300 QPM = sudah.")
    elif "SERVICE_DISABLED" in raw or "has not been used in project" in raw:
        hint = ("API GBP belum diaktifkan di project. Aktifkan ketujuh API Business Profile di Cloud Console "
                "(daftar di docs/FASE-1-GBP.md, Langkah 4).")
    elif status == 401 or "invalid_grant" in raw:
        hint = "Token GBP kedaluwarsa/dicabut. Buat ulang: scripts/generate_refresh_token.py --gbp."
    elif status == 403:
        hint = ("Akun Google ini bukan Pemilik/Pengelola lokasi tersebut, atau scope business.manage belum "
                "diberikan. Pastikan email login terdaftar di profil (Orang dan akses).")
    elif status == 404:
        hint = "Lokasi/ulasan/postingan tidak ditemukan. Ambil ID terbaru dengan gbp_locations."
    else:
        hint = ""
    return text + (f" || Saran: {hint}" if hint else "")


class Gbp:
    """Klien REST tipis. `session` = objek mirip requests.Session (AuthorizedSession di produksi, palsu di tes)."""

    def __init__(self, session: Any):
        self.session = session

    @classmethod
    def from_settings(cls, settings: Any) -> "Gbp":
        token = getattr(settings, "gbp_refresh_token", "")
        if not token:
            raise GbpError(SETUP_HINT)
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.credentials import Credentials
        creds = Credentials(None, refresh_token=token, client_id=settings.client_id,
                            client_secret=settings.client_secret, token_uri=TOKEN_URI, scopes=[SCOPE])
        return cls(AuthorizedSession(creds))

    def _call(self, method: str, url: str, params: dict | None = None, body: dict | None = None) -> dict[str, Any]:
        try:
            resp = self.session.request(method, url, params=params, json=body, timeout=60)
        except Exception as ex:  # noqa: BLE001 — RefreshError, koneksi putus, dll.
            raw = str(ex)
            raise GbpError(explain(401 if "invalid_grant" in raw else 0, raw)) from ex
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = resp.text
        if resp.status_code >= 400:
            raise GbpError(explain(resp.status_code, data))
        return data if isinstance(data, dict) else {}

    def _pages(self, url: str, key: str, params: dict | None = None, limit: int = 1000) -> list[dict]:
        out: list[dict] = []
        params = dict(params or {})
        while True:
            data = self._call("GET", url, params=params)
            out += data.get(key, [])
            token = data.get("nextPageToken")
            if not token or len(out) >= limit:
                return out[:limit]
            params["pageToken"] = token

    # --- baca ---------------------------------------------------------------------------------------------
    def accounts(self) -> list[dict]:
        return self._pages(f"{ACCOUNTS}/accounts", "accounts", {"pageSize": 20})

    def locations(self, account: str) -> list[dict]:
        return self._pages(f"{INFO}/{account_name(account)}/locations", "locations",
                           {"readMask": READ_MASK, "pageSize": 100})

    def reviews(self, account: str, location: str, limit: int = 50) -> dict[str, Any]:
        url = f"{V4}/{account_name(account)}/{location_name(location)}/reviews"
        data = self._call("GET", url, params={"pageSize": min(limit, 50), "orderBy": "updateTime desc"})
        reviews = data.get("reviews", [])
        while data.get("nextPageToken") and len(reviews) < limit:
            data = self._call("GET", url, params={"pageSize": 50, "orderBy": "updateTime desc",
                                                  "pageToken": data["nextPageToken"]})
            reviews += data.get("reviews", [])
        return {"averageRating": data.get("averageRating"), "totalReviewCount": data.get("totalReviewCount"),
                "reviews": reviews[:limit]}

    def review(self, review: str) -> dict[str, Any]:
        return self._call("GET", f"{V4}/{review}")

    # --- tulis --------------------------------------------------------------------------------------------
    def reply(self, review: str, comment: str) -> dict[str, Any]:
        return self._call("PUT", f"{V4}/{review}/reply", body={"comment": comment})

    def create_post(self, account: str, location: str, post: dict) -> dict[str, Any]:
        return self._call("POST", f"{V4}/{account_name(account)}/{location_name(location)}/localPosts", body=post)

    def patch_special_hours(self, location: str, special_hours: dict, validate_only: bool) -> dict[str, Any]:
        params = {"updateMask": "specialHours"}
        if validate_only:
            params["validateOnly"] = "true"
        return self._call("PATCH", f"{INFO}/{location_name(location)}", params=params,
                          body={"specialHours": special_hours})


# --- nama sumber daya -------------------------------------------------------------------------------------

def account_name(value: str) -> str:
    v = str(value).strip()
    return v if v.startswith("accounts/") else f"accounts/{v}"


def location_name(value: str) -> str:
    v = str(value).strip()
    return v[v.index("locations/"):] if "locations/" in v else f"locations/{v}"


def pick_account(accounts: list[dict], query: str | None) -> dict:
    """Pilih akun/grup lokasi dari nama atau ID. Tanpa query: satu-satunya akun, atau grup lokasi pertama."""
    if not accounts:
        raise GbpError("Tidak ada akun Business Profile yang bisa diakses login ini.")
    if query:
        q = query.strip().lower()
        hits = [a for a in accounts
                if q in (a.get("accountName") or "").lower() or account_name(q) == a.get("name")]
        if len(hits) == 1:
            return hits[0]
        names = ", ".join(f"{a.get('accountName')} ({a.get('name')})" for a in (hits or accounts))
        raise GbpError(f"Akun '{query}' {'ambigu' if hits else 'tidak ditemukan'}. Pilihan: {names}")
    if len(accounts) == 1:
        return accounts[0]
    groups = [a for a in accounts if a.get("type") == "LOCATION_GROUP"]
    if len(groups) == 1:
        return groups[0]
    names = ", ".join(f"{a.get('accountName')} ({a.get('name')})" for a in accounts)
    raise GbpError(f"Ada beberapa akun/grup lokasi, sebutkan salah satu lewat 'account': {names}")


def filter_locations(locations: list[dict], wanted: list[str] | str | None) -> list[dict]:
    """wanted: None/'semua' = semua lokasi; atau daftar ID / potongan nama / kode toko."""
    if wanted in (None, "", "semua", "all") or wanted == ["semua"]:
        return locations
    items = [wanted] if isinstance(wanted, str) else list(wanted)
    out, missing = [], []
    for w in items:
        key = w.strip().lower()
        hits = [l for l in locations
                if location_name(key) == l.get("name") or key == (l.get("storeCode") or "").lower()
                or key in (l.get("title") or "").lower()]
        (out.extend(h for h in hits if h not in out) if hits else missing.append(w))
    if missing:
        raise GbpError(f"Lokasi tidak ditemukan: {', '.join(missing)}. Lihat daftar lewat gbp_locations.")
    return out


# --- audit kelengkapan profil (t104) ----------------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    key: str
    weight: int
    label: str
    fix: str


CHECKS = (
    Check("terverifikasi", 20, "Profil terverifikasi", "Selesaikan verifikasi di business.google.com."),
    Check("kategori", 15, "Kategori utama tepat", "Isi kategori utama yang paling spesifik."),
    Check("kategori_tambahan", 5, "Kategori tambahan", "Tambahkan 2–5 kategori tambahan yang relevan."),
    Check("telepon", 10, "Nomor telepon", "Isi nomor telepon utama (bisa nomor WhatsApp bisnis)."),
    Check("website", 10, "Website", "Isi website; idealnya halaman khusus cabang ini."),
    Check("jam", 15, "Jam buka rutin", "Isi jam buka setiap hari."),
    Check("deskripsi", 10, "Deskripsi ≥ 250 karakter",
          f"Tulis deskripsi {DESCRIPTION_MIN}–750 karakter: layanan, area, keunggulan."),
    Check("alamat", 10, "Alamat / area layanan", "Isi alamat toko atau area layanan."),
    Check("buka", 5, "Status buka", "Status profil bukan 'OPEN' — cek apakah ditandai tutup."),
)


def _has(loc: dict, key: str) -> bool:
    if key == "terverifikasi":
        return bool((loc.get("metadata") or {}).get("hasVoiceOfMerchant"))
    if key == "kategori":
        return bool(((loc.get("categories") or {}).get("primaryCategory") or {}).get("displayName"))
    if key == "kategori_tambahan":
        return bool((loc.get("categories") or {}).get("additionalCategories"))
    if key == "telepon":
        return bool((loc.get("phoneNumbers") or {}).get("primaryPhone"))
    if key == "website":
        return bool(loc.get("websiteUri"))
    if key == "jam":
        return bool((loc.get("regularHours") or {}).get("periods"))
    if key == "deskripsi":
        return len(((loc.get("profile") or {}).get("description") or "").strip()) >= DESCRIPTION_MIN
    if key == "alamat":
        return bool(loc.get("storefrontAddress") or loc.get("serviceArea"))
    if key == "buka":
        return (loc.get("openInfo") or {}).get("status", "OPEN") == "OPEN"
    raise KeyError(key)


def city(loc: dict) -> str:
    addr = loc.get("storefrontAddress") or {}
    return addr.get("locality") or addr.get("administrativeArea") or ""


def summarize(loc: dict) -> dict[str, Any]:
    """Ringkasan satu lokasi + skor kelengkapan 0–100 dan daftar perbaikan."""
    passed = [c for c in CHECKS if _has(loc, c.key)]
    missing = [c for c in CHECKS if c not in passed]
    addr = loc.get("storefrontAddress") or {}
    return {
        "lokasi": loc.get("name"),
        "nama": loc.get("title"),
        "kode_toko": loc.get("storeCode"),
        "kota": city(loc),
        "alamat": ", ".join(addr.get("addressLines") or []),
        "kategori": ((loc.get("categories") or {}).get("primaryCategory") or {}).get("displayName"),
        "telepon": (loc.get("phoneNumbers") or {}).get("primaryPhone"),
        "website": loc.get("websiteUri"),
        "maps": (loc.get("metadata") or {}).get("mapsUri"),
        "link_ulasan": (loc.get("metadata") or {}).get("newReviewUri"),
        "skor_kelengkapan": sum(c.weight for c in passed),
        "perlu_dilengkapi": [f"{c.label}: {c.fix}" for c in missing],
    }


def audit(locations: list[dict]) -> dict[str, Any]:
    rows = sorted((summarize(l) for l in locations), key=lambda r: r["skor_kelengkapan"])
    findings = []
    weak = [r for r in rows if r["skor_kelengkapan"] < 80]
    if weak:
        findings.append(f"{len(weak)} dari {len(rows)} lokasi skornya < 80: "
                        + ", ".join(f"{r['nama']} ({r['skor_kelengkapan']})" for r in weak[:10]))
    for check in CHECKS:
        n = sum(1 for l in locations if not _has(l, check.key))
        if n and len(rows) > 1:
            findings.append(f"{check.label} kosong/kurang di {n} lokasi.")
    avg = round(sum(r["skor_kelengkapan"] for r in rows) / len(rows)) if rows else None
    return {"jumlah_lokasi": len(rows), "rata_rata_skor": avg, "temuan": findings, "lokasi": rows}


# --- ulasan (t106) ---------------------------------------------------------------------------------------

def review_row(r: dict) -> dict[str, Any]:
    return {
        "ulasan": r.get("name"),
        "bintang": STARS.get(r.get("starRating", ""), None),
        "pengulas": (r.get("reviewer") or {}).get("displayName"),
        "komentar": r.get("comment", ""),
        "waktu": r.get("createTime"),
        "balasan": (r.get("reviewReply") or {}).get("comment"),
    }


def review_findings(rows: list[dict]) -> list[str]:
    out = []
    unreplied = [r for r in rows if not r["balasan"]]
    low = [r for r in unreplied if (r["bintang"] or 5) <= 2]
    if low:
        out.append(f"{len(low)} ulasan bintang 1–2 belum dibalas — prioritaskan, balas sopan & tawarkan solusi.")
    if unreplied:
        out.append(f"{len(unreplied)} ulasan belum dibalas.")
    return out


def check_reply(comment: str) -> list[str]:
    text = (comment or "").strip()
    if not text:
        raise GbpError("Balasan kosong.")
    if len(text) > REPLY_MAX:
        raise GbpError(f"Balasan {len(text)} karakter, maksimal {REPLY_MAX}.")
    notes = []
    if re.search(r"(\+62|\b0)8\d{7,}", text):
        notes.append("Balasan memuat nomor HP — boleh, tapi pastikan memang nomor resmi bisnis.")
    return notes


# --- postingan serentak (t105) ---------------------------------------------------------------------------

def render(template: str, loc: dict) -> str:
    """Isi placeholder per cabang: {nama} {kota} {alamat} {telepon} {website} {kode}."""
    s = summarize(loc)
    values = {"nama": s["nama"] or "", "kota": s["kota"], "alamat": s["alamat"], "telepon": s["telepon"] or "",
              "website": s["website"] or "", "kode": s["kode_toko"] or ""}
    try:
        return template.format(**values).strip()
    except KeyError as ex:
        raise GbpError(f"Placeholder {{{ex.args[0]}}} tidak dikenal. Pakai: {', '.join('{'+k+'}' for k in values)}")


def _date(value: str) -> dict[str, int]:
    try:
        d = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as ex:
        raise GbpError(f"Tanggal harus YYYY-MM-DD, didapat: {value!r}") from ex
    return {"year": d.year, "month": d.month, "day": d.day}


def _time(value: str) -> dict[str, int]:
    m = re.fullmatch(r"(\d{1,2})[.:](\d{2})", value.strip())
    if not m or int(m.group(1)) > 24 or int(m.group(2)) > 59:
        raise GbpError(f"Jam harus HH:MM (mis. 08:00), didapat: {value!r}")
    return {"hours": int(m.group(1)), "minutes": int(m.group(2))}


def build_post(summary: str, topic: str = "STANDARD", cta_type: str | None = None, cta_url: str | None = None,
               image_url: str | None = None, title: str | None = None, start: str | None = None,
               end: str | None = None, coupon: str | None = None, terms: str | None = None,
               language: str = "id") -> tuple[dict, list[str]]:
    """Body localPost untuk satu lokasi + catatan. topic: STANDARD (berita), OFFER (promo), EVENT (acara)."""
    topic = (topic or "STANDARD").upper()
    if topic not in TOPICS:
        raise GbpError(f"topic harus salah satu dari {', '.join(TOPICS)}.")
    text = (summary or "").strip()
    if not text:
        raise GbpError("Isi postingan kosong.")
    if len(text) > POST_MAX:
        raise GbpError(f"Isi postingan {len(text)} karakter, maksimal {POST_MAX}.")
    notes: list[str] = []
    if re.search(r"(\+62|\b0)8\d{7,}|\b021[-\s]?\d{6,}", text):
        notes.append("Google sering MENOLAK postingan yang memuat nomor telepon di teks. Pakai tombol CALL saja.")
    post: dict[str, Any] = {"languageCode": language, "summary": text, "topicType": topic}
    if cta_type:
        cta = cta_type.upper()
        if cta not in CTA_TYPES:
            raise GbpError(f"cta_type harus salah satu dari {', '.join(CTA_TYPES)}.")
        if cta != "CALL" and not cta_url:
            raise GbpError(f"Tombol {cta} butuh cta_url.")
        post["callToAction"] = {"actionType": cta, **({"url": cta_url} if cta != "CALL" else {})}
    if image_url:
        if not image_url.startswith("https://"):
            raise GbpError("image_url harus link https publik ke file gambar (JPG/PNG).")
        post["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": image_url}]
    else:
        notes.append("Tanpa gambar. Postingan bergambar jauh lebih menarik di Maps.")
    if topic in ("OFFER", "EVENT"):
        if not (title and start and end):
            raise GbpError(f"Postingan {topic} butuh title, start, dan end (YYYY-MM-DD).")
        s, e = _date(start), _date(end)
        if date(**e) < date(**s):
            raise GbpError("Tanggal end lebih awal dari start.")
        if date(**e) < date.today():
            raise GbpError("Tanggal end sudah lewat.")
        post["event"] = {"title": title.strip(), "schedule": {"startDate": s, "endDate": e}}
    if topic == "OFFER" and (coupon or terms):
        post["offer"] = {**({"couponCode": coupon} if coupon else {}),
                         **({"termsConditions": terms} if terms else {})}
    return post, notes


# --- jam khusus (t107) -----------------------------------------------------------------------------------

def special_periods(dates: list[str], closed: bool = True, open_time: str | None = None,
                    close_time: str | None = None) -> list[dict]:
    if not dates:
        raise GbpError("Isi minimal satu tanggal (YYYY-MM-DD).")
    if not closed and not (open_time and close_time):
        raise GbpError("Jam khusus buka butuh open_time dan close_time (HH:MM), atau closed=true untuk tutup.")
    out = []
    for d in dates:
        day = _date(d)
        if date(**day) < date.today():
            raise GbpError(f"Tanggal {d} sudah lewat.")
        p: dict[str, Any] = {"startDate": day, "endDate": day}
        if closed:
            p["closed"] = True
        else:
            p.update(openTime=_time(open_time), closeTime=_time(close_time))
        out.append(p)
    return out


def _key(p: dict) -> tuple:
    d = p.get("startDate") or {}
    return d.get("year"), d.get("month"), d.get("day")


def merge_special_hours(existing: dict | None, new: list[dict]) -> dict:
    """PATCH specialHours MENGGANTI seluruh daftar, jadi jam khusus lama (tanggal lain) harus ikut dikirim.
    Tanggal yang sama ditimpa yang baru; tanggal yang sudah lewat dibuang."""
    today = date.today()
    new_keys = {_key(p) for p in new}
    keep = [p for p in (existing or {}).get("specialHourPeriods", [])
            if _key(p) not in new_keys and all(_key(p)) and date(*_key(p)) >= today]
    return {"specialHourPeriods": sorted(keep + new, key=_key)}


def describe_period(p: dict) -> str:
    y, m, d = _key(p)
    day = f"{d:02d}-{m:02d}-{y}"
    if p.get("closed"):
        return f"{day}: TUTUP"
    o, c = p.get("openTime", {}), p.get("closeTime", {})
    return f"{day}: {o.get('hours', 0):02d}:{o.get('minutes', 0):02d}–{c.get('hours', 0):02d}:{c.get('minutes', 0):02d}"


def fan_out(locations: list[dict], action: Callable[[dict], Any]) -> tuple[list[dict], list[dict]]:
    """Jalankan aksi per lokasi; satu cabang gagal tidak menghentikan cabang lain."""
    ok, failed = [], []
    for loc in locations:
        try:
            ok.append({"lokasi": loc.get("name"), "nama": loc.get("title"), "hasil": action(loc)})
        except GbpError as ex:
            failed.append({"lokasi": loc.get("name"), "nama": loc.get("title"), "error": str(ex)})
    return ok, failed
