"""Kit dossier per klien: konteks akun yang dikemas untuk agen AI (Claude, ChatGPT, Claude Code, Cursor).

Terinspirasi "Expert Space" Adspirer (kit berbayar per akun). Versi AdsAi dibuat otomatis dari data akun,
gratis, bisa diperbarui kapan saja (`adsai dossier` / tool `client_dossier`), dan berbahasa Indonesia.

Isi folder dossiers/<klien>/ (format Agent Skills — hanya references/ yang bersarang, dimuat sesuai kebutuhan):
  SKILL.md                         doktrin kerja yang dipatok ke akun ini (frontmatter name/description)
  AGENTS.md                        satu-satunya berkas instruksi: root proyek agen coding / kolom instruksi chat
  PROMPTBOOK.md                    menu prompt per level — dipilih satu, bukan dijalankan semua
  README.md                        ke mana setiap berkas dipasang
  references/account-profile.md    dossier: kondisi akun, jenis akun, prioritas, ritme kerja, angka
  references/working-with-adsai.md kontrak pemanggilan tool AdsAi dan pagar pengaman

Kontak klien (WA/email) sengaja TIDAK dimasukkan.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from . import gaql, strategy
from .analysis import Alert, estimated_savings, evaluate_alerts, suggest_negatives
from .collect import collect_snapshot, today_local
from .fmt import number, rupiah

FILES = ("SKILL.md", "AGENTS.md", "PROMPTBOOK.md", "README.md",
         "references/account-profile.md", "references/working-with-adsai.md")
OBSOLETE = ("AGENT_INSTRUCTIONS.md",)   # kit lama; isinya kini ada di AGENTS.md


class Searcher(Protocol):
    def search(self, customer_id: str, query: str) -> list[dict[str, Any]]: ...


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "klien"


# ---------------------------------------------------------------------------
# Pengumpulan data
# ---------------------------------------------------------------------------

def period(today: date, days: int = 90) -> tuple[str, str, str]:
    """(start, end, klausa GAQL) untuk N hari sampai kemarin."""
    end = (today - timedelta(days=1)).isoformat()
    start = (today - timedelta(days=days)).isoformat()
    return start, end, f"segments.date BETWEEN '{start}' AND '{end}'"


def fetch_data(ads: Searcher, client: Any, today: date | None = None, days: int = 90) -> dict[str, Any]:
    today = today or today_local()
    start, end, period_clause = period(today, days)
    cid = client.customer_id
    return {
        "today": today,
        "periode": (start, end),
        "snapshot": collect_snapshot(ads, client, today=today),
        "kampanye_periode": ads.search(cid, gaql.q_campaign_performance(period_clause)),
        "kampanye_aktif": ads.search(cid, (
            "SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.bidding_strategy_type, "
            "campaign_budget.amount_micros, campaign.network_settings.target_search_network, "
            "campaign.network_settings.target_content_network, campaign.ai_max_setting.enable_ai_max, "
            "campaign.geo_target_type_setting.positive_geo_target_type FROM campaign "
            "WHERE campaign.status = 'ENABLED' ORDER BY campaign.name")),
        "konversi": ads.search(cid, gaql.q_conversion_actions()),
        "istilah_periode": ads.search(cid, f"{gaql.q_search_terms(period_clause)} LIMIT 1000"),
    }


# ---------------------------------------------------------------------------
# Penyusun berkas (murni)
# ---------------------------------------------------------------------------

def _yes(v: Any) -> str:
    return "ya" if v else "tidak"


def _campaign_table(active: list[dict], perf: list[dict]) -> list[str]:
    by_id = {p.get("campaign.id"): p for p in perf}
    lines = ["| Kampanye | ID | Jenis | Bidding | Budget/hari | Biaya periode | Konversi | Biaya/konv. | Catatan |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in active:
        p = by_id.get(c.get("campaign.id"), {})
        cost = float(p.get("metrics.cost") or 0)
        conv = float(p.get("metrics.conversions") or 0)
        flags = []
        if c.get("campaign.network_settings.target_search_network"):
            flags.append("Search Partners ON")
        if c.get("campaign.network_settings.target_content_network"):
            flags.append("Display ON")
        if c.get("campaign.ai_max_setting.enable_ai_max"):
            flags.append("AI Max ON")
        if c.get("campaign.geo_target_type_setting.positive_geo_target_type") == "PRESENCE_OR_INTEREST":
            flags.append("lokasi: minat")
        lines.append(
            f"| {c.get('campaign.name')} | {c.get('campaign.id')} | {c.get('campaign.advertising_channel_type')} | "
            f"{c.get('campaign.bidding_strategy_type')} | {rupiah(c.get('campaign_budget.amount'))} | {rupiah(cost)} | "
            f"{number(conv, 1)} | {rupiah(cost / conv) if conv else '-'} | {', '.join(flags) or '-'} |"
        )
    if len(lines) == 2:
        lines.append("| (tidak ada kampanye aktif) | | | | | | | | |")
    return lines


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or not old:
        return None
    return (new - old) / old * 100


def classify_account(snap: dict[str, Any], total: dict[str, Any]) -> dict[str, Any]:
    """Jenis akun → prioritas & ritme kerja. Urutan: tidak tayang → data tipis → turun → tumbuh → stabil.

    Jenis: MACET (berhenti/memburuk), BARU (data konversi belum cukup untuk Smart Bidding),
    BERTUMBUH (konversi naik tanpa CPA memburuk), STABIL (sisanya). Musiman tidak bisa dibaca dari 90 hari
    — tim menandainya sendiri di catatan strategi klien.
    """
    week, prev = snap["minggu_ini"], snap["minggu_lalu"]
    conv_chg = _pct(week["konversi"], prev["konversi"])
    cpa_chg = _pct(week["biaya_per_konversi"], prev["biaya_per_konversi"])
    not_serving = snap.get("kampanye_aktif_tidak_tayang") or []
    reasons: list[str] = []
    if not_serving or (week["biaya"] == 0 and prev["biaya"] > 0):
        kind = "MACET"
        reasons.append(f"{len(not_serving)} kampanye aktif tidak tayang" if not_serving else "biaya 7 hari terakhir nol")
    elif total["konversi"] < 30:
        kind = "BARU"
        reasons.append(f"hanya {number(total['konversi'], 1)} konversi dalam 90 hari (<30: Smart Bidding belum stabil)")
    elif (conv_chg is not None and conv_chg <= -20) or (cpa_chg is not None and cpa_chg >= 20):
        kind = "MACET"
        if conv_chg is not None and conv_chg <= -20:
            reasons.append(f"konversi minggu ini {number(conv_chg, 0)}% dibanding minggu lalu")
        if cpa_chg is not None and cpa_chg >= 20:
            reasons.append(f"biaya/konversi naik {number(cpa_chg, 0)}%")
    elif conv_chg is not None and conv_chg >= 15 and (cpa_chg is None or cpa_chg <= 10):
        kind = "BERTUMBUH"
        reasons.append(f"konversi +{number(conv_chg, 0)}% minggu ini, biaya/konversi terjaga")
    else:
        kind = "STABIL"
        reasons.append("tidak ada perubahan besar minggu ke minggu")
    plan = {
        "MACET": (["Diagnosis dulu sebelum mengubah apa pun: tayang? (saldo, penolakan, budget) → tracking konversi "
                   "→ search terms → pengaturan berisiko.",
                   "Satu perbaikan per siklus, ukur 7 hari, baru langkah berikutnya."],
                  "Cek harian sampai pulih; perubahan besar paling cepat 7 hari sekali."),
        "BARU": (["Pastikan tracking benar dulu (konversi Utama ≤ 3, tanpa aksi mikro).",
                  "Jangan ganti strategi bidding; biarkan fase belajar ±2 minggu.",
                  "Pangkas search terms boros 2× seminggu; tambah sitelink/callout agar CTR naik."],
                 "Review 2× seminggu; ubah bidding/budget besar paling cepat 14 hari."),
        "BERTUMBUH": (["Skalakan bertahap: budget +20–30% per minggu selama biaya/konversi ≤ target.",
                       "Lindungi pencarian pemenang (jangan dinegatifkan), tambahkan sebagai kata kunci EXACT.",
                       "Pantau pacing agar tidak kehabisan budget bulanan."],
                      "Review mingguan; naik budget paling sering 1× seminggu."),
        "STABIL": (["Pangkas pemborosan (negatif, Search Partners, jadwal) dan uji 1 RSA baru per grup iklan.",
                    "Realokasi 20–30% budget dari kampanye dengan biaya/konversi tertinggi ke yang terendah."],
                   "Review mingguan; laporan ke klien setiap Senin."),
    }[kind]
    return {"jenis": kind, "alasan": reasons, "prioritas": plan[0], "ritme": plan[1],
            "perubahan_konversi_pct": conv_chg, "perubahan_cpa_pct": cpa_chg}


TOOL_MAP = [
    ("Kondisi akun", "client_health, account_alerts, campaign_performance, budget_pacing, detect_anomalies"),
    ("Tracking", "conversion_actions → update_conversion_action"),
    ("Pemborosan", "search_terms, ngram_analysis, suggest_negative_keywords → add_negative_keywords / shared list"),
    ("Struktur & iklan", "campaign_structure, ad_performance, validate_ad_copy → create/update_responsive_search_ad"),
    ("Ekstensi", "list_extensions → add_sitelinks/add_callouts/add_structured_snippets/add_call_extension"),
    ("Pengaturan & target", "campaign_settings, resolve_locations → update_campaign_settings, add_location_targets, "
     "add_radius_target, set_ad_schedule, set_device_bid_adjustments"),
    ("Budget & bidding", "update_budget, update_bid_strategy"),
    ("Riwayat & laporan", "change_log, weekly_report → send_to_client"),
    ("Diagnosis", "suggest_next_action, verify_campaign_is_live, why_did_this_fail, explain_error"),
    ("Strategi klien", "client_strategy → add_strategy_directive (hanya setelah keputusan eksplisit)"),
]


def build_kit(client: Any, data: dict[str, Any]) -> dict[str, str]:
    snap = data["snapshot"]
    start, end = data["periode"]
    today = data["today"].isoformat()
    key = slug(client.key)
    total = gaql.summarize(data["kampanye_periode"])
    alerts: list[Alert] = evaluate_alerts(snap, client)
    kind = classify_account(snap, total)
    primary = [c for c in data["konversi"] if c.get("conversion_action.primary_for_goal")]
    terms = data["istilah_periode"]
    winners = sorted((t for t in terms if float(t.get("metrics.conversions") or 0) > 0),
                     key=lambda t: -float(t.get("metrics.conversions") or 0))[:12]
    directives = data.get("strategi") or []
    negatives = suggest_negatives(terms, client.thresholds.min_waste_cost,
                                  tuple(client.protected_terms) + tuple(strategy.protected_terms(directives)),
                                  snap.get("negatif_terpasang", []))[:10]
    drift = strategy.drift(terms, directives)[:10]
    active = data["kampanye_aktif"]
    first_campaign = active[0].get("campaign.name") if active else "kampanye utama"
    products = ", ".join(client.protected_terms) or "(belum diisi — tambahkan protected_terms di clients.yaml)"
    target = rupiah(client.target_cpa) if client.target_cpa else "belum ditetapkan"
    budget = rupiah(client.monthly_budget) if client.monthly_budget else "belum ditetapkan"

    rules = [
        "Uang butuh persetujuan: setiap perubahan lewat pratinjau (`confirm=false`) dulu, eksekusi `confirm=true` "
        "hanya setelah manusia setuju di percakapan, lalu laporkan isi `terverifikasi`.",
        f"Jangan pernah memblokir kata produk ({products}) atau kata niat beli (harga, jual, distributor, terdekat) "
        "sebagai negatif PHRASE/BROAD.",
        "Cek `conversion_actions` sebelum menilai CPA; bila konversi Utama > 3, katakan angka belum bisa dipercaya.",
        "Budget naik bertahap (maks 2× sekali ubah, dijaga AdsAi); realokasi 20–30% lalu tunggu seminggu.",
        "Ganti strategi bidding/konversi paling sering sekali per 2 minggu per kampanye (fase belajar).",
        "Jeda, jangan hapus: iklan & kampanye baru dibuat dijeda; aktifkan hanya setelah disetujui.",
        "Jangan mengarang angka. Bila tool gagal, sebutkan tool dan error-nya.",
    ]

    # ---------------- SKILL.md (doktrin; ringkas, selalu dimuat) ----------------
    s: list[str] = [
        "---",
        f"name: adsai-{key}",
        f"description: Doktrin kerja akun Google Ads {client.name} (customer ID {client.customer_id}) — jenis akun, "
        "prioritas, target, kata yang dilindungi, dan aturan main. Pakai untuk SEMUA pertanyaan atau perubahan tentang "
        f"iklan Google {client.name}.",
        "---",
        "",
        f"# {client.name} — doktrin Google Ads",
        "",
        f"Dibuat {today} oleh AdsAi dari data {start} s.d. {end}. Perbarui dengan `adsai dossier --client {client.key}`"
        " atau tool `client_dossier`. Angka di kit ini potret saat dibuat — untuk angka terbaru selalu panggil tool.",
        "",
        "## Akun",
        f"- Customer ID **{client.customer_id}** — selalu kirim `customer_id=\"{client.customer_id}\"` ke tool AdsAi.",
        "- Mata uang Rupiah (nilai tool dalam rupiah, mis. 50000 = Rp50.000).",
        f"- Target biaya/konversi: **{target}** · budget iklan bulanan: **{budget}**.",
        f"- Kata yang dilindungi: {products}",
        "",
        f"## Jenis akun: {kind['jenis']}",
        f"Alasan: {'; '.join(kind['alasan'])}.",
        "",
        "Prioritas:",
        *[f"{i}. {x}" for i, x in enumerate(kind["prioritas"], 1)],
        "",
        f"Ritme: {kind['ritme']}",
        "",
        "## Arahan strategi klien",
        *([f"- [{d.bagian}] {d.line()[2:]}" for d in directives]
          or ["- Belum ada. Simpan keputusan yang disepakati dengan `add_strategy_directive` (setelah pengguna setuju)."]),
        "",
        "Patuhi arahan ini. Bila data baru bertentangan, sampaikan dan tanyakan — jangan diam-diam mengabaikannya.",
        "",
        "## Aturan main",
        *[f"{i}. {r}" for i, r in enumerate(rules, 1)],
        "",
        "## Rujukan (buka saat dibutuhkan)",
        "- `references/account-profile.md` — kondisi akun lengkap: angka, kampanye, tracking, search terms, temuan.",
        "- `references/working-with-adsai.md` — cara memanggil tool AdsAi dengan aman.",
        "- `PROMPTBOOK.md` — menu langkah berikutnya; pilih SATU yang paling relevan, jangan jalankan semuanya.",
    ]

    # ---------------- references/account-profile.md (data) ----------------
    f: list[str] = [
        f"# Profil akun — {client.name}",
        "",
        f"Potret {today} (data {start} s.d. {end}).",
        "",
        f"## Kondisi: {kind['jenis']}",
        *[f"- {r}" for r in kind["alasan"]],
        f"- Ritme kerja: {kind['ritme']}",
        "",
        f"## Ringkasan {start} s.d. {end}",
        f"- Biaya {rupiah(total['biaya'])} · klik {number(total['klik'])} · konversi {number(total['konversi'], 1)} · "
        f"biaya/konversi {rupiah(total['biaya_per_konversi'])} · CPC {rupiah(total['cpc_rata2'])}",
        f"- 7 hari terakhir: biaya {rupiah(snap['minggu_ini']['biaya'])}, konversi {number(snap['minggu_ini']['konversi'], 1)}, "
        f"biaya/konversi {rupiah(snap['minggu_ini']['biaya_per_konversi'])}",
        "",
        "## Kampanye aktif",
        *_campaign_table(active, data["kampanye_periode"]),
        "",
        "## Tracking konversi",
        f"- {len(primary)} konversi berstatus **Utama** (dipakai bidding):"
        + (" ⚠️ lebih dari 3 — angka konversi kemungkinan menggelembung." if len(primary) > 3 else ""),
        *[f"  - {c.get('conversion_action.name')} ({c.get('conversion_action.category')}, "
          f"{c.get('conversion_action.counting_type')})" for c in primary],
        "",
        "## Pola search terms",
        "Pencarian yang menghasilkan konversi (pakai kata-katanya di iklan):",
        *([f"- \"{t.get('search_term_view.search_term')}\" — {number(float(t.get('metrics.conversions') or 0), 1)} konv., "
           f"{rupiah(float(t.get('metrics.cost') or 0))}" for t in winners] or ["- (belum ada data)"]),
        "",
        f"Kandidat negatif (biaya tanpa konversi; kata produk & niat beli sudah disaring) — potensi hemat "
        f"±{rupiah(estimated_savings(terms, negatives))} pada periode ini:",
        *([f"- {n['match_type']} \"{n['text']}\" — {rupiah(n['biaya'])} ({n['alasan']})" for n in negatives]
          or ["- (tidak ada di atas ambang)"]),
        "",
        "## Temuan terkini",
        *([f"- [{a.level}] {a.pesan}" for a in alerts] or ["- Tidak ada masalah terdeteksi."]),
    ]
    if drift:
        f += ["", "## Penyimpangan strategi (search term melanggar arahan AVOID)",
              *[f"- \"{d['search_term']}\" — {rupiah(d['biaya'])} (melanggar: {d['melanggar']})" for d in drift]]

    # ---------------- references/working-with-adsai.md (kontrak tool) ----------------
    w: list[str] = [
        "# Bekerja dengan AdsAi",
        "",
        "## Siklus wajib",
        "1. **Baca sebelum menulis** — panggil tool baca yang relevan dulu; jangan menebak ID kampanye/grup/aset, "
        "ambil dari hasil tool.",
        "2. **Pratinjau** — tool ubah dipanggil `confirm=false` (validasi Google tanpa mengubah apa pun). "
        "Tunjukkan rencananya ke manusia.",
        "3. **Eksekusi** — `confirm=true` hanya setelah persetujuan eksplisit di percakapan.",
        "4. **Verifikasi** — laporkan isi `terverifikasi` dari hasil; bila `false`, katakan dan cek ulang.",
        "",
        "## Pagar pengaman (dijaga server)",
        "- Mode baca-saja, batas budget harian, kenaikan budget maks 2× per perubahan, budget bersama ditolak.",
        "- Tidak bisa menghapus semua lokasi atau mematikan semua perangkat sekaligus.",
        "- Jeda, jangan hapus: tidak ada tool hapus kampanye; RSA baru dibuat dijeda.",
        "- Semua perubahan tercatat di log audit (`change_log`) dan muncul di laporan mingguan klien.",
        "",
        "## Peta tool",
        "| Kebutuhan | Tool |",
        "|---|---|",
        *[f"| {a} | {b} |" for a, b in TOOL_MAP],
        "",
        "Bila tool gagal: baca `saran` di pesan error atau panggil `why_did_this_fail`; sebutkan nama tool dan "
        "error-nya apa adanya; jangan mengulang dengan argumen tebakan.",
        "Setelah timeout: baca kondisi terkini dulu (perubahan mungkin sudah terjadi) sebelum mencoba ulang.",
    ]

    # ---------------- PROMPTBOOK.md (menu) ----------------
    p: list[str] = [
        f"# Prompt book — {client.name}",
        "",
        "**Menu, bukan naskah.** Pilih SATU prompt yang paling cocok dengan kebutuhan saat ini dan jalankan itu saja — "
        "setiap prompt memakai panggilan tool sungguhan. Agen: buka berkas ini untuk mengusulkan langkah berikutnya, "
        "jangan mengerjakannya dari atas ke bawah. Semua perubahan tetap lewat pratinjau.",
        "",
        f"Mulai dari sini untuk akun **{kind['jenis']}**: "
        + {"MACET": "prompt 1 dan 3.", "BARU": "prompt 3 dan 5.", "BERTUMBUH": "prompt 8 dan 11.",
           "STABIL": "prompt 5 dan 6."}[kind["jenis"]],
        "",
        "## Pemula — memahami kondisi akun",
        f"1. \"Cek kesehatan akun {client.name} dengan client_health, jelaskan dalam 5 poin bahasa awam.\"",
        f"2. \"Bandingkan performa 7 hari terakhir dengan minggu sebelumnya untuk {client.name}; mulai dari kesimpulan.\"",
        f"3. \"Apakah angka konversi {client.name} bisa dipercaya? Periksa conversion_actions.\"",
        f"4. \"Buatkan laporan mingguan {client.name} (weekly_report) — jangan kirim dulu.\"",
        "",
        "## Menengah — menemukan pemborosan",
        f"5. \"Jalankan suggest_negative_keywords untuk {client.name} 30 hari terakhir. Kelompokkan: kompetitor, "
        "informasional, tidak relevan. Tandai yang sebaiknya masuk shared list.\"",
        f"6. \"Cek campaign_settings semua kampanye aktif {client.name}: Search Partners, perluasan Display, AI Max, "
        "mode lokasi. Usulkan perbaikan dengan alasannya.\"",
        f"7. \"Tampilkan campaign_structure '{first_campaign}' dan daftar temuan audit strukturnya.\"",
        f"8. \"Proyeksikan budget bulan ini (budget_pacing) dan sarankan budget harian {client.name}.\"",
        "",
        "## Ahli — mengeksekusi optimasi",
        f"9. \"Pecah grup iklan terbesar di '{first_campaign}' per produk: usulkan struktur grup iklan, kata kunci, "
        "dan 1 RSA per grup (validate_ad_copy). Jangan eksekusi dulu.\"",
        f"10. \"Lengkapi ekstensi '{first_campaign}': minimal 4 sitelink, 4–6 callout, 1 snippet Jenis, dan telepon. "
        "Pratinjau semua, tunggu persetujuan.\"",
        f"11. \"Bandingkan CPA per kampanye {client.name} 30 hari; usulkan realokasi budget 20–30% dengan hitungannya.\"",
        f"12. \"Jadwal iklan {client.name}: lihat jam kerja CS, usulkan set_ad_schedule; jelaskan dampaknya untuk "
        "formulir yang masuk di luar jam kerja.\"",
    ]
    if negatives:
        sample = ", ".join(f'"{n["text"]}"' for n in negatives[:5])
        p += ["", "## Usulan siap dieksekusi (dari data saat dossier dibuat)",
              f"13. \"Tambahkan negatif ini ke kampanye yang relevan untuk {client.name}: {sample} — pratinjau dulu.\""]

    # ---------------- AGENTS.md (satu-satunya berkas instruksi) ----------------
    g: list[str] = [
        f"# AGENTS.md — {client.name}",
        "",
        f"Kamu adalah performance marketer agensi untuk akun Google Ads {client.name} "
        f"(customer ID {client.customer_id}, Rupiah), bekerja lewat server MCP AdsAi (\"google-ads\").",
        "",
        "## Akun",
        f"- Selalu pakai `customer_id=\"{client.customer_id}\"`.",
        f"- Target biaya/konversi: {target}; budget bulanan: {budget}.",
        f"- Jenis akun saat kit dibuat: {kind['jenis']} — prioritas di SKILL.md.",
        "",
        "## Aturan",
        *[f"- {r}" for r in rules],
        "- Jangan menulis kredensial (.env, client_secret*.json) atau kontak klien ke berkas/commit.",
        "- Jawab dalam Bahasa Indonesia, mulai dari kesimpulan, satu rekomendasi dengan angka.",
        "",
        "## Konteks",
        "- `SKILL.md` — doktrin akun. `references/account-profile.md` — angka & temuan. "
        "`references/working-with-adsai.md` — kontrak tool. `PROMPTBOOK.md` — menu langkah berikutnya (pilih satu).",
        f"- CLI: `adsai alerts --client {client.key}`, `adsai report --client {client.key}`, "
        f"`adsai dossier --client {client.key}`.",
    ]

    # ---------------- README.md (pemasangan) ----------------
    r: list[str] = [
        f"# Kit dossier AdsAi — {client.name}",
        "",
        "| Berkas | Fungsi | Dipasang di |",
        "|---|---|---|",
        "| `SKILL.md` + `references/` | Doktrin akun; dimuat otomatis saat membahas akun ini | Claude: Settings → "
        "Capabilities → Skills (unggah folder sebagai zip); Claude Code: `~/.claude/skills/adsai-" + key + "/` |",
        "| `AGENTS.md` | Instruksi agen (satu-satunya) | Claude/ChatGPT Project: tempel ke *Project instructions*; "
        "Claude Code/Cursor/Codex: root folder proyek |",
        "| `references/account-profile.md` + `PROMPTBOOK.md` | Data & menu prompt | Claude/ChatGPT Project: unggah sebagai "
        "*Project knowledge* (data, bukan aturan) |",
        "| `README.md` | Petunjuk ini | Dibaca tim |",
        "",
        "Aturan pemasangan: **instruksi = AGENTS.md, pengetahuan = account-profile.md** — jangan dibalik.",
        "",
        f"Perbarui tiap awal bulan: `adsai dossier --client {client.key}`, lalu unggah ulang berkas yang berubah.",
    ]

    return {"SKILL.md": "\n".join(s) + "\n", "AGENTS.md": "\n".join(g) + "\n",
            "PROMPTBOOK.md": "\n".join(p) + "\n", "README.md": "\n".join(r) + "\n",
            "references/account-profile.md": "\n".join(f) + "\n",
            "references/working-with-adsai.md": "\n".join(w) + "\n"}


def write_kit(kit: dict[str, str], directory: Path) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    for name in OBSOLETE:
        (directory / name).unlink(missing_ok=True)
    paths = []
    for name, content in kit.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        paths.append(str(path))
    return paths
