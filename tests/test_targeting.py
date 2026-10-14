from types import SimpleNamespace

import pytest

from gads_mcp import targeting as tg
from gads_mcp.mutations import Mutator
from gads_mcp.safety import AuditLog, SafetyError
from gads_mcp.targeting import Targeting

from fakes import FakeAds

CID = "5827572362"
CAMP = "22806119404"


def campaign_row(**over):
    row = {"campaign.id": CAMP, "campaign.name": "Lead-Search", "campaign.status": "ENABLED",
           "campaign.advertising_channel_type": "SEARCH", "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
           "campaign.network_settings.target_google_search": True,
           "campaign.network_settings.target_search_network": True,
           "campaign.network_settings.target_content_network": True,
           "campaign.geo_target_type_setting.positive_geo_target_type": "PRESENCE_OR_INTEREST",
           "campaign.ai_max_setting.enable_ai_max": True}
    row.update(over)
    return row


CRITERIA = [
    {"campaign_criterion.criterion_id": "2360", "campaign_criterion.type": "LOCATION",
     "campaign_criterion.location.geo_target_constant": "geoTargetConstants/2360"},
    {"campaign_criterion.criterion_id": "9001", "campaign_criterion.type": "PROXIMITY",
     "campaign_criterion.proximity.radius": 25.0, "campaign_criterion.proximity.radius_units": "KILOMETERS",
     "campaign_criterion.proximity.address.city_name": "Surabaya"},
    {"campaign_criterion.criterion_id": "7001", "campaign_criterion.type": "AD_SCHEDULE",
     "campaign_criterion.ad_schedule.day_of_week": "MONDAY", "campaign_criterion.ad_schedule.start_hour": 8,
     "campaign_criterion.ad_schedule.start_minute": "ZERO", "campaign_criterion.ad_schedule.end_hour": 17,
     "campaign_criterion.ad_schedule.end_minute": "THIRTY", "campaign_criterion.bid_modifier": 1.2},
    {"campaign_criterion.criterion_id": "30001", "campaign_criterion.type": "DEVICE",
     "campaign_criterion.device.type": "MOBILE", "campaign_criterion.bid_modifier": 1.3},
]


def search_for(criteria=CRITERIA, **campaign_over):
    def search(cid, q):
        if "FROM campaign WHERE campaign.id" in q:
            return [campaign_row(**campaign_over)]
        if "FROM campaign_criterion" in q:
            return criteria
        if "FROM geo_target_constant" in q:
            return [{"geo_target_constant.resource_name": "geoTargetConstants/2360", "geo_target_constant.id": "2360",
                     "geo_target_constant.canonical_name": "Indonesia", "geo_target_constant.target_type": "Country"}]
        if "FROM ad_group_bid_modifier" in q:
            return [{"ad_group_bid_modifier.criterion_id": "30000", "ad_group_bid_modifier.device.type": "DESKTOP",
                     "ad_group_bid_modifier.bid_modifier": 0.9, "campaign.bidding_strategy_type": "MANUAL_CPC"}]
        return []
    return search


def make(tmp_path, search=None):
    ads = FakeAds(search or search_for())
    return ads, Targeting(Mutator(ads, AuditLog(tmp_path)))


# ---------------- murni ----------------

def test_pct_conversion():
    assert tg.pct_to_modifier(20) == 1.2 and tg.pct_to_modifier(-100) == 0.0 and tg.pct_to_modifier(None) is None
    with pytest.raises(ValueError):
        tg.pct_to_modifier(950)
    assert tg.modifier_to_pct(0.0) == -100.0 and tg.modifier_to_pct(None) is None


def test_normalize_schedule():
    w = tg.normalize_schedule([{"day": "hari_kerja", "start": "08:00", "end": "17:30", "bid_pct": 10},
                               {"day": "Sabtu", "start": 9, "end": 12}])
    assert len(w) == 6 and w[0]["day"] == "MONDAY" and w[0]["end"] == (17, 30) and w[0]["bid_modifier"] == 1.1
    assert tg.fmt_window(w[5]) == "Sabtu 09:00-12:00"
    for bad in ([{"day": "senin", "start": "17:00", "end": "08:00"}],          # melewati tengah malam
                [{"day": "senin", "start": "08:10", "end": "12:00"}],          # menit tidak valid
                [{"day": "libur", "start": 8, "end": 12}],                     # hari tidak dikenal
                [{"day": "senin", "start": 8, "end": 12}, {"day": "senin", "start": 11, "end": 14}],  # tumpang tindih
                [{"day": "senin", "start": 8, "end": 12, "bid_pct": -95}]):    # di bawah -90%
        with pytest.raises(ValueError):
            tg.normalize_schedule(bad)


