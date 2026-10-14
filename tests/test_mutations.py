import pytest

from gads_mcp.config import Safety
from gads_mcp.mutations import Mutator, normalize_keywords
from gads_mcp.safety import AuditLog, SafetyError, check_budget, to_micros

from fakes import FakeAds

CID = "5827572362"


def make(tmp_path, search=None, safety=None):
    ads = FakeAds(search, safety)
    return ads, Mutator(ads, AuditLog(tmp_path)), AuditLog(tmp_path)


def test_normalize_keywords():
    out = normalize_keywords([
        {"text": "  Beda  Geotextile ", "match_type": "phrase"},
        {"text": "beda geotextile", "match_type": "PHRASE"},
        {"text": "hibritex"},
    ])
    assert out == [{"text": "beda geotextile", "match_type": "PHRASE"}, {"text": "hibritex", "match_type": "PHRASE"}]
    with pytest.raises(ValueError):
        normalize_keywords([{"text": "x", "match_type": "FUZZY"}])
    with pytest.raises(ValueError):
        normalize_keywords([{"text": " ".join(["a"] * 11)}])


def test_check_budget_rules():
    s = Safety(max_daily_budget=300_000, max_budget_increase_factor=2)
    assert check_budget(s, 100_000, 150_000) == []
    with pytest.raises(SafetyError):
        check_budget(s, 100_000, 250_000)  # > 2x
    with pytest.raises(SafetyError):
        check_budget(s, 200_000, 350_000)  # > batas harian
    assert check_budget(s, 200_000, 50_000)  # catatan penurunan besar
    assert to_micros(50_000) == 50_000_000_000
    assert to_micros(1.234567) == 1_230_000


def test_add_negatives_preview_then_confirm(tmp_path):
    existing = [{"campaign_criterion.keyword.text": "beda", "campaign_criterion.keyword.match_type": "PHRASE"}]
    ads, m, audit = make(tmp_path, lambda cid, q: existing)
    kws = [{"text": "beda", "match_type": "PHRASE"}, {"text": "hibritex", "match_type": "PHRASE"}]

    preview = m.add_negative_keywords(CID, "22806119404", kws, confirm=False)
    assert preview["status"] == "PRATINJAU"
    assert preview["sesudah"]["ditambahkan"] == [{"text": "hibritex", "match_type": "PHRASE"}]
    assert [c[1]["validate_only"] for c in ads.calls] == [True]
    op = ads.calls[0][1]["operations"][0]
    assert op.create.negative is True
    assert op.create.keyword.text == "hibritex"
    assert op.create.campaign == "customers/5827572362/campaigns/22806119404"
    assert audit.read(CID) == []

    done = m.add_negative_keywords(CID, "22806119404", kws, confirm=True)
    assert done["status"] == "DIJALANKAN"
    assert [c[1]["validate_only"] for c in ads.calls][-2:] == [True, False]
    log = audit.read(CID)
    assert log[0]["aksi"] == "add_negative_keywords" and log[0]["hasil"] == "BERHASIL"


def test_add_negatives_nothing_new(tmp_path):
    existing = [{"campaign_criterion.keyword.text": "beda", "campaign_criterion.keyword.match_type": "PHRASE"}]
    ads, m, _ = make(tmp_path, lambda cid, q: existing)
    res = m.add_negative_keywords(CID, "1", [{"text": "beda"}], confirm=True)
    assert res["status"] == "TIDAK_ADA_PERUBAHAN"
    assert ads.calls == []


def test_remove_negatives(tmp_path):
    rows = [{"campaign_criterion.criterion_id": "777", "campaign_criterion.keyword.text": "fungsi",
             "campaign_criterion.keyword.match_type": "PHRASE"}]
    ads, m, _ = make(tmp_path, lambda cid, q: rows)
    res = m.remove_negative_keywords(CID, "22806119404", ["Fungsi"], confirm=False)
    assert res["status"] == "PRATINJAU"
    assert ads.calls[0][1]["operations"][0].remove == "customers/5827572362/campaignCriteria/22806119404~777"


