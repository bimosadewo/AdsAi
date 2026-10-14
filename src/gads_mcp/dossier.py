"""Kit dossier per klien: konteks akun yang dikemas untuk agen AI (Claude, ChatGPT, Claude Code, Cursor).

Terinspirasi "Expert Space" Adspirer (kit berbayar per akun). Versi AdsAi dibuat otomatis dari data akun,
gratis, bisa diperbarui kapan saja (`adsai dossier` / tool `client_dossier`), dan berbahasa Indonesia.

Isi folder dossiers/<klien>/:
  SKILL.md               profil akun + aturan main (format Agent Skills: frontmatter name/description)
  PROMPTBOOK.md          prompt siap pakai, Pemula → Menengah → Ahli
  AGENT_INSTRUCTIONS.md  instruksi siap tempel untuk Claude/ChatGPT Project
  AGENTS.md              instruksi untuk agen coding (Claude Code, Cursor, Codex)

Kontak klien (WA/email) sengaja TIDAK dimasukkan.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Protocol

from . import gaql
from .analysis import Alert, estimated_savings, evaluate_alerts, suggest_negatives
from .collect import collect_snapshot, today_local
from .fmt import number, rupiah

FILES = ("SKILL.md", "PROMPTBOOK.md", "AGENT_INSTRUCTIONS.md", "AGENTS.md")


class Searcher(Protocol):
    def search(self, customer_id: str, query: str) -> list[dict[str, Any]]: ...


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "klien"


# ---------------------------------------------------------------------------
# Pengumpulan data
# ---------------------------------------------------------------------------

def fetch_data(ads: Searcher, client: Any, today: date | None = None, days: int = 90) -> dict[str, Any]:
    today = today or today_local()
    end = (today - timedelta(days=1)).isoformat()
    start = (today - timedelta(days=days)).isoformat()
    period = f"segments.date BETWEEN '{start}' AND '{end}'"
    cid = client.customer_id
    return {
        "today": today,
        "periode": (start, end),
        "snapshot": collect_snapshot(ads, client, today=today),
        "kampanye_periode": ads.search(cid, gaql.q_campaign_performance(period)),
        "kampanye_aktif": ads.search(cid, (
            "SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.bidding_strategy_type, "
            "campaign_budget.amount_micros, campaign.network_settings.target_search_network, "
            "campaign.network_settings.target_content_network, campaign.ai_max_setting.enable_ai_max, "
            "campaign.geo_target_type_setting.positive_geo_target_type FROM campaign "
            "WHERE campaign.status = 'ENABLED' ORDER BY campaign.name")),
        "konversi": ads.search(cid, gaql.q_conversion_actions()),
        "istilah_periode": ads.search(cid, f"{gaql.q_search_terms(period)} LIMIT 1000"),
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


def build_kit(client: Any, data: dict[str, Any]) -> dict[str, str]:
    snap = data["snapshot"]
    start, end = data["periode"]
    today = data["today"].isoformat()
    key = slug(client.key)
    total = gaql.summarize(data["kampanye_periode"])
    alerts: list[Alert] = evaluate_alerts(snap, client)
    primary = [c for c in data["konversi"] if c.get("conversion_action.primary_for_goal")]
    terms = data["istilah_periode"]
    winners = sorted((t for t in terms if float(t.get("metrics.conversions") or 0) > 0),
                     key=lambda t: -float(t.get("metrics.conversions") or 0))[:12]
    negatives = suggest_negatives(terms, client.thresholds.min_waste_cost, client.protected_terms,
                                  snap.get("negatif_terpasang", []))[:10]
    active = data["kampanye_aktif"]
    first_campaign = active[0].get("campaign.name") if active else "kampanye utama"
    products = ", ".join(client.protected_terms) or "(belum diisi — tambahkan protected_terms di clients.yaml)"

    # ---------------- SKILL.md ----------------
    s: list[str] = [
        "---",
        f"name: adsai-{key}",
        f"description: Konteks akun Google Ads {client.name} (customer ID {client.customer_id}) — produk, target, "
        "kampanye aktif, konversi, pola search terms, temuan terkini, dan aturan main. Pakai untuk SEMUA pertanyaan "
        f"atau perubahan tentang iklan Google {client.name}.",
        "---",
        "",
        f"# {client.name} — dossier Google Ads",
        "",
        f"Dibuat {today} dari data {start} s.d. {end} oleh AdsAi. Perbarui dengan `adsai dossier --client {client.key}`"
        " atau tool `client_dossier`. Angka di sini adalah potret saat dibuat — untuk angka terbaru selalu panggil tool.",
        "",
        "## Akun",
        f"- Customer ID: **{client.customer_id}** — selalu kirim `customer_id=\"{client.customer_id}\"` ke tool AdsAi.",
        "- Mata uang: Rupiah (nilai tool dalam rupiah, mis. 50000 = Rp50.000).",
        f"- Target biaya/konversi: **{rupiah(client.target_cpa)}**" if client.target_cpa else "- Target biaya/konversi: belum ditetapkan",
        f"- Budget iklan bulanan disepakati: **{rupiah(client.monthly_budget)}**" if client.monthly_budget else "- Budget bulanan: belum ditetapkan",
        f"- Produk / kata yang dilindungi (tidak boleh jadi negatif luas): {products}",
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
        "",
        "## Aturan main akun ini",
        "1. Uang butuh persetujuan: setiap perubahan lewat pratinjau (`confirm=false`) dulu, eksekusi hanya setelah "
        "pengguna setuju, laporkan isi `terverifikasi`.",
        f"2. Jangan pernah memblokir kata produk ({products}) atau kata niat beli (harga, jual, distributor, terdekat) "
        "sebagai negatif PHRASE/BROAD.",
        "3. Cek `conversion_actions` sebelum menilai CPA; bila konversi Utama > 3, katakan angka belum bisa dipercaya.",
        "4. Budget naik bertahap (maks 2× sekali ubah, dijaga AdsAi); realokasi 20–30% lalu tunggu seminggu.",
        "5. Ganti strategi bidding/konversi paling sering sekali per 2 minggu per kampanye (fase belajar).",
        "6. Iklan & kampanye baru dibuat dijeda; aktifkan hanya setelah pengguna setuju.",
        "7. Jangan mengarang angka. Bila tool gagal, sebutkan tool dan error-nya.",
        "",
        "Kontrak lengkap & peta tool: skill `adsai-agent`. Alur kerja: `adsai-review`, `adsai-optimasi`, `adsai-iklan`.",
    ]

    # ---------------- PROMPTBOOK.md ----------------
    p: list[str] = [
        f"# Prompt book — {client.name}",
        "",
        "Salin prompt, kirim ke Claude yang tersambung ke AdsAi. Semua perubahan tetap lewat pratinjau.",
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

    # ---------------- AGENT_INSTRUCTIONS.md ----------------
    a: list[str] = [
        f"# Instruksi Project — {client.name}",
        "",
        "Tempel ke **Claude → Project → Project instructions** (atau ChatGPT Project). Lampirkan juga SKILL.md "
        "sebagai pengetahuan project.",
        "",
        "```",
        f"Kamu adalah performance marketer agensi untuk akun Google Ads {client.name} "
        f"(customer ID {client.customer_id}, Rupiah).",
        f"- Selalu pakai customer_id \"{client.customer_id}\" di tool AdsAi (server MCP \"google-ads\").",
        f"- Target biaya/konversi: {rupiah(client.target_cpa)}; budget bulanan: {rupiah(client.monthly_budget)}.",
        f"- Kata produk yang dilindungi: {products}.",
        "- Semua perubahan: pratinjau confirm=false → tunjukkan rencana → confirm=true hanya setelah saya setuju.",
        "- Cek conversion_actions sebelum menilai CPA. Jangan mengarang angka.",
        "- Jawab dalam Bahasa Indonesia, mulai dari kesimpulan, satu rekomendasi dengan angka.",
        "```",
    ]

    # ---------------- AGENTS.md ----------------
    g: list[str] = [
        f"# AGENTS.md — {client.name}",
        "",
        "Untuk agen coding (Claude Code, Cursor, Codex) yang bekerja dengan akun ini lewat AdsAi.",
        "",
        "## Setup",
        "- Server MCP: `google-ads-mcp` (repo AdsAi). Claude Code: `claude mcp add google-ads -- <path>/.venv/bin/google-ads-mcp`",
        f"- Akun: customer ID `{client.customer_id}` (klien `{client.key}` di clients.yaml).",
        f"- CLI: `adsai alerts --client {client.key}`, `adsai report --client {client.key}`, `adsai dossier --client {client.key}`.",
        "",
        "## Aturan",
        "- Jangan pernah memanggil tool ubah dengan confirm=true tanpa persetujuan eksplisit manusia di percakapan.",
        "- Jangan menulis kredensial (.env, client_secret*.json) atau kontak klien ke berkas/commit.",
        "- Baca SKILL.md di folder ini untuk konteks akun dan aturan main.",
    ]

    return {"SKILL.md": "\n".join(s) + "\n", "PROMPTBOOK.md": "\n".join(p) + "\n",
            "AGENT_INSTRUCTIONS.md": "\n".join(a) + "\n", "AGENTS.md": "\n".join(g) + "\n"}


def write_kit(kit: dict[str, str], directory: Path) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in kit.items():
        path = directory / name
        path.write_text(content, encoding="utf-8")
        paths.append(str(path))
    return paths