def test_assemble_and_audit():
    s = tg.assemble([campaign_row()], CRITERIA, search_for()("x", "FROM geo_target_constant"))
    assert s["lokasi"][0]["nama"] == "Indonesia" and s["radius"][0]["radius"] == 25.0
    assert s["jadwal"][0]["jendela"] == "Senin 08:00-17:30" and s["jadwal"][0]["penyesuaian_bid_pct"] == 20.0
    assert s["perangkat"][0] == {"criterion_id": "30001", "penyesuaian_bid_pct": 30.0, "perangkat": "MOBILE"}
    msgs = " | ".join(f["pesan"] for f in s["temuan"])
    for expected in ("Search Partners aktif", "Perluasan Display aktif", "AI Max aktif",
                     "Kehadiran atau minat", "mengabaikan penyesuaian bid"):
        assert expected in msgs, expected
    bare = tg.assemble([campaign_row(**{"campaign.network_settings.target_search_network": False,
                                         "campaign.network_settings.target_content_network": False,
                                         "campaign.ai_max_setting.enable_ai_max": False,
                                         "campaign.geo_target_type_setting.positive_geo_target_type": "PRESENCE"})],
                       [], [])
    msgs = " | ".join(f["pesan"] for f in bare["temuan"])
    assert "SELURUH DUNIA" in msgs and "Tanpa jadwal" in msgs and "Search Partners" not in msgs


# ---------------- operasi ----------------

def test_update_settings_mask(tmp_path):
    ads, t = make(tmp_path)
    res = t.update_settings(CID, CAMP, confirm=True, search_partners=False, display_expansion=False, ai_max=False,
                            location_mode="presence")
    op = ads.calls[-1][1]["operations"][0]
    assert sorted(op.update_mask.paths) == ["ai_max_setting.enable_ai_max", "geo_target_type_setting.positive_geo_target_type",
                                            "network_settings.target_content_network", "network_settings.target_search_network"]
    assert op.update.geo_target_type_setting.positive_geo_target_type.name == "PRESENCE"
    assert res["sebelum"]["mode_lokasi"] == "PRESENCE_OR_INTEREST" and "terverifikasi" in res
    with pytest.raises(ValueError):
        t.update_settings(CID, CAMP, confirm=False)
    with pytest.raises(ValueError):
        t.update_settings(CID, CAMP, confirm=False, location_mode="NEARBY")


def test_resolve_locations(tmp_path):
    ads, t = make(tmp_path)
    geo = SimpleNamespace(id=1007700, name="Surabaya", canonical_name="Surabaya,East Java,Indonesia",
                          target_type="City", country_code="ID")
    ads.extras["suggest_geo_target_constants"] = SimpleNamespace(geo_target_constant_suggestions=[
        SimpleNamespace(search_term="Surabaya", geo_target_constant=geo, reach=5_000_000)])
    out = t.resolve_locations(["Surabaya"])
    assert out == [{"query": "Surabaya", "geo_id": "1007700", "nama": "Surabaya,East Java,Indonesia", "jenis": "City",
                    "negara": "ID", "jangkauan": 5_000_000}]
    assert ads.calls[-1][1]["country_code"] == "ID" and ads.calls[-1][1]["location_names"] == {"names": ["Surabaya"]}


def test_add_and_exclude_locations(tmp_path):
    ads, t = make(tmp_path)
    assert t.add_locations(CID, CAMP, ["2360"], confirm=False)["status"] == "TIDAK_ADA_PERUBAHAN"
    t.add_locations(CID, CAMP, ["geoTargetConstants/1007700"], confirm=False, bid_pct=20)
    crit = ads.calls[-1][1]["operations"][0].create
    assert crit.location.geo_target_constant == "geoTargetConstants/1007700" and crit.bid_modifier == pytest.approx(1.2)
    res = t.add_locations(CID, CAMP, ["21345"], confirm=False, exclude=True, bid_pct=50)
    crit = ads.calls[-1][1]["operations"][0].create
    assert crit.negative is True and "bid_modifier" not in crit  # pengecualian tanpa modifier
    assert res["aksi"] == "exclude_locations"


