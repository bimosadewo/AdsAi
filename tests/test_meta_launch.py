import pytest

from gads_mcp import meta_launch
from gads_mcp.meta import MetaError

SRC_SET = {"id": "S1", "attribution_spec": [{"event_type": "CLICK_THROUGH", "window_days": 7}], "name": "GTNW Mei", "campaign_id": "C0", "optimization_goal": "CONVERSATIONS",
           "billing_event": "IMPRESSIONS", "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
           "promoted_object": {"page_id": "PG", "whatsapp_phone_number": "6281"}, "destination_type": "WHATSAPP",
           "targeting": {"geo_locations": {"countries": ["ID"]}, "age_min": 18, "age_max": 65,
                         "publisher_platforms": ["facebook", "audience_network"]}}
SRC_CAMP = {"id": "C0", "name": "05 MEI 2025_GTNW", "objective": "OUTCOME_ENGAGEMENT", "special_ad_categories": []}
ADS = {"A1": {"id": "A1", "name": "Butek_14mei", "adset_id": "S1", "campaign_id": "C0",
              "creative": {"id": "CR1", "body": "Geotextile ready stock, pengalaman 13 tahun", "object_story_id": "x"}},
       "A2": {"id": "A2", "name": "Membran_140525", "adset_id": "S1", "campaign_id": "C0",
              "creative": {"id": "CR2", "body": "Geomembrane diskon 10% Mei 2025"}}}


class FakeApi:
    def __init__(self, fail_on=None):
        self.created, self.deleted, self.validated, self.fail_on = [], [], [], fail_on
        self.n = 0

    def get(self, oid, fields):
        if oid in ADS:
            return ADS[oid]
        if oid == "S1":
            return SRC_SET
        if oid == "C0":
            return SRC_CAMP
        return {"id": oid, "name": "WA_Geosintetik_Nov26", "effective_status": "PAUSED",
                "adsets": {"data": [{"name": "Geotextile", "daily_budget": "70000", "effective_status": "PAUSED"}]},
                "ads": {"data": [{"name": "Butek_14mei", "effective_status": "PAUSED"}]}}

    def create(self, act, edge, fields, validate_only=False):
        if validate_only:
            self.validated.append((edge, fields))
            return {"success": True}
        if edge == self.fail_on:
            raise MetaError("ditolak")
        self.n += 1
        self.created.append((edge, fields))
        return {"id": f"{edge}-{self.n}"}

    def delete(self, oid):
        self.deleted.append(oid)
        return {"success": True}


SPEC = [{"name": "Geotextile", "daily_budget": 70000, "ads": ["A1"], "age_min": 25, "age_max": 64,
         "gender": "pria", "placements": "feed_reels"},
        {"name": "Geomembrane", "daily_budget": 30000, "ads": ["A2"]}]


def test_plan_copies_source_and_overrides():
    l = meta_launch.Launcher(FakeApi(), "123", "IDR")
    p = l.plan("WA_Geosintetik_Nov26", SPEC)
    assert p["campaign"]["objective"] == "OUTCOME_ENGAGEMENT" and p["campaign"]["status"] == "PAUSED"
    f = p["sets"][0]["fields"]
    assert f["daily_budget"] == 70000 and f["destination_type"] == "WHATSAPP"
    assert f["promoted_object"] == {"page_id": "PG", "whatsapp_phone_number": "6281"}
    t = f["targeting"]
    assert t["age_min"] == 25 and t["genders"] == [1] and "audience_network" not in t["publisher_platforms"]
    assert t["instagram_positions"] == ["stream", "reels"] and t["geo_locations"] == {"countries": ["ID"]}
    assert p["sets"][0]["ads"][0]["fields"]["creative"] == {"creative_id": "CR1"}
    assert any("13 tahun" in n for n in p["notes"]) and any("2025" in n and "diskon" in n for n in p["notes"])
    s = l.summary(p)
    assert s["kampanye"]["total_harian"] == 100000 and s["set_iklan"][0]["target"]["gender"] == "pria"
    assert s["set_iklan"][0]["iklan"][0]["pakai_postingan_lama"] is True


def test_validate_and_execute():
    api = FakeApi()
    l = meta_launch.Launcher(api, "123", "IDR")
    p = l.plan("X", SPEC)
    assert l.validate(p) == ["kampanye", "set 'Geotextile'", "set 'Geomembrane'", "iklan"]
    assert api.validated[1][1]["campaign_id"] == "C0" and api.created == []
    out = l.execute(p)
    edges = [e for e, _ in api.created]
    assert edges == ["campaigns", "adsets", "ads", "adsets", "ads"]
    assert api.created[1][1]["campaign_id"] == out["kampanye"] and api.created[2][1]["adset_id"] == "adsets-2"
    assert l.verify(out["kampanye"])["set_iklan"][0]["budget_harian"] == 70000


def test_rollback_on_failure():
    api = FakeApi(fail_on="ads")
    l = meta_launch.Launcher(api, "123", "IDR")
    with pytest.raises(MetaError, match="dihapus lagi"):
        l.execute(l.plan("X", SPEC))
    assert api.deleted == ["campaigns-1"]


def test_input_errors_and_units():
    l = meta_launch.Launcher(FakeApi(), "123", "IDR")
    with pytest.raises(MetaError, match="daily_budget"):
        l.plan("X", [{"name": "a", "ads": ["A1"], "daily_budget": 0}])
    with pytest.raises(MetaError, match="gender"):
        l.plan("X", [{"name": "a", "ads": ["A1"], "daily_budget": 1, "gender": "x"}])
    assert meta_launch.to_minor(10.5, "USD") == 1050 and meta_launch.to_minor(70000, "IDR") == 70000
    assert meta_launch.claims("Pengalaman 11 tahun") == ["klaim '11 tahun'"]


