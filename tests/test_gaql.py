import pytest

from gads_mcp import gaql
from gads_mcp.config import normalize_customer_id


def test_normalize_customer_id():
    assert normalize_customer_id("582-757-2362") == "5827572362"
    assert normalize_customer_id(5827572362) == "5827572362"
    assert normalize_customer_id(None) == ""
    with pytest.raises(ValueError):
        normalize_customer_id("123")


def test_date_clause_preset_and_custom():
    assert gaql.date_clause("last_7_days") == "segments.date DURING LAST_7_DAYS"
    assert gaql.date_clause(start="2025-10-17", end="2026-10-14") == (
        "segments.date BETWEEN '2025-10-17' AND '2026-10-14'"
    )
    with pytest.raises(ValueError):
        gaql.date_clause("LAST_99_DAYS")
    with pytest.raises(ValueError):
        gaql.date_clause(start="2026-10-14")
    with pytest.raises(ValueError):
        gaql.date_clause(start="2026-10-14", end="2026-10-01")


def test_ensure_read_only():
    assert gaql.ensure_read_only("SELECT campaign.id FROM campaign").endswith("LIMIT 1000")
    assert gaql.ensure_read_only("SELECT campaign.id FROM campaign LIMIT 50").endswith("LIMIT 50")
    assert gaql.ensure_read_only("SELECT campaign.id FROM campaign LIMIT 99999").endswith("LIMIT 1000")
    for bad in ("DELETE campaign", "SELECT a FROM b; SELECT c FROM d", "  mutate x"):
        with pytest.raises(ValueError):
            gaql.ensure_read_only(bad)


def test_flatten_and_humanize():
    raw = {
        "campaign": {"id": "22806119404", "name": "Lead-Search", "resource_name": "customers/1/campaigns/2"},
        "campaign_budget": {"amount_micros": "50000000000"},
        "metrics": {"clicks": "4276", "cost_micros": "28758261260000", "average_cpc": 6725577000.0, "ctr": 0.0802123},
    }
    row = gaql.humanize(gaql.flatten(raw))
    assert row["campaign.id"] == "22806119404"  # ID tetap string
    assert row["campaign_budget.amount"] == 50000.0
    assert row["metrics.clicks"] == 4276
    assert row["metrics.cost"] == 28758261.26
    assert row["metrics.average_cpc"] == 6725.58
    assert row["metrics.ctr"] == 0.0802
    assert not any(k.endswith("resource_name") for k in row)


def test_summarize():
    rows = [
        {"metrics.cost": 5496340.10, "metrics.conversions": 835, "metrics.clicks": 4883, "metrics.impressions": 40713},
        {"metrics.cost": 3927520.84, "metrics.conversions": 596, "metrics.clicks": 1292, "metrics.impressions": 13615},
    ]
    s = gaql.summarize(rows)
    assert s["baris"] == 2
    assert s["klik"] == 6175
    assert s["biaya_per_konversi"] == round((5496340.10 + 3927520.84) / 1431, 2)
    assert gaql.summarize([])["biaya_per_konversi"] is None


@pytest.mark.parametrize(
    "builder",
    [
        gaql.q_accounts,
        gaql.q_campaigns,
        gaql.q_conversion_actions,
        gaql.q_enabled_campaign_impressions,
        gaql.q_offline_upload_summary,
        lambda: gaql.q_campaign_performance("segments.date DURING LAST_7_DAYS"),
        lambda: gaql.q_search_terms("segments.date DURING LAST_7_DAYS", "22806119404", 1_000_000),
        lambda: gaql.q_keywords("segments.date DURING LAST_7_DAYS", "22806119404"),
    ],
)
def test_builders_are_select(builder):
    q = builder()
    assert q.startswith("SELECT ")
    gaql.ensure_read_only(q)


def test_campaign_id_must_be_numeric():
    with pytest.raises(ValueError):
        gaql.q_search_terms("segments.date DURING LAST_7_DAYS", "1 OR 1=1")
