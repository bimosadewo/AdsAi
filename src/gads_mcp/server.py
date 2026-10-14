"""MCP server Google Ads — Fase 1 (baca-saja)."""

from __future__ import annotations

import functools
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import gaql
from .client import Ads, AdsError
from .config import load_settings

INSTRUCTIONS = """\
Server ini membaca akun Google Ads (baca-saja, belum bisa mengubah apa pun).
- Mulai dengan list_accounts untuk mendapatkan customer_id.
- Semua nilai uang sudah dalam mata uang akun (bukan micros).
- Untuk kebutuhan yang tidak tercakup tool lain, pakai run_query dengan GAQL.
"""

mcp = MCPServer("google-ads", instructions=INSTRUCTIONS)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

_ads: Ads | None = None


def tool(fn: Callable) -> Callable:
    """Daftarkan tool baca-saja dan ubah error yang bisa dipahami menjadi pesan untuk Claude."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (AdsError, ValueError, RuntimeError) as ex:
            raise ToolError(str(ex)) from ex

    return mcp.tool(annotations=READ_ONLY)(wrapper)


def ads() -> Ads:
    global _ads
    if _ads is None:
        _ads = Ads(load_settings())
    return _ads


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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
