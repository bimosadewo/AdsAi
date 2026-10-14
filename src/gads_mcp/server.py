"""MCP server Google Ads untuk AdsAi."""

from __future__ import annotations

import functools
from datetime import timedelta
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import analysis, gaql
from .client import Ads, AdsError
from .clients import find_client, load_clients
from .collect import collect_snapshot, daily_rows, q_daily, today_local
from .config import load_settings
from .mutations import Mutator
from .safety import AuditLog
from .service import Context, alerts_message, build_weekly_report, send_message

INSTRUCTIONS = """\
Server ini membaca dan mengelola akun Google Ads klien agensi (uang sungguhan).
- Mulai dengan list_clients / client_health (atau list_accounts) untuk customer_id dan target klien.
- Semua nilai uang dalam mata uang akun (mis. 50000 = Rp50.000), bukan micros.
- Cek conversion_actions sebelum menilai CPA: konversi Utama > 3 atau aksi ringan sebagai Utama
  berarti angka konversi belum bisa dipercaya.
- Tools yang MENGUBAH akun selalu dua langkah: confirm=false (pratinjau tervalidasi Google, tidak
  mengubah apa pun) -> tunjukkan rencana ke pengguna -> confirm=true hanya setelah pengguna jelas
  setuju. Setelah itu laporkan isi 'terverifikasi' (kondisi akun yang dibaca ulang).
- send_to_client mengirim pesan ke klien: pratinjau dulu, kirim hanya setelah disetujui.
- Jangan mengarang angka; bila tool gagal, sebutkan error-nya.
- Untuk kebutuhan yang tidak tercakup tool lain, pakai run_query dengan GAQL.
"""

mcp = MCPServer("google-ads", instructions=INSTRUCTIONS)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

_ads: Ads | None = None


def _register(fn: Callable, annotations: ToolAnnotations) -> Callable:
    """Daftarkan tool dan ubah error yang bisa dipahami menjadi pesan untuk Claude."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (AdsError, ValueError, RuntimeError) as ex:
            raise ToolError(str(ex)) from ex

    return mcp.tool(annotations=annotations)(wrapper)


def tool(fn: Callable) -> Callable:
    return _register(fn, READ_ONLY)


CONFIRM_DOC = """

confirm=false (default): pratinjau + validasi Google, TIDAK mengubah apa pun.
confirm=true: jalankan sungguhan dan catat di log audit. Hanya setelah pengguna setuju."""


def write_tool(fn: Callable) -> Callable:
    fn.__doc__ = (fn.__doc__ or "").rstrip() + CONFIRM_DOC
    return _register(fn, WRITE)


def ads() -> Ads:
    global _ads
    if _ads is None:
        _ads = Ads(load_settings())
    return _ads


def mutator() -> Mutator:
    a = ads()
    return Mutator(a, AuditLog(a.settings.safety.audit_log_dir))


def _run(customer_id: str | None, query: str) -> tuple[str, list[dict[str, Any]]]:
    a = ads()
    cid = a.customer_id(customer_id)
    return cid, a.search(cid, query)


@tool
def list_accounts() -> dict:
    """Daftar akun klien di bawah akun Manager (MCC), atau akun yang bisa diakses bila tanpa MCC."""
    a = ads()
    login = a.settings.login_customer_id
    if login:
        rows = a.search(login, gaql.q_accounts())
        return {"manager_id": login, "akun": rows}
    return {"akun_yang_bisa_diakses": a.accessible_customers()}


@tool
def list_campaigns(customer_id: str | None = None, include_removed: bool = False) -> dict:
    """Daftar kampanye beserta status, alasan status (primary_status_reasons), jenis, bidding, dan budget harian."""
    cid, rows = _run(customer_id, gaql.q_campaigns(include_removed))
    return {"customer_id": cid, "jumlah": len(rows), "kampanye": rows}


@tool
def campaign_performance(
    customer_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    start: str | None = None,
    end: str | None = None,
    only_with_impressions: bool = True,
) -> dict:
    """Performa per kampanye: biaya, klik, CTR, CPC, konversi, biaya/konversi, dan pangsa tayangan.

    date_range: LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, LAST_MONTH, dll. Atau isi start & end (YYYY-MM-DD).
    """
    cid, rows = _run(customer_id, gaql.q_campaign_performance(gaql.date_clause(date_range, start, end), only_with_impressions))
    return {"customer_id": cid, "ringkasan": gaql.summarize(rows), "kampanye": rows}


@tool
def search_terms(
    customer_id: str | None = None,
    campaign_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    start: str | None = None,
    end: str | None = None,
    min_cost: float = 0,
    limit: int = 300,
) -> dict:
    """Istilah penelusuran yang memicu iklan, diurutkan dari biaya terbesar. min_cost dalam mata uang akun."""
    q = gaql.q_search_terms(gaql.date_clause(date_range, start, end), campaign_id, int(min_cost * 1_000_000))
    cid, rows = _run(customer_id, gaql.ensure_read_only(f"{q} LIMIT {min(limit, gaql.MAX_ROWS)}"))
    zero = [r for r in rows if not r.get("metrics.conversions")]
    return {
        "customer_id": cid,
        "ringkasan": gaql.summarize(rows),
        "biaya_tanpa_konversi": round(sum(float(r.get("metrics.cost") or 0) for r in zero), 2),
        "istilah": rows,
    }


@tool
def keywords(
    customer_id: str | None = None,
    campaign_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Kata kunci beserta match type, Quality Score (dan komponennya), CPC, dan konversi."""
    cid, rows = _run(customer_id, gaql.q_keywords(gaql.date_clause(date_range, start, end), campaign_id))
    return {"customer_id": cid, "ringkasan": gaql.summarize(rows), "kata_kunci": rows}


