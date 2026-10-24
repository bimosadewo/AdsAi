"""Alat diagnosis gratis (hanya baca): langkah berikutnya, cek kampanye benar-benar tayang, kegagalan terakhir.

Diadaptasi dari "diagnostic helper tools" Adspirer (start_here / suggest_next_action / verify_campaign_is_live /
why_did_this_fail). Pelajaran mereka: diagnosis yang salah lebih buruk daripada tidak ada — jadi setiap cek
menyebut datanya, dan bila data tidak bisa dibaca hasilnya "TIDAK_LENGKAP", bukan lolos.
"""

from __future__ import annotations

from typing import Any, Iterable

from .analysis import LEVEL_ORDER, Alert

# kode alert → (langkah, tool yang dipakai)
ACTIONS: dict[str, tuple[str, str]] = {
    "TIDAK_TAYANG": ("Cari tahu kenapa kampanye aktif tidak tayang (saldo/penagihan, iklan ditolak, budget) lalu "
                     "pulihkan.", "verify_campaign_is_live"),
    "IKLAN_DITOLAK": ("Perbaiki iklan yang ditolak: baca alasan kebijakan, tulis ulang teks, pratinjau.",
                      "campaign_structure → update_responsive_search_ad"),
    "KONVERSI_NOL": ("Konversi nol beberapa hari dengan biaya jalan: cek tracking dulu, baru search terms.",
                     "conversion_actions → search_terms"),
    "KONVERSI_UTAMA_BERLEBIH": ("Rapikan konversi Utama (maks ±3, buang aksi mikro) agar bidding belajar dari "
                                "prospek sungguhan.", "conversion_actions → update_conversion_action"),
    "IMPOR_GAGAL": ("Perbaiki impor konversi offline yang gagal.", "conversion_actions"),
    "CPA_NAIK": ("Biaya/konversi naik: cari search terms boros dan kampanye penyebabnya.",
                 "suggest_negative_keywords → add_negative_keywords"),
    "DI_ATAS_TARGET": ("Biaya/konversi di atas target klien: pangkas pemborosan, realokasi budget 20–30%.",
                       "suggest_negative_keywords, campaign_performance → update_budget"),
    "PACING_BOROS": ("Belanja diproyeksi melewati budget bulanan: turunkan budget harian.",
                     "budget_pacing → update_budget"),
    "PACING_KURANG": ("Belanja di bawah rencana: naikkan budget bertahap bila CPA sehat.",
                      "budget_pacing → update_budget"),
    "PENGATURAN_BERISIKO": ("Matikan Search Partners/Display/AI Max yang boros dan pakai lokasi 'kehadiran'.",
                            "campaign_settings → update_campaign_settings"),
}
ANOMALY = ("Ada lonjakan/penurunan harian tak wajar: bandingkan dengan change_log dan search terms hari itu.",
           "detect_anomalies → change_log")

# langkah rutin per jenis akun (lihat dossier.classify_account) bila tidak ada alert
ROUTINE: dict[str, list[tuple[str, str]]] = {
    "MACET": [("Diagnosis tayang & tracking sebelum mengubah apa pun.", "verify_campaign_is_live, conversion_actions")],
    "BARU": [("Pangkas search terms boros (data tipis: hanya EXACT).", "suggest_negative_keywords"),
             ("Lengkapi ekstensi untuk menaikkan CTR.", "campaign_structure → add_sitelinks / add_callouts")],
    "BERTUMBUH": [("Naikkan budget kampanye terbaik +20–30% selama CPA ≤ target.", "budget_pacing → update_budget"),
                  ("Jadikan pencarian pemenang kata kunci EXACT.", "search_terms → add_keywords")],
    "STABIL": [("Pangkas pemborosan mingguan.", "suggest_negative_keywords → add_negative_keywords"),
               ("Uji 1 RSA baru di grup iklan terbesar.", "ad_performance → create_responsive_search_ad")],
}


