"""Query GAQL dan pengolah hasil. Semua fungsi di sini murni (tanpa API) agar mudah dites."""

from __future__ import annotations

import re
from typing import Any, Iterable

DATE_RANGES = {
    "TODAY",
    "YESTERDAY",
    "LAST_7_DAYS",
    "LAST_14_DAYS",
    "LAST_30_DAYS",
    "LAST_BUSINESS_WEEK",
    "THIS_MONTH",
    "LAST_MONTH",
    "THIS_WEEK_SUN_TODAY",
    "THIS_WEEK_MON_TODAY",
    "LAST_WEEK_SUN_SAT",
    "LAST_WEEK_MON_SUN",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ROWS = 1000


def date_clause(date_range: str = "LAST_30_DAYS", start: str | None = None, end: str | None = None) -> str:
    """Bangun filter tanggal GAQL. Tanggal kustom (YYYY-MM-DD) mengalahkan date_range."""
    if start or end:
        if not (start and end and _DATE_RE.match(start) and _DATE_RE.match(end)):
            raise ValueError("start dan end wajib diisi bersamaan dengan format YYYY-MM-DD.")
        if start > end:
            raise ValueError("start tidak boleh setelah end.")
        return f"segments.date BETWEEN '{start}' AND '{end}'"
    key = date_range.upper()
    if key not in DATE_RANGES:
        raise ValueError(f"date_range tidak dikenal: {date_range}. Pilihan: {sorted(DATE_RANGES)}")
    return f"segments.date DURING {key}"


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def ensure_read_only(query: str) -> str:
    """Tolak apa pun selain SELECT dan batasi jumlah baris."""
    q = query.strip().rstrip(";")
    if not q.upper().startswith("SELECT "):
        raise ValueError("Hanya query SELECT yang diizinkan.")
    if ";" in q:
        raise ValueError("Query tidak boleh berisi lebih dari satu pernyataan.")
    m = re.search(r"\bLIMIT\s+(\d+)\s*$", q, re.IGNORECASE)
    if m:
        if int(m.group(1)) > MAX_ROWS:
            q = q[: m.start(1)] + str(MAX_ROWS)
    else:
        q = f"{q} LIMIT {MAX_ROWS}"
    return q


# ---------- Query builders ----------

def q_accounts() -> str:
    return (
        "SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager, "
        "customer_client.status, customer_client.currency_code, customer_client.time_zone, "
        "customer_client.level FROM customer_client WHERE customer_client.level <= 1"
    )


def q_campaigns(include_removed: bool = False) -> str:
    where = "" if include_removed else " WHERE campaign.status != 'REMOVED'"
    return (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.primary_status, "
        "campaign.primary_status_reasons, campaign.advertising_channel_type, "
        "campaign.bidding_strategy_type, campaign_budget.amount_micros "
        f"FROM campaign{where} ORDER BY campaign.name"
    )


def q_campaign_performance(date_sql: str, only_with_spend: bool = True) -> str:
    extra = " AND metrics.impressions > 0" if only_with_spend else ""
    return (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        "campaign_budget.amount_micros, metrics.impressions, metrics.clicks, metrics.ctr, "
        "metrics.average_cpc, metrics.cost_micros, metrics.conversions, metrics.conversions_value, "
        "metrics.cost_per_conversion, metrics.search_impression_share, "
        "metrics.search_budget_lost_impression_share, metrics.search_rank_lost_impression_share "
        f"FROM campaign WHERE {date_sql}{extra} ORDER BY metrics.cost_micros DESC"
    )


def q_search_terms(date_sql: str, campaign_id: str | None = None, min_cost_micros: int = 0) -> str:
    filters = [date_sql]
    if campaign_id:
        filters.append(f"campaign.id = {int(campaign_id)}")
    if min_cost_micros:
        filters.append(f"metrics.cost_micros >= {int(min_cost_micros)}")
    return (
        "SELECT search_term_view.search_term, search_term_view.status, campaign.name, ad_group.name, "
        "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, "
        "metrics.cost_per_conversion FROM search_term_view WHERE "
        + " AND ".join(filters)
        + " ORDER BY metrics.cost_micros DESC"
    )


def q_keywords(date_sql: str, campaign_id: str | None = None) -> str:
    filters = [date_sql, "ad_group_criterion.status != 'REMOVED'", "campaign.status != 'REMOVED'"]
    if campaign_id:
        filters.append(f"campaign.id = {int(campaign_id)}")
    return (
        "SELECT campaign.name, ad_group.name, ad_group_criterion.criterion_id, "
        "ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
        "ad_group_criterion.status, ad_group_criterion.quality_info.quality_score, "
        "ad_group_criterion.quality_info.creative_quality_score, "
        "ad_group_criterion.quality_info.post_click_quality_score, "
        "ad_group_criterion.quality_info.search_predicted_ctr, metrics.impressions, metrics.clicks, "
        "metrics.average_cpc, metrics.cost_micros, metrics.conversions, metrics.cost_per_conversion "
        "FROM keyword_view WHERE " + " AND ".join(filters) + " ORDER BY metrics.cost_micros DESC"
    )


def q_conversion_actions() -> str:
    return (
        "SELECT conversion_action.id, conversion_action.name, conversion_action.status, "
        "conversion_action.type, conversion_action.category, conversion_action.origin, "
        "conversion_action.primary_for_goal, conversion_action.include_in_conversions_metric, "
        "conversion_action.counting_type, conversion_action.click_through_lookback_window_days, "
        "conversion_action.view_through_lookback_window_days, "
        "conversion_action.value_settings.default_value, "
        "conversion_action.value_settings.always_use_default_value "
        "FROM conversion_action WHERE conversion_action.status != 'REMOVED'"
    )


def q_enabled_campaign_impressions() -> str:
    return (
        "SELECT campaign.id, campaign.name, campaign.primary_status, campaign.primary_status_reasons, "
        "metrics.impressions FROM campaign WHERE campaign.status = 'ENABLED' "
        "AND segments.date DURING LAST_7_DAYS"
    )


def q_offline_upload_summary() -> str:
    return (
        "SELECT offline_conversion_upload_client_summary.client, "
        "offline_conversion_upload_client_summary.status, "
        "offline_conversion_upload_client_summary.total_event_count, "
        "offline_conversion_upload_client_summary.successful_event_count, "
        "offline_conversion_upload_client_summary.success_rate, "
        "offline_conversion_upload_client_summary.last_upload_date_time "
        "FROM offline_conversion_upload_client_summary"
    )


# ---------- Pengolah hasil ----------

def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """{'campaign': {'name': 'x'}} -> {'campaign.name': 'x'}."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(flatten(v, key))
            else:
                out[key] = v
    return out


def humanize(row: dict[str, Any]) -> dict[str, Any]:
    """Ubah *_micros jadi mata uang biasa, rapikan angka string, buang resource_name."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key.endswith("resource_name"):
            continue
        if key.endswith("_micros"):
            try:
                out[key[: -len("_micros")]] = round(int(value) / 1_000_000, 2)
            except (TypeError, ValueError):
                out[key] = value
            continue
        if key in ("metrics.average_cpc", "metrics.cost_per_conversion") and value is not None:
            out[key] = round(float(value) / 1_000_000, 2)
            continue
        if isinstance(value, str) and value.isdigit() and not key.endswith(".id") and "id" not in key.split(".")[-1]:
            out[key] = int(value)
            continue
        if isinstance(value, float):
            out[key] = round(value, 4)
            continue
        out[key] = value
    return out


def summarize(rows: Iterable[dict[str, Any]], cost_key: str = "metrics.cost", conv_key: str = "metrics.conversions") -> dict[str, Any]:
    cost = conv = clicks = impr = 0.0
    n = 0
    for r in rows:
        n += 1
        cost += float(r.get(cost_key) or 0)
        conv += float(r.get(conv_key) or 0)
        clicks += float(r.get("metrics.clicks") or 0)
        impr += float(r.get("metrics.impressions") or 0)
    return {
        "baris": n,
        "biaya": round(cost, 2),
        "klik": int(clicks),
        "tayangan": int(impr),
        "konversi": round(conv, 2),
        "biaya_per_konversi": round(cost / conv, 2) if conv else None,
        "cpc_rata2": round(cost / clicks, 2) if clicks else None,
    }