def test_add_proximity(tmp_path):
    ads, t = make(tmp_path)
    t.add_proximity(CID, CAMP, 30, confirm=False, latitude=-7.2575, longitude=112.7521)
    p = ads.calls[-1][1]["operations"][0].create.proximity
    assert p.geo_point.latitude_in_micro_degrees == -7257500 and p.radius_units.name == "KILOMETERS"
    t.add_proximity(CID, CAMP, 10, confirm=False, street_address="Jl. Rungkut Industri", city_name="Surabaya")
    p = ads.calls[-1][1]["operations"][0].create.proximity
    assert p.address.city_name == "Surabaya" and p.address.country_code == "ID"
    for kwargs in ({"radius_km": 0.5, "city_name": "X"}, {"radius_km": 10}, {"radius_km": 10, "latitude": 99, "longitude": 1}):
        with pytest.raises(ValueError):
            t.add_proximity(CID, CAMP, confirm=False, **kwargs)


def test_remove_criteria_guards_worldwide(tmp_path):
    ads, t = make(tmp_path)
    with pytest.raises(SafetyError):
        t.remove_criteria(CID, CAMP, ["2360", "9001"], confirm=False)
    res = t.remove_criteria(CID, CAMP, ["2360"], confirm=False)  # radius masih ada
    assert ads.calls[-1][1]["operations"][0].remove == "customers/5827572362/campaignCriteria/22806119404~2360"
    res = t.remove_criteria(CID, CAMP, ["7001"], confirm=False)
    assert any("24 jam" in n for n in res["catatan"])
    with pytest.raises(ValueError):
        t.remove_criteria(CID, CAMP, ["30001"], confirm=False)  # perangkat tidak bisa dihapus


def test_set_schedule_replaces_atomically(tmp_path):
    ads, t = make(tmp_path)
    res = t.set_schedule(CID, CAMP, [{"day": "hari_kerja", "start": "08:00", "end": "17:00"},
                                     {"day": "sabtu", "start": "08:00", "end": "12:00", "bid_pct": -20}], confirm=False)
    name, req = ads.calls[-1]
    ops = req["mutate_operations"]
    assert name == "mutate" and len(ops) == 7
    assert ops[0].campaign_criterion_operation.remove.endswith("~7001")      # jadwal lama dilepas
    s = ops[1].campaign_criterion_operation.create.ad_schedule
    assert s.day_of_week.name == "MONDAY" and s.start_hour == 8 and s.end_minute.name == "ZERO"
    assert ops[6].campaign_criterion_operation.create.bid_modifier == pytest.approx(0.8)
    assert res["sebelum"] == ["Senin 08:00-17:30"] and "Sabtu 08:00-12:00 (-20%)" in res["sesudah"]
    assert any("Minggu" in n for n in res["catatan"]) and any("Smart Bidding" in n for n in res["catatan"])
    with pytest.raises(ValueError):
        t.set_schedule(CID, CAMP, [], confirm=False)


def test_device_adjustments_campaign_and_ad_group(tmp_path):
    ads, t = make(tmp_path)
    res = t.set_devices(CID, confirm=False, adjustments={"mobile": 30, "desktop": -20}, campaign_id=CAMP)
    ops = ads.calls[-1][1]["operations"]
    upd = [o for o in ops if o.update.resource_name][0]
    assert upd.update.resource_name.endswith("~30001") and upd.update.bid_modifier == pytest.approx(1.3)
    new = [o for o in ops if not o.update.resource_name][0]
    assert new.create.device.type_.name == "DESKTOP" and new.create.bid_modifier == pytest.approx(0.8)
    assert any("Smart Bidding" in n for n in res["catatan"])
    t.set_devices(CID, confirm=False, adjustments={"desktop": 10, "tablet": -100}, ad_group_id="555")
    ops = ads.calls[-1][1]["operations"]
    assert ops[0].update.resource_name == "customers/5827572362/adGroupBidModifiers/555~30000"
    assert ops[1].create.ad_group == "customers/5827572362/adGroups/555" and ops[1].create.bid_modifier == 0.0
    with pytest.raises(SafetyError):
        t.set_devices(CID, confirm=False, adjustments={"mobile": -100, "desktop": -100, "tablet": -100}, campaign_id=CAMP)
    with pytest.raises(ValueError):
        t.set_devices(CID, confirm=False, adjustments={"tv": 10}, campaign_id=CAMP)
    with pytest.raises(ValueError):
        t.set_devices(CID, confirm=False, adjustments={"mobile": 10})