def next_actions(alerts: Iterable[Alert], kind: str, drift: list[dict[str, Any]] | None = None,
                 limit: int = 5) -> list[dict[str, Any]]:
    """Urutkan langkah: alert KRITIS → penyimpangan strategi → PERINGATAN → INFO → rutin jenis akun."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(priority: str, why: str, step: tuple[str, str]) -> None:
        if step[0] in seen:
            return
        seen.add(step[0])
        out.append({"prioritas": priority, "alasan": why, "langkah": step[0], "tool": step[1]})

    ordered = sorted(alerts, key=lambda a: LEVEL_ORDER.get(a.level, 9))
    for a in ordered:
        if a.level == "KRITIS":
            add(a.level, a.pesan, ACTIONS.get(a.kode) or (ANOMALY if a.kode.startswith("ANOMALI_") else
                                                          ("Tangani temuan ini.", "client_health")))
    if drift:
        cost = sum(d["biaya"] for d in drift)
        add("PERINGATAN", f"{len(drift)} search term melanggar arahan AVOID di strategi klien "
                          f"(biaya Rp{cost:,.0f})".replace(",", "."),
            ("Negatifkan pencarian yang melanggar arahan strategi.", "client_strategy → add_negative_keywords"))
    for a in ordered:
        if a.level != "KRITIS":
            add(a.level, a.pesan, ACTIONS.get(a.kode) or (ANOMALY if a.kode.startswith("ANOMALI_") else
                                                          ("Tangani temuan ini.", "client_health")))
    for step in ROUTINE.get(kind, []):
        add("RUTIN", f"akun berjenis {kind}", step)
    return out[:limit]


# ---------------------------------------------------------------------------
# verify_campaign_is_live
# ---------------------------------------------------------------------------

LIVE_PRIMARY = {"ELIGIBLE", "LIMITED", "LEARNING"}


def live_queries(campaign_id: str) -> dict[str, str]:
    c = f"campaign.id = {campaign_id}"
    return {
        "kampanye": ("SELECT campaign.id, campaign.name, campaign.status, campaign.serving_status, "
                     "campaign.primary_status, campaign.primary_status_reasons, campaign.advertising_channel_type, "
                     f"campaign_budget.amount_micros FROM campaign WHERE {c}"),
        "grup": f"SELECT ad_group.id FROM ad_group WHERE {c} AND ad_group.status = 'ENABLED'",
        "iklan": ("SELECT ad_group_ad.ad.id, ad_group_ad.policy_summary.approval_status FROM ad_group_ad "
                  f"WHERE {c} AND ad_group_ad.status = 'ENABLED' AND ad_group.status = 'ENABLED'"),
        "kata_kunci": ("SELECT ad_group_criterion.criterion_id FROM ad_group_criterion "
                       f"WHERE {c} AND ad_group_criterion.status = 'ENABLED' AND ad_group.status = 'ENABLED' "
                       "AND ad_group_criterion.type = 'KEYWORD' AND ad_group_criterion.negative = FALSE LIMIT 1"),
        "kemarin": f"SELECT metrics.impressions, metrics.cost_micros FROM campaign WHERE {c} AND segments.date DURING YESTERDAY",
        "grup_aset": ("SELECT asset_group.id, asset_group.name, asset_group.status, asset_group.primary_status, "
                      f"asset_group.ad_strength FROM asset_group WHERE {c} AND asset_group.status = 'ENABLED'"),
    }


def assess_live(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Murni: nilai hasil live_queries(). Mengembalikan daftar cek + vonis TAYANG / TIDAK_TAYANG."""
    rows = data.get("kampanye") or []
    if not rows:
        return {"vonis": "TIDAK_DITEMUKAN", "cek": [], "saran": ["Kampanye tidak ditemukan; cek ID dengan list_campaigns."]}
    c = rows[0]
    search = c.get("campaign.advertising_channel_type") == "SEARCH"
    ads = data.get("iklan") or []
    approved = [a for a in ads if a.get("ad_group_ad.policy_summary.approval_status") != "DISAPPROVED"]
    primary = c.get("campaign.primary_status")
    reasons = c.get("campaign.primary_status_reasons") or []
    budget = float(c.get("campaign_budget.amount") or 0)
    checks = [
        ("Status kampanye ENABLED", c.get("campaign.status") == "ENABLED", c.get("campaign.status"),
         "Aktifkan dengan set_status (level campaign) setelah disetujui."),
        ("Budget harian > 0", budget > 0, budget, "Isi budget dengan update_budget."),
        ("Status utama Google layak tayang", primary in LIVE_PRIMARY,
         f"{primary}" + (f" ({', '.join(reasons)})" if reasons else ""),
         "Baca alasan status utama; tersering: penagihan/saldo, iklan ditolak, atau budget terbatas."),
    ]
    pmax = c.get("campaign.advertising_channel_type") == "PERFORMANCE_MAX"
    groups = data.get("grup_aset") or []
    if pmax:   # PMax tidak punya grup iklan/iklan biasa — yang dinilai grup aset
        checks.append(("Ada grup aset aktif", bool(groups), len(groups),
                       "Aktifkan grup aset (UI: Kampanye → Grup aset) — tanpa itu PMax tidak tayang."))
    else:
        checks += [
            ("Ada grup iklan aktif", bool(data.get("grup")), len(data.get("grup") or []),
             "Aktifkan minimal satu grup iklan."),
            ("Ada iklan aktif yang tidak ditolak", bool(approved), f"{len(approved)} dari {len(ads)} iklan aktif",
             "Perbaiki/aktifkan iklan; lihat campaign_structure untuk alasan penolakan."),
        ]
    if search:
        checks.append(("Ada kata kunci aktif", bool(data.get("kata_kunci")), len(data.get("kata_kunci") or []),
                       "Tambahkan kata kunci dengan add_keywords."))
    y = (data.get("kemarin") or [{}])[0]
    impressions = int(y.get("metrics.impressions") or 0)
    result_checks = [{"cek": n, "lolos": ok, "nilai": v} for n, ok, v, _ in checks]
    failed = [fix for _, ok, _, fix in checks if not ok]
    verdict = "TAYANG" if not failed else "TIDAK_TAYANG"
    out = {"kampanye": c.get("campaign.name"), "id": c.get("campaign.id"), "vonis": verdict, "cek": result_checks,
           "kemarin": {"tayangan": impressions, "biaya": float(y.get("metrics.cost") or 0)}}
    weak = [g.get("asset_group.name") for g in groups if g.get("asset_group.ad_strength") in ("POOR", "AVERAGE")]
    if pmax and weak:
        out["peringatan"] = [f"Kekuatan iklan grup aset lemah ({', '.join(str(w) for w in weak)}): tambah judul, "
                             "deskripsi, gambar (persegi & lanskap), logo, dan video agar jangkauan tidak dibatasi."]
    if verdict == "TAYANG" and impressions == 0:
        out["catatan"] = ("Semua syarat terpenuhi tetapi kemarin 0 tayangan — wajar untuk kampanye yang baru "
                          "diaktifkan/disetujui; bila sudah >2 hari, cek bid, kata kunci, dan penargetan lokasi.")
    if failed:
        out["saran"] = failed
    return out


def why_failed(entries: list[dict[str, Any]]) -> dict[str, Any]:
    from .errors import hints_for
    if not entries:
        return {"ringkasan": "Tidak ada kegagalan tercatat.", "kegagalan": []}
    items = []
    for e in entries:
        hints = hints_for(e.get("kode") or [])
        items.append({**e, "saran": hints or None})
    auth = [e for e in entries if "OAuth" in e["pesan"] or "refresh" in e["pesan"].lower()
            or any(k in (e.get("kode") or []) for k in ("USER_PERMISSION_DENIED", "OAUTH_TOKEN_EXPIRED",
                                                       "OAUTH_TOKEN_REVOKED", "NOT_ADS_USER"))]
    summary = f"{len(entries)} kegagalan terakhir; terbaru: {entries[0]['tool']} ({entries[0]['waktu']})."
    if auth:
        summary += (" Sebagian adalah masalah LOGIN Google Ads (kredensial di .env), bukan koneksi Claude ke AdsAi —"
                    " jangan sambung ulang konektor; perbaiki kredensial.")
    return {"ringkasan": summary, "kegagalan": items}