@tool
def conversion_actions(customer_id: str | None = None) -> dict:
    """Semua conversion action: Utama/Sekunder, kategori, cara hitung, jangka waktu, dan nilai default."""
    cid, rows = _run(customer_id, gaql.q_conversion_actions())
    primary = [r for r in rows if r.get("conversion_action.primary_for_goal")]
    return {
        "customer_id": cid,
        "jumlah": len(rows),
        "jumlah_utama": len(primary),
        "peringatan": (
            f"Ada {len(primary)} konversi Utama. Idealnya 1-3 aksi yang benar-benar lead/penjualan."
            if len(primary) > 3
            else None
        ),
        "konversi": rows,
    }


@tool
def account_alerts(customer_id: str | None = None) -> dict:
    """Cek kesehatan akun: kampanye aktif tanpa tayangan 7 hari, konversi Utama berlebihan, dan impor konversi offline yang gagal."""
    a = ads()
    cid = a.customer_id(customer_id)
    alerts: list[str] = []

    rows = a.search(cid, gaql.q_enabled_campaign_impressions())
    impressions: dict[str, int] = {}
    for r in rows:
        key = r["campaign.id"]
        impressions[key] = impressions.get(key, 0) + int(r.get("metrics.impressions") or 0)
    enabled = a.search(cid, "SELECT campaign.id, campaign.name, campaign.primary_status, campaign.primary_status_reasons "
                            "FROM campaign WHERE campaign.status = 'ENABLED'")
    for c in enabled:
        if impressions.get(c["campaign.id"], 0) == 0:
            alerts.append(
                f"Kampanye aktif '{c['campaign.name']}' tidak tayang 7 hari terakhir "
                f"(status: {c.get('campaign.primary_status')}, alasan: {c.get('campaign.primary_status_reasons')}). "
                "Cek saldo/pembayaran dan status iklan."
            )

    conv = a.search(cid, gaql.q_conversion_actions())
    n_primary = sum(1 for r in conv if r.get("conversion_action.primary_for_goal"))
    if n_primary > 3:
        alerts.append(f"{n_primary} konversi berstatus Utama — angka konversi kemungkinan menggelembung.")

    try:
        uploads = a.search(cid, gaql.q_offline_upload_summary())
    except AdsError:
        uploads = []
    for u in uploads:
        rate = u.get("offline_conversion_upload_client_summary.success_rate")
        if rate is not None and float(rate) < 0.9:
            alerts.append(
                f"Impor konversi offline ({u.get('offline_conversion_upload_client_summary.client')}) "
                f"tingkat sukses hanya {float(rate):.0%}."
            )

    return {"customer_id": cid, "jumlah_peringatan": len(alerts), "peringatan": alerts}


