import pytest

from gads_mcp import goals
from gads_mcp.mutations import Mutator
from gads_mcp.safety import AuditLog

from fakes import FakeAds

CID = "5827572362"
ACTIONS = [
    {"conversion_action.name": "KlikWhatsapp", "conversion_action.category": "CONTACT",
     "conversion_action.origin": "WEBSITE", "conversion_action.primary_for_goal": True},
    {"conversion_action.name": "Form-Kontak", "conversion_action.category": "SUBMIT_LEAD_FORM",
     "conversion_action.origin": "WEBSITE", "conversion_action.primary_for_goal": True},
    {"conversion_action.name": "Scroll", "conversion_action.category": "PAGE_VIEW",
     "conversion_action.origin": "WEBSITE", "conversion_action.primary_for_goal": False},
]


def goal(prefix, cat, biddable=True):
    return {f"{prefix}.category": cat, f"{prefix}.origin": "WEBSITE", f"{prefix}.biddable": biddable}


def search_for(level="CAMPAIGN", camp_goals=("PAGE_VIEW",), acct_goals=("CONTACT", "SUBMIT_LEAD_FORM")):
    def search(cid, q):
        if "FROM campaign WHERE" in q:
            return [{"campaign.id": "22806119404", "campaign.name": "Lead-Search",
                     "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS"}]
        if "FROM conversion_goal_campaign_config" in q:
            return [{"conversion_goal_campaign_config.goal_config_level": level}]
        if "FROM campaign_conversion_goal" in q:
            return [goal("campaign_conversion_goal", c) for c in camp_goals] + \
                   [goal("campaign_conversion_goal", "PURCHASE", biddable=False)]
        if "FROM customer_conversion_goal" in q:
            return [goal("customer_conversion_goal", c) for c in acct_goals]
        if "FROM conversion_action" in q:
            return ACTIONS
        return []
    return search


def test_assess_detects_campaign_goal_without_primary():
    data = {k: search_for()(CID, q) for k, q in goals.queries(CID, "22806119404").items()}
    res = goals.assess(data)
    assert res["status"] == "TANPA_KONVERSI_UTAMA" and res["sumber_sasaran"] == "khusus kampanye"
    assert "use_account_conversion_goals" in res["pesan"]
    assert res["sasaran_dipakai"] == [{"kategori": "PAGE_VIEW", "sumber": "WEBSITE", "konversi_utama": []}]
    ok = goals.assess({k: search_for(level="CUSTOMER", camp_goals=("CONTACT", "PAGE_VIEW"))(CID, q)
                       for k, q in goals.queries(CID, "1").items()})
    assert ok["status"] == "OK" and ok["sasaran_kosong"] == ["PAGE_VIEW (WEBSITE)"]
    assert goals.assess({"kampanye": [{}]})["status"] == "TIDAK_LENGKAP"


def test_use_account_goals_preview_and_mutation(tmp_path):
    ads = FakeAds(search_for())
    g = goals.Goals(Mutator(ads, AuditLog(tmp_path)))
    prev = g.use_account_goals(CID, "22806119404", confirm=False)
    assert prev["status"] == "PRATINJAU" and "KlikWhatsapp" in prev["catatan"][0]
    name, req = ads.calls[-1]
    assert name == "mutate_conversion_goal_campaign_configs" and req["validate_only"] is True
    op = req["operations"][0]
    assert op.update.resource_name == f"customers/{CID}/conversionGoalCampaignConfigs/22806119404"
    assert op.update.goal_config_level == ads.client.enums.GoalConfigLevelEnum.CUSTOMER
    assert list(op.update_mask.paths) == ["goal_config_level"]
    done = g.use_account_goals(CID, "22806119404", confirm=True)
    assert done["status"] == "DIJALANKAN" and "terverifikasi" in done


def test_use_account_goals_guards(tmp_path):
    same = goals.Goals(Mutator(FakeAds(search_for(level="CUSTOMER")), AuditLog(tmp_path)))
    assert same.use_account_goals(CID, "1", confirm=True)["status"] == "TIDAK_BERUBAH"
    empty = goals.Goals(Mutator(FakeAds(search_for(acct_goals=("PAGE_VIEW",))), AuditLog(tmp_path)))
    with pytest.raises(ValueError, match="update_conversion_action"):
        empty.use_account_goals(CID, "1", confirm=False)
