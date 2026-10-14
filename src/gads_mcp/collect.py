"""Mengumpulkan data satu klien dari Google Ads menjadi 'snapshot' untuk alert & laporan."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from . import gaql
from .analysis import budget_pacing
from .client import AdsError

TZ = ZoneInfo("Asia/Jakarta")


class Searcher(Protocol):
    def search(self, customer_id: str, query: str) -> list[dict[str, Any]]: ...


def today_local() -> date:
    return datetime.now(TZ).date()


def week_ranges(today: date) -> tuple[tuple[str, str], tuple[str, str]]:
    """(7 hari penuh terakhir, 7 hari sebelumnya) — tidak termasuk hari ini."""
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    return (start.isoformat(), end.isoformat()), (prev_start.isoformat(), prev_end.isoformat())


def q_daily(start: str, end: str) -> str:
    return (
        "SELECT segments.date, metrics.cost_micros, metrics.clicks, metrics.impressions, metrics.conversions "
        f"FROM customer WHERE segments.date BETWEEN '{start}' AND '{end}'"
    )


def q_campaign_impressions(start: str, end: str) -> str:
    return (
        "SELECT campaign.id, campaign.name, campaign.primary_status_reasons, metrics.impressions "
        f"FROM campaign WHERE campaign.status = 'ENABLED' AND segments.date BETWEEN '{start}' AND '{end}'"
    )


Q_ENABLED = ("SELECT campaign.id, campaign.name, campaign.primary_status, campaign.primary_status_reasons "
             "FROM campaign WHERE campaign.status = 'ENABLED' AND campaign.serving_status = 'SERVING'")
Q_DISAPPROVED = ("SELECT campaign.name, ad_group.name, ad_group_ad.ad.id FROM ad_group_ad "
                 "WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED' "
                 "AND ad_group_ad.status = 'ENABLED' AND ad_group_ad.policy_summary.approval_status = 'DISAPPROVED'")
Q_SETTINGS = ("SELECT campaign.name, campaign.advertising_channel_type, "
              "campaign.network_settings.target_search_network, campaign.network_settings.target_content_network, "
              "campaign.geo_target_type_setting.positive_geo_target_type FROM campaign "
              "WHERE campaign.status = 'ENABLED' AND campaign.advertising_channel_type = 'SEARCH'")
Q_NEGATIVES = ("SELECT campaign_criterion.keyword.text FROM campaign_criterion "
               "WHERE campaign_criterion.negative = TRUE AND campaign_criterion.type = 'KEYWORD' "
               "AND campaign.status != 'REMOVED'")


def daily_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        out.append({
            "date": r.get("segments.date"),
            "cost": float(r.get("metrics.cost") or 0),
            "clicks": int(r.get("metrics.clicks") or 0),
            "impressions": int(r.get("metrics.impressions") or 0),
            "conversions": float(r.get("metrics.conversions") or 0),
        })
    return sorted(out, key=lambda r: r["date"])


def collect_snapshot(ads: Searcher, client: Any, today: date | None = None,
                     with_search_terms: bool = True) -> dict[str, Any]:
    today = today or today_local()
    cid = client.customer_id
    (w_start, w_end), (p_start, p_end) = week_ranges(today)

    week_rows = ads.search(cid, gaql.q_campaign_performance(f"segments.date BETWEEN '{w_start}' AND '{w_end}'"))
    prev_rows = ads.search(cid, gaql.q_campaign_performance(f"segments.date BETWEEN '{p_start}' AND '{p_end}'"))

    daily_start = (today - timedelta(days=21)).isoformat()
    daily = daily_rows(ads.search(cid, q_daily(daily_start, w_end)))

    # Kampanye aktif & tayang menurut status, tapi 0 tayangan 3 hari terakhir.
    d3 = (today - timedelta(days=3)).isoformat()
    imp: dict[str, int] = {}
    for r in ads.search(cid, q_campaign_impressions(d3, w_end)):
        imp[r["campaign.id"]] = imp.get(r["campaign.id"], 0) + int(r.get("metrics.impressions") or 0)
    not_serving = [
        {"id": c["campaign.id"], "nama": c["campaign.name"],
         "alasan": ", ".join(c.get("campaign.primary_status_reasons") or []) or None}
        for c in ads.search(cid, Q_ENABLED) if imp.get(c["campaign.id"], 0) == 0
    ]

    disapproved = [
        {"kampanye": r.get("campaign.name"), "grup": r.get("ad_group.name"), "ad_id": r.get("ad_group_ad.ad.id")}
        for r in ads.search(cid, Q_DISAPPROVED)
    ]

    risky_settings = []
    for r in ads.search(cid, Q_SETTINGS):
        issues = []
        if r.get("campaign.network_settings.target_search_network"):
            issues.append("Search Partners")
        if r.get("campaign.network_settings.target_content_network"):
            issues.append("perluasan Display")
        if r.get("campaign.geo_target_type_setting.positive_geo_target_type") == "PRESENCE_OR_INTEREST":
            issues.append("lokasi 'kehadiran atau minat'")
        if issues:
            risky_settings.append({"kampanye": r.get("campaign.name"), "masalah": issues})

    conv = ads.search(cid, gaql.q_conversion_actions())
    n_primary = sum(1 for r in conv if r.get("conversion_action.primary_for_goal"))

    try:
        uploads = ads.search(cid, gaql.q_offline_upload_summary())
    except AdsError:
        uploads = []
    upload_issues = [
        {"nama": u.get("offline_conversion_upload_client_summary.client"),
         "rate": float(u.get("offline_conversion_upload_client_summary.success_rate") or 0)}
        for u in uploads
        if u.get("offline_conversion_upload_client_summary.success_rate") is not None
        and float(u["offline_conversion_upload_client_summary.success_rate"]) < 0.9
    ]

    pacing = None
    if client.monthly_budget and today.day > 1:
        first = today.replace(day=1).isoformat()
        mtd = sum(r["cost"] for r in daily_rows(ads.search(cid, q_daily(first, w_end))))
        pacing = budget_pacing(mtd, client.monthly_budget, today)

    snapshot: dict[str, Any] = {
        "klien": client.name,
        "customer_id": cid,
        "dibuat": datetime.now(TZ).isoformat(timespec="minutes"),
        "periode": {"minggu_ini": [w_start, w_end], "minggu_lalu": [p_start, p_end]},
        "minggu_ini": gaql.summarize(week_rows),
        "minggu_lalu": gaql.summarize(prev_rows),
        "kampanye_minggu_ini": week_rows,
        "harian": daily,
        "kampanye_aktif_tidak_tayang": not_serving,
        "jumlah_konversi_utama": n_primary,
        "iklan_ditolak": disapproved,
        "pengaturan_berisiko": risky_settings,
        "impor_offline_bermasalah": upload_issues,
        "pacing": pacing,
    }
    if with_search_terms:
        q = gaql.q_search_terms(f"segments.date BETWEEN '{w_start}' AND '{w_end}'")
        snapshot["istilah_minggu_ini"] = ads.search(cid, f"{q} LIMIT 500")
        snapshot["negatif_terpasang"] = [r.get("campaign_criterion.keyword.text", "") for r in ads.search(cid, Q_NEGATIVES)]
    return snapshot