@tool
def run_query(query: str, customer_id: str | None = None) -> dict:
    """Jalankan query GAQL bebas (hanya SELECT, maks 1000 baris).

    Contoh: SELECT ad_group.name, metrics.clicks FROM ad_group WHERE segments.date DURING LAST_7_DAYS
    Referensi field: https://developers.google.com/google-ads/api/fields
    """
    cid, rows = _run(customer_id, gaql.ensure_read_only(query))
    return {"customer_id": cid, "jumlah": len(rows), "baris": rows}


# ---------------------------------------------------------------------------
# Fase 2 — tools yang mengubah akun (selalu pratinjau dulu)
# ---------------------------------------------------------------------------

@write_tool
def add_negative_keywords(campaign_id: str, keywords: list[dict], customer_id: str | None = None,
                          confirm: bool = False) -> dict:
    """Tambah kata kunci negatif di level kampanye. keywords: [{"text": "...", "match_type": "PHRASE|EXACT|BROAD"}].
    Kata kunci yang sudah terpasang otomatis dilewati."""
    a = ads()
    return mutator().add_negative_keywords(a.customer_id(customer_id), campaign_id, keywords, confirm)


@write_tool
def remove_negative_keywords(campaign_id: str, texts: list[str], customer_id: str | None = None,
                             confirm: bool = False) -> dict:
    """Hapus kata kunci negatif kampanye berdasarkan teksnya (semua match type yang cocok)."""
    a = ads()
    return mutator().remove_negative_keywords(a.customer_id(customer_id), campaign_id, texts, confirm)


@write_tool
def update_budget(campaign_id: str, daily_budget: float, customer_id: str | None = None,
                  confirm: bool = False, allow_shared: bool = False) -> dict:
    """Ubah budget harian kampanye (mata uang akun, mis. 50000 = Rp50.000).
    Dibatasi ADSAI_MAX_DAILY_BUDGET dan ADSAI_MAX_BUDGET_INCREASE_FACTOR."""
    a = ads()
    return mutator().update_budget(a.customer_id(customer_id), campaign_id, daily_budget, confirm, allow_shared)


@write_tool
def set_status(level: str, ids: list[str], status: str, customer_id: str | None = None,
               confirm: bool = False) -> dict:
    """Jeda/aktifkan. level: campaign | ad_group | keyword. status: ENABLED | PAUSED.
    Untuk keyword, id berformat 'ad_group_id~criterion_id'. Menghapus permanen tidak didukung."""
    a = ads()
    return mutator().set_status(a.customer_id(customer_id), level, ids, status, confirm)


@write_tool
def update_conversion_action(conversion_action_id: str, customer_id: str | None = None, confirm: bool = False,
                             primary_for_goal: bool | None = None, counting_type: str | None = None,
                             click_through_days: int | None = None, view_through_days: int | None = None,
                             status: str | None = None) -> dict:
    """Ubah conversion action: Utama/Sekunder (primary_for_goal), counting_type (ONE_PER_CLICK/MANY_PER_CLICK),
    jangka waktu klik (1-90) / tayang (1-30) hari, status (ENABLED/HIDDEN)."""
    a = ads()
    return mutator().update_conversion_action(
        a.customer_id(customer_id), conversion_action_id, confirm, primary_for_goal, counting_type,
        click_through_days, view_through_days, status)


@write_tool
def update_bid_strategy(campaign_id: str, strategy: str, customer_id: str | None = None, confirm: bool = False,
                        target_cpa: float | None = None, max_cpc: float | None = None) -> dict:
    """Ganti strategi bidding: MAXIMIZE_CONVERSIONS (opsional target_cpa), MAXIMIZE_CLICKS (opsional max_cpc),
    atau MANUAL_CPC."""
    a = ads()
    return mutator().update_bid_strategy(a.customer_id(customer_id), campaign_id, strategy, confirm, target_cpa, max_cpc)


@write_tool
def add_keywords(ad_group_id: str, keywords: list[dict], customer_id: str | None = None, confirm: bool = False,
                 cpc_bid: float | None = None, paused: bool = False) -> dict:
    """Tambah kata kunci ke grup iklan. keywords: [{"text": "...", "match_type": "EXACT|PHRASE|BROAD"}]."""
    a = ads()
    return mutator().add_keywords(a.customer_id(customer_id), ad_group_id, keywords, confirm, cpc_bid, paused)