def test_update_budget_guard_and_mask(tmp_path):
    row = [{"campaign.name": "Lead-Search", "campaign_budget.amount": 50_000.0,
            "campaign_budget.explicitly_shared": False, "campaign_budget.reference_count": 1}]
    ads, m, _ = make(tmp_path, lambda cid, q: row, Safety(max_daily_budget=500_000))
    with pytest.raises(SafetyError):
        m.update_budget(CID, "22806119404", 150_000, confirm=False)  # 3x
    res = m.update_budget(CID, "22806119404", 75_000, confirm=False)
    op = ads.calls[-1][1]["operations"][0]
    assert op.update.amount_micros == 75_000_000_000
    assert list(op.update_mask.paths) == ["amount_micros"]
    assert op.update.resource_name == "customers/1/campaignBudgets/99"
    assert res["sebelum"] == {"budget_harian": 50_000.0}


def test_update_budget_shared_requires_flag(tmp_path):
    row = [{"campaign.name": "X", "campaign_budget.amount": 50_000.0,
            "campaign_budget.explicitly_shared": True, "campaign_budget.reference_count": 3}]
    _, m, _ = make(tmp_path, lambda cid, q: row)
    with pytest.raises(SafetyError):
        m.update_budget(CID, "1", 60_000, confirm=False)
    assert m.update_budget(CID, "1", 60_000, confirm=False, allow_shared=True)["status"] == "PRATINJAU"


def test_read_only_blocks_writes(tmp_path):
    row = [{"campaign.name": "X", "campaign_budget.amount": 50_000.0}]
    ads, m, _ = make(tmp_path, lambda cid, q: row, Safety(read_only=True))
    with pytest.raises(SafetyError):
        m.update_budget(CID, "1", 60_000, confirm=True)
    assert ads.calls == []


def test_set_status_levels(tmp_path):
    ads, m, _ = make(tmp_path)
    m.set_status(CID, "campaign", ["23754923169"], "paused", confirm=False)
    op = ads.calls[-1][1]["operations"][0]
    assert op.update.status.name == "PAUSED" and list(op.update_mask.paths) == ["status"]
    m.set_status(CID, "keyword", ["111~222"], "ENABLED", confirm=False)
    assert ads.calls[-1][1]["operations"][0].update.resource_name == "customers/5827572362/adGroupCriteria/111~222"
    with pytest.raises(ValueError):
        m.set_status(CID, "keyword", ["222"], "PAUSED", confirm=False)
    with pytest.raises(ValueError):
        m.set_status(CID, "campaign", ["1"], "REMOVED", confirm=False)


def test_update_conversion_action(tmp_path):
    row = [{"conversion_action.name": "Scroll Depth 75", "conversion_action.type": "WEBPAGE"}]
    ads, m, _ = make(tmp_path, lambda cid, q: row)
    m.update_conversion_action(CID, "7340319524", confirm=False, primary_for_goal=False, view_through_days=1)
    op = ads.calls[-1][1]["operations"][0]
    assert op.update.primary_for_goal is False
    assert sorted(op.update_mask.paths) == ["primary_for_goal", "view_through_lookback_window_days"]
    with pytest.raises(ValueError):
        m.update_conversion_action(CID, "7340319524", confirm=False)
    with pytest.raises(ValueError):
        m.update_conversion_action(CID, "7340319524", confirm=False, view_through_days=60)


def test_update_bid_strategy(tmp_path):
    row = [{"campaign.name": "Lead-Search", "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS"}]
    ads, m, _ = make(tmp_path, lambda cid, q: row)
    m.update_bid_strategy(CID, "22806119404", "maximize_conversions", confirm=False, target_cpa=15_000)
    op = ads.calls[-1][1]["operations"][0]
    assert op.update.maximize_conversions.target_cpa_micros == 15_000_000_000
    assert list(op.update_mask.paths) == ["maximize_conversions.target_cpa_micros"]


def test_add_keywords(tmp_path):
    ads, m, _ = make(tmp_path)
    m.add_keywords(CID, "555", [{"text": "jual geotextile surabaya", "match_type": "EXACT"}],
                   confirm=False, cpc_bid=5000, paused=True)
    crit = ads.calls[-1][1]["operations"][0].create
    assert crit.keyword.match_type.name == "EXACT"
    assert crit.cpc_bid_micros == 5_000_000_000
    assert crit.status.name == "PAUSED"
