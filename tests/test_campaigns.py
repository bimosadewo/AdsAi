import pytest
from google.ads.googleads.client import GoogleAdsClient
from google.oauth2.credentials import Credentials

from gads_mcp import campaigns

H = ["Toko Geotextile Terdekat", "Gudang di Tangerang", "Datang Langsung ke Gudang"]
D = ["Gudang geosintetik di Tangerang. Datang langsung, cek barang.", "Stok lengkap, harga pabrik."]
SPEC = {"name": "TP Search Lokal Jabodetabek (Maps)", "daily_budget": 30000, "geo_ids": ["20440", "1007716"],
        "bidding": "MAXIMIZE_CLICKS", "max_cpc": 6000, "final_urls": ["https://www.tigapilar.co.id/contact/"],
        "headlines": H, "descriptions": D, "path1": "gudang", "path2": "tangerang",
        "negative_keywords": ["lowongan", "pdf"],
        "schedule": [{"day": "hari_kerja", "start": "08:00", "end": "17:00"},
                     {"day": "Sabtu", "start": "09:00", "end": "15:00"}],
        "ad_groups": [{"name": "Geotextile Lokal", "keywords": ["jual geotextile terdekat",
                                                                {"text": "toko geotextile", "match_type": "EXACT"}]},
                      {"name": "Plastik Cor Lokal", "keywords": ["jual plastik cor tangerang"]}]}


class FakeMutator:
    def __init__(self):
        self.gc = GoogleAdsClient(Credentials(token="x"), developer_token="x", version="v25", use_proto_plus=True)

    def _fill_assets(self, repeated, items, pin_map):
        for it in items:
            a = self.gc.get_type("AdTextAsset")
            a.text = it["text"]
            repeated.append(a)


def kinds(ops):
    return [type(o).pb(o).WhichOneof("operation") for o in ops]


def test_plan_rejects_unsafe_or_incomplete_specs():
    with pytest.raises(ValueError) as ex:
        campaigns.plan({**SPEC, "geo_ids": [], "daily_budget": 0, "ad_groups": []})
    msg = str(ex.value)
    assert "geo_ids" in msg and "daily_budget" in msg and "ad_groups" in msg


def test_operations_are_one_atomic_search_only_paused_request():
    p = campaigns.plan(SPEC)
    ops = campaigns.CampaignBuilder(FakeMutator()).operations("5827572362", p)
    k = kinds(ops)
    assert k[0] == "campaign_budget_operation" and k[1] == "campaign_operation"
    c = ops[1].campaign_operation.create
    assert c.status.name == "PAUSED" and c.advertising_channel_type.name == "SEARCH"
    assert c.network_settings.target_google_search and not c.network_settings.target_search_network
    assert not c.network_settings.target_content_network
    assert c.geo_target_type_setting.positive_geo_target_type.name == "PRESENCE"
    assert type(c).pb(c).WhichOneof("campaign_bidding_strategy") == "target_spend"
    assert c.target_spend.cpc_bid_ceiling_micros == 6_000 * 1_000_000
    crits = [o.campaign_criterion_operation.create for o in ops if kinds([o])[0] == "campaign_criterion_operation"]
    assert sum(1 for x in crits if x.location.geo_target_constant) == 2
    assert sum(1 for x in crits if x.language.language_constant) == 2
    assert sum(1 for x in crits if x.ad_schedule.start_hour or x.ad_schedule.end_hour) == 6   # 5 hari kerja + Sabtu
    assert sum(1 for x in crits if x.negative) == 2
    assert k.count("ad_group_operation") == 2 and k.count("ad_group_criterion_operation") == 3
    assert k.count("ad_group_ad_operation") == 2
    ad = [o.ad_group_ad_operation.create for o in ops if kinds([o])[0] == "ad_group_ad_operation"][0]
    assert ad.ad.final_urls[0] == "https://www.tigapilar.co.id/contact/" and len(ad.ad.responsive_search_ad.headlines) == 3


def test_maximize_conversions_sets_oneof():
    p = campaigns.plan({**SPEC, "bidding": "MAXIMIZE_CONVERSIONS"})
    c = campaigns.CampaignBuilder(FakeMutator()).operations("1", p)[1].campaign_operation.create
    assert type(c).pb(c).WhichOneof("campaign_bidding_strategy") == "maximize_conversions"


def test_location_group_links_only_that_group():
    p = campaigns.plan({**SPEC, "location_asset_set_id": "9099504071"})
    ops = campaigns.CampaignBuilder(FakeMutator()).operations("1935434488", p)
    links = [o.campaign_asset_set_operation.create for o in ops if o.campaign_asset_set_operation.create.asset_set]
    assert [l.asset_set for l in links] == ["customers/1935434488/assetSets/9099504071"]
    assert links[0].campaign == "customers/1935434488/campaigns/-2"
    assert "9099504071" in campaigns.summary(p)["aset_lokasi_maps"]
    assert not [o for o in campaigns.CampaignBuilder(FakeMutator()).operations("1", campaigns.plan(SPEC))
                if o.campaign_asset_set_operation.create.asset_set]
    with pytest.raises(ValueError, match="location_asset_set_id"):
        campaigns.plan({**SPEC, "location_asset_set_id": "abc"})