@tool
def change_log(customer_id: str | None = None, limit: int = 50) -> dict:
    """Riwayat perubahan yang dilakukan lewat AdsAi untuk satu akun (terbaru dulu)."""
    a = ads()
    cid = a.customer_id(customer_id)
    return {"customer_id": cid, "perubahan": AuditLog(a.settings.safety.audit_log_dir).read(cid, limit)}


# ---------------------------------------------------------------------------
# Fase 3 — klien, kesehatan akun, laporan, pengiriman
# ---------------------------------------------------------------------------

def ctx() -> Context:
    a = ads()
    return Context(a.settings, a, load_clients(a.settings.clients_file), AuditLog(a.settings.safety.audit_log_dir))


@tool
def list_clients() -> dict:
    """Daftar klien agensi dari clients.yaml (nama, customer ID, kontak, target CPA, budget bulanan)."""
    a = ads()
    return {"klien": [c.to_dict() for c in load_clients(a.settings.clients_file).values()]}


@tool
def client_health(client: str | None = None) -> dict:
    """Cek kesehatan akun satu klien (key/nama/customer ID) atau semua klien aktif: tidak tayang, konversi nol,
    anomali harian, CPA naik, pacing budget, konversi Utama berlebih, impor offline gagal."""
    c = ctx()
    out = []
    for cl in c.active_clients(client):
        try:
            snap = collect_snapshot(c.ads, cl, with_search_terms=False)
            alerts = analysis.evaluate_alerts(snap, cl)
            out.append({"klien": cl.name, "customer_id": cl.customer_id,
                        "alert": [x.to_dict() for x in alerts],
                        "minggu_ini": snap["minggu_ini"], "minggu_lalu": snap["minggu_lalu"],
                        "pacing": snap["pacing"], "pesan_wa": alerts_message(cl, alerts)})
        except (AdsError, ValueError) as ex:
            out.append({"klien": cl.name, "error": str(ex)})
    return {"hasil": out}


@tool
def weekly_report(client: str, fmt: str = "whatsapp") -> dict:
    """Buat laporan mingguan (7 hari terakhir vs minggu sebelumnya) untuk satu klien. Hanya pratinjau,
    tidak mengirim apa pun. fmt: whatsapp | markdown."""
    c = ctx()
    report = build_weekly_report(c, c.client(client), fmt)
    return {"klien": report["klien"], "teks": report["teks"], "alert": report["alert"]}


@write_tool
def send_to_client(client: str, message: str, subject: str = "Laporan Google Ads", confirm: bool = False) -> dict:
    """Kirim pesan (mis. teks dari weekly_report) ke WhatsApp/email klien sesuai clients.yaml.
    confirm=false: tampilkan siapa penerimanya tanpa mengirim. confirm=true: kirim sungguhan.
    Selalu tunjukkan isi pesan dan penerima ke pengguna sebelum confirm=true."""
    c = ctx()
    cl = c.client(client)
    if not confirm:
        return {"status": "PRATINJAU", "klien": cl.name, "whatsapp": list(cl.whatsapp), "email": list(cl.email),
                "pesan": message, "catatan": "Belum dikirim. Panggil lagi dengan confirm=true setelah pengguna setuju."}
    return {"status": "DIKIRIM", "hasil": send_message(cl, subject, message)}


# ---------------------------------------------------------------------------
# Fase 4 — analisa lanjutan
# ---------------------------------------------------------------------------

def _resolve(client: str | None, customer_id: str | None):
    """Kembalikan (customer_id, objek klien atau None)."""
    a = ads()
    if client:
        cl = find_client(load_clients(a.settings.clients_file), client)
        return cl.customer_id, cl
    return a.customer_id(customer_id), None


