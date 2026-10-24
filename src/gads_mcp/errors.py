"""Penerjemah error Google Ads → sebab & langkah perbaikan dalam bahasa awam.

Pelajaran dari changelog Adspirer: error mentah ("OPERATION_NOT_PERMITTED_FOR_CONTEXT") membuat agen menebak
dan mengulang dengan argumen asal. Setiap error kini membawa `saran` yang bisa langsung dijalankan, dan error
sementara (CONCURRENT_MODIFICATION, kuota sesaat) dicoba ulang otomatis — aman karena mutate Google atomik:
yang gagal tidak mengubah apa pun.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Kode yang aman dicoba ulang (permintaan tidak diterapkan sama sekali).
RETRYABLE = {"CONCURRENT_MODIFICATION", "RESOURCE_TEMPORARILY_EXHAUSTED", "TRANSIENT_ERROR", "INTERNAL_ERROR",
             "DEADLINE_EXCEEDED"}

HINTS: dict[str, str] = {
    # akses & akun
    "USER_PERMISSION_DENIED": "Login OAuth tidak punya akses ke akun ini. Pastikan akun tertaut di bawah MCC dan "
                              "GOOGLE_ADS_LOGIN_CUSTOMER_ID berisi ID MCC (tanpa tanda hubung).",
    "CUSTOMER_NOT_ENABLED": "Akun Google Ads belum aktif / dibatalkan (biasanya belum ada penagihan). Aktifkan di UI.",
    "CUSTOMER_NOT_FOUND": "Customer ID tidak ditemukan. Panggil list_accounts / list_clients untuk ID yang benar.",
    "DEVELOPER_TOKEN_NOT_APPROVED": "Level akses API project belum cukup (Test/Explorer). Keyword Planner "
                                    "(research_keywords) butuh Basic Access: selesaikan verifikasi merek lalu ajukan di "
                                    "Cloud Console → Google Ads API → Overview (docs/FASE-0-AKSES.md). Sementara itu: "
                                    "location_performance untuk data per daerah, atau Keyword Planner di UI Google Ads.",
    "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION": "Project Google Cloud belum disetujui untuk akun sungguhan. Selesaikan "
                                                 "verifikasi merek lalu ajukan Basic Access di Cloud Console → Google "
                                                 "Ads API → Overview → Upgrade access level (docs/FASE-0-AKSES.md).",
    "CLOUD_PROJECT_NOT_FOUND": "Project Google Cloud pemilik OAuth client tidak ditemukan/nonaktif. Cek project di "
                               "Cloud Console dan buat ulang refresh token dengan OAuth client project itu.",
    "DEVELOPER_TOKEN_PROHIBITED": "Developer token tidak boleh dipakai dengan project Google Cloud ini.",
    "NOT_ADS_USER": "Akun Google yang login tidak terhubung ke akun Google Ads mana pun.",
    "TWO_STEP_VERIFICATION_NOT_ENROLLED": "Akun Google yang login wajib mengaktifkan verifikasi 2 langkah.",
    "OAUTH_TOKEN_EXPIRED": "Token OAuth kedaluwarsa. Buat ulang refresh token (scripts/generate_refresh_token.py).",
    "OAUTH_TOKEN_REVOKED": "Refresh token dicabut. Buat ulang dengan scripts/generate_refresh_token.py.",
    # kuota
    "RESOURCE_EXHAUSTED": "Kuota API habis/terlalu cepat. Tunggu beberapa menit; kurangi query besar beruntun.",
    "RESOURCE_TEMPORARILY_EXHAUSTED": "Kuota sesaat penuh — sudah dicoba ulang otomatis. Coba lagi 1–2 menit lagi.",
    "CONCURRENT_MODIFICATION": "Objek yang sama sedang diubah proses lain — sudah dicoba ulang otomatis. "
                               "Tunggu sebentar lalu ulangi pratinjau.",
    # bidding & kampanye
    "OPERATION_NOT_PERMITTED_FOR_CONTEXT": "Operasi tidak berlaku untuk kampanye ini. Penyebab tersering: kampanye "
                                           "memakai strategi bidding portofolio (bersama) — lepaskan kampanye dari "
                                           "portofolio di UI, atau ubah strategi portofolionya.",
    "BIDDING_STRATEGY_NOT_SUPPORTED": "Strategi bidding tidak didukung jenis kampanye ini (cek advertising_channel_type).",
    "FIELD_TYPE_INCOMPATIBLE_WITH_CAMPAIGN_TYPE": "Pengaturan ini tidak cocok dengan jenis kampanye (mis. jaringan Display di PMax).",
    "BUDGET_TOO_SMALL": "Budget di bawah minimum Google untuk mata uang akun. Untuk berhenti, jeda dengan set_status.",
    "BUDGET_AMOUNT_TOO_SMALL": "Budget di bawah minimum Google untuk mata uang akun.",
    "BID_TOO_SMALL": "Bid CPC di bawah minimum untuk mata uang akun.",
    "BIDDING_STRATEGY_TRANSITION_NOT_ALLOWED": "Peralihan strategi bidding ini tidak diizinkan langsung — pindah ke "
                                               "MAXIMIZE_CONVERSIONS dulu, atau ubah di UI.",
    "IMMUTABLE_FIELD": "Field ini tidak bisa diubah lewat API setelah dibuat. Ubah di UI Google Ads, atau buat baru.",
    "FIELD_HAS_SUBFIELDS": "Field mask salah (field induk). Laporkan sebagai bug AdsAi beserta tool & argumennya.",
    # kata kunci & iklan
    "INVALID_KEYWORD_MATCH_TYPE": "Jenis pencocokan tidak diterima. Kampanye AI Max/broad-only menolak EXACT/PHRASE — "
                          "cek campaign_settings, pakai BROAD atau matikan AI Max.",
    "KEYWORD_HAS_INVALID_CHARS": "Kata kunci berisi karakter terlarang (mis. ! @ % , *). Hapus karakter tersebut.",
    "TOO_MANY_WORDS": "Kata kunci maksimal 10 kata.",
    "POLICY_FINDING": "Teks melanggar kebijakan iklan (merek dagang, klaim, kesehatan, dll). Lihat detail kebijakan "
                      "di pesan; ubah teks, atau ajukan pengecualian di UI bila sah.",
    "DUPLICATE_ASSET": "Aset dengan teks yang sama sudah ada. Pakai list_extensions untuk melihat/menautkannya.",
    "TOO_LONG": "Teks melebihi batas karakter. Jangan dipotong otomatis — tulis ulang lebih pendek "
                "(headline 30, deskripsi 90, sitelink/callout 25, deskripsi sitelink 35, path 15).",
    "DUPLICATE_ELEMENT": "Elemen kembar dalam permintaan. Hapus duplikatnya.",
    "INVALID_DESTINATION_URL": "URL tujuan tidak valid. Pakai URL lengkap https:// yang bisa dibuka.",
    "INVALID_DOMAIN_NAME": "URL tujuan tidak valid. Pakai URL lengkap https:// yang bisa dibuka.",
    # konversi & penargetan
    "MUTATE_NOT_ALLOWED": "Objek ini tidak bisa diubah lewat API — biasanya konversi buatan Google (GOOGLE_HOSTED: "
                          "panggilan/local actions/YouTube) atau konversi dari URL di UI (WEBPAGE_CODELESS, mis. "
                          "'Page load …'). Ubah di UI: Sasaran → Konversi → klik nama → Edit setelan.",
    "INVALID_GEO_TARGET_CONSTANT": "ID lokasi tidak valid. Pakai resolve_locations untuk mencari ID yang benar.",
}

_CODE = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")


def codes_of(error_code: Any) -> list[str]:
    """Ambil nama enum dari ErrorCode (oneof), mis. 'authorization_error: USER_PERMISSION_DENIED'."""
    return [c for c in _CODE.findall(str(error_code)) if c not in ("UNSPECIFIED", "UNKNOWN")]


def hints_for(codes: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for c in codes:
        h = HINTS.get(c)
        if h and h not in seen:
            seen.append(h)
    return seen


def is_retryable(codes: Iterable[str]) -> bool:
    codes = list(codes)
    return bool(codes) and all(c in RETRYABLE for c in codes)


def explain(text: str) -> dict[str, Any]:
    """Untuk tool `explain_error`: terjemahkan pesan error mentah (salinan dari UI/log)."""
    codes = sorted(set(_CODE.findall(text)))
    hints = hints_for(codes)
    return {
        "kode_dikenali": [c for c in codes if c in HINTS],
        "saran": hints or ["Kode tidak dikenali. Baca pesan Google apa adanya; jangan mengulang dengan argumen "
                           "tebakan. Bila berulang, catat request_id untuk dukungan Google Ads API."],
    }
