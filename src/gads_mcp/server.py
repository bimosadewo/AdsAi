"""MCP server Google Ads untuk AdsAi."""

from __future__ import annotations

import functools
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import gaql
from .client import Ads, AdsError
from .config import load_settings
from .mutations import Mutator
from .safety import AuditLog

INSTRUCTIONS = """\
Server ini membaca dan mengelola akun Google Ads klien agensi.
- Mulai dengan list_accounts (atau list_clients) untuk mendapatkan customer_id.
- Semua nilai uang sudah dalam mata uang akun (bukan micros).
- Untuk kebutuhan yang tidak tercakup tool lain, pakai run_query dengan GAQL.
- Tools yang MENGUBAH akun selalu dua langkah: panggil dulu dengan confirm=false (pratinjau,
  divalidasi Google tanpa mengubah apa pun), tunjukkan rencananya ke pengguna, dan baru panggil
  dengan confirm=true setelah pengguna jelas menyetujui. Jangan pernah langsung confirm=true.
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