@tool
def ngram_analysis(client: str | None = None, customer_id: str | None = None, campaign_id: str | None = None,
                   n: int = 1, date_range: str = "LAST_30_DAYS", start: str | None = None, end: str | None = None,
                   min_cost: float = 0, limit: int = 50) -> dict:
    """Kelompokkan istilah penelusuran per kata (n=1), dua kata (n=2), dst. dengan biaya, klik, konversi —
    untuk menemukan pola pemborosan yang tidak terlihat per istilah."""
    cid, _ = _resolve(client, customer_id)
    q = gaql.q_search_terms(gaql.date_clause(date_range, start, end), campaign_id)
    rows = ads().search(cid, f"{q} LIMIT {gaql.MAX_ROWS}")
    return {"customer_id": cid, "n": n, "jumlah_istilah": len(rows),
            "ngram": analysis.ngram_table(rows, max(1, min(n, 4)), min_cost=min_cost)[:limit]}


@tool
def suggest_negative_keywords(client: str | None = None, customer_id: str | None = None,
                              campaign_id: str | None = None, date_range: str = "LAST_30_DAYS",
                              start: str | None = None, end: str | None = None,
                              min_cost: float | None = None) -> dict:
    """Usulan kata kunci negatif dari istilah/n-gram yang memakan biaya tanpa konversi. Kata produk klien
    (protected_terms di clients.yaml) dan kata niat beli (harga, jual, distributor, ...) tidak pernah diusulkan.
    Hasilnya bisa langsung dipakai di add_negative_keywords (tetap lewat pratinjau)."""
    cid, cl = _resolve(client, customer_id)
    q = gaql.q_search_terms(gaql.date_clause(date_range, start, end), campaign_id)
    a = ads()
    rows = a.search(cid, f"{q} LIMIT {gaql.MAX_ROWS}")
    existing = [r.get("campaign_criterion.keyword.text", "") for r in a.search(cid, (
        "SELECT campaign_criterion.keyword.text FROM campaign_criterion WHERE campaign_criterion.negative = TRUE "
        "AND campaign_criterion.type = 'KEYWORD'" + (f" AND campaign.id = {int(campaign_id)}" if campaign_id else "")))]
    threshold = min_cost if min_cost is not None else (cl.thresholds.min_waste_cost if cl else 20_000)
    sugg = analysis.suggest_negatives(rows, threshold, cl.protected_terms if cl else (), existing)
    return {"customer_id": cid, "ambang_biaya": threshold, "jumlah_usulan": len(sugg),
            "potensi_hemat": round(sum(s["biaya"] for s in sugg), 2), "usulan": sugg,
            "catatan": None if cl else "Tanpa clients.yaml, kata produk klien tidak terlindungi — periksa usulan dengan teliti."}


@tool
def detect_anomalies(client: str | None = None, customer_id: str | None = None, days: int = 21,
                     z: float = 2.5) -> dict:
    """Deteksi lonjakan/penurunan tajam kemarin (biaya, klik, konversi, CPC) dibanding hari-hari sebelumnya,
    plus jumlah hari berturut-turut tanpa konversi."""
    cid, _ = _resolve(client, customer_id)
    t = today_local()
    daily = daily_rows(ads().search(cid, q_daily((t - timedelta(days=days)).isoformat(), (t - timedelta(days=1)).isoformat())))
    return {"customer_id": cid, "hari": len(daily), "anomali": analysis.detect_anomalies(daily, z),
            "hari_tanpa_konversi_berturut": analysis.zero_conversion_streak(daily), "harian": daily}


@tool
def budget_pacing(client: str | None = None, customer_id: str | None = None,
                  monthly_budget: float | None = None) -> dict:
    """Proyeksi biaya akhir bulan vs budget bulanan (dari clients.yaml atau parameter monthly_budget),
    plus saran budget harian agar pas."""
    cid, cl = _resolve(client, customer_id)
    budget = monthly_budget or (cl.monthly_budget if cl else None)
    if not budget:
        raise ValueError("monthly_budget belum diketahui. Isi di clients.yaml atau kirim sebagai parameter.")
    t = today_local()
    if t.day == 1:
        return {"customer_id": cid, "catatan": "Hari pertama bulan ini; belum ada data untuk proyeksi."}
    mtd = sum(r["cost"] for r in daily_rows(ads().search(cid, q_daily(t.replace(day=1).isoformat(),
                                                                (t - timedelta(days=1)).isoformat()))))
    return {"customer_id": cid, **analysis.budget_pacing(mtd, budget, t)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