def test_server_preview_and_readonly(monkeypatch, tmp_path):
    from fakes import FakeAds
    from mcp.server.mcpserver.exceptions import ToolError

    from gads_mcp import server
    from gads_mcp.config import Safety

    api = FakeApi()
    api.ad_accounts = lambda: [{"akun": "act_123", "nama": "TigaPilar_2023", "mata_uang": "IDR"}]
    api.campaigns = lambda act: [{"id": "C9", "name": "Lama", "effective_status": "PAUSED"}]
    api.update = lambda oid, f: {"success": True}
    monkeypatch.setattr(server, "_meta", api)
    monkeypatch.setattr(server, "_ads", FakeAds(safety=Safety(read_only=True, audit_log_dir=tmp_path)))
    prev = server.meta_create_campaign_from_proven("WA_Geosintetik_Nov26", SPEC, account="TigaPilar")
    assert prev["status"] == "PRATINJAU" and prev["kampanye"]["status"] == "DIJEDA" and not api.created
    with pytest.raises(ToolError, match="baca-saja"):
        server.meta_create_campaign_from_proven("WA_Geosintetik_Nov26", SPEC, confirm=True)
    assert not api.created
    with pytest.raises(ToolError, match="Sudah ada"):
        server.meta_create_campaign_from_proven("lama", SPEC)
    monkeypatch.setattr(server, "_ads", FakeAds(safety=Safety(audit_log_dir=tmp_path)))
    done = server.meta_create_campaign_from_proven("WA_Geosintetik_Nov26", SPEC, confirm=True)
    assert done["status"] == "DIJALANKAN" and done["terverifikasi"]["status"] == "PAUSED"
    assert (tmp_path / "meta-act_123.jsonl").exists()


def test_advantage_audience_age_as_suggestion():
    src = {**SRC_SET["targeting"], "targeting_automation": {"advantage_audience": 1}}
    t, notes = meta_launch.targeting_for(src, {"age_min": 25, "age_max": 64, "gender": "pria"})
    assert (t["age_min"], t["age_max"], t["age_range"]) == (25, 65, [25, 64]) and "saran" in notes[0]
    t, _ = meta_launch.targeting_for(src, {"age_min": 30, "age_max": 50})
    assert (t["age_min"], t["age_max"], t["age_range"]) == (25, 65, [30, 50])
    assert "saran" in meta_launch.describe_targeting(t)["umur"]
    t, _ = meta_launch.targeting_for(SRC_SET["targeting"], {"age_min": 25, "age_max": 64})
    assert (t["age_min"], t["age_max"]) == (25, 64) and "age_range" not in t


def test_validate_adsets_even_when_source_is_cbo(monkeypatch):
    api = FakeApi()
    monkeypatch.setitem(SRC_CAMP, "daily_budget", "100000")
    l = meta_launch.Launcher(api, "123", "IDR")
    checked = l.validate(l.plan("X", SPEC))
    sets = [f for e, f in api.validated if e == "adsets"]
    assert len(sets) == 2 and all("daily_budget" not in f and "bid_strategy" not in f for f in sets)
    assert "set 'Geotextile' (tanpa budget)" in checked


def test_attribution_not_copied():
    p = meta_launch.Launcher(FakeApi(), "123", "IDR").plan("X", SPEC)
    assert all("attribution_spec" not in s["fields"] for s in p["sets"])
    assert any("atribusi" in n for n in p["notes"])


ERR = ('Meta menolak permintaan (kode 100): Please update the targeting spec to remove them or replace them with '
       'alternative options. Relevant alternative options: [{"deprecated_interest_id":"6003058848156",'
       '"deprecated_interest_name":"Kreator Official","alternative_interest_id":"6003475887071",'
       '"alternative_interest_name":"Thrash metal"}] (subkode 1870247)')


def test_strip_deprecated_interests():
    assert meta_launch.deprecated_ids(ERR) == ["6003058848156"]
    t = {"geo_locations": {"countries": ["ID"]},
         "flexible_spec": [{"interests": [{"id": "6003058848156", "name": "Kreator Official"},
                                          {"id": "1", "name": "Konstruksi"}]},
                           {"interests": [{"id": "6003058848156", "name": "Kreator Official"}]}]}
    out, removed = meta_launch.strip_interests(t, ["6003058848156"])
    assert removed == ["Kreator Official", "Kreator Official"]
    assert out["flexible_spec"] == [{"interests": [{"id": "1", "name": "Konstruksi"}]}]
    assert meta_launch.describe_targeting(out)["minat"] == ["Konstruksi"]
    assert "flexible_spec" not in meta_launch.strip_interests({"flexible_spec": t["flexible_spec"][1:]},
                                                              ["6003058848156"])[0]


def test_validate_retries_after_removing_deprecated_interest(monkeypatch):
    targeting = {**SRC_SET["targeting"], "flexible_spec": [{"interests": [{"id": "6003058848156",
                                                                          "name": "Kreator Official"}]}]}
    monkeypatch.setitem(SRC_SET, "targeting", targeting)
    api = FakeApi()
    real = api.create

    def create(act, edge, fields, validate_only=False):
        if edge == "adsets" and "Kreator" in str(fields.get("targeting")):
            raise MetaError(ERR)
        return real(act, edge, fields, validate_only)

    api.create = create
    l = meta_launch.Launcher(api, "123", "IDR")
    p = l.plan("X", SPEC)
    checked = l.validate(p)
    assert "set 'Geotextile'" in checked and all("flexible_spec" not in s["fields"]["targeting"] for s in p["sets"])
    assert any("Kreator Official" in n and "tidak diganti" in n for n in p["notes"])
