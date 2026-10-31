import pytest

from gads_mcp import extensions as ext
from gads_mcp.extensions import Extensions
from gads_mcp.mutations import Mutator
from gads_mcp.safety import AuditLog

from fakes import FakeAds

CID = "5827572362"
CAMP = "22806119404"

SITELINKS = [
    {"link_text": "Harga Geotextile", "final_url": "https://tigapilar.co.id/harga",
     "description1": "Daftar harga per m2 terbaru", "description2": "Woven & non woven"},
    {"link_text": "Minta Penawaran", "final_url": "https://tigapilar.co.id/penawaran",
     "description1": "Respon cepat via WhatsApp", "description2": "Untuk proyek & kontraktor"},
]


# ---------------- validasi ----------------

def test_validate_sitelinks():
    ok, errors, warnings = ext.validate_sitelinks(SITELINKS)
    assert errors == [] and warnings == [] and ok[0]["link_text"] == "Harga Geotextile"
    _, errors, warnings = ext.validate_sitelinks([
        {"link_text": "Teks sitelink yang terlalu panjang sekali", "final_url": "bukan-url"},
        {"link_text": "Kontak", "final_url": "https://x.id/a", "description1": "hanya satu"},
        {"link_text": "kontak", "final_url": "https://x.id/a", "description1": "d" * 36, "description2": "ok"},
    ])
    joined = " | ".join(errors)
    for expected in ("terlalu panjang (41/25)", "URL tidak valid", "berpasangan", "description1 terlalu panjang (36/35)",
                     "duplikat"):
        assert expected in joined, expected
    assert any("URL yang sama" in w for w in warnings)


def test_validate_callouts():
    ok, errors, warnings = ext.validate_callouts(["Stok Ready", "Harga Pabrik", "Kirim Seluruh Indonesia"])
    assert errors == [] and warnings == []
    _, errors, warnings = ext.validate_callouts(["Gratis Ongkir.", "Promo!", "Klik Sekarang", "x" * 26, "stok ready",
                                                 "Stok Ready"])
    joined = " | ".join(errors)
    assert "titik" in joined and "terlalu panjang (26/25)" in joined and "duplikat" in joined
    assert any("ajakan" in w for w in warnings)


def test_validate_snippets_and_headers():
    ok, errors, _ = ext.validate_snippets([{"header": "jenis", "values": ["Woven", "Non Woven", "HDPE"]}])
    assert errors == [] and ok[0]["header"] == "Types"
    assert ext.normalize_header("SERVICE_CATALOG") == "Service catalog"
    assert ext.normalize_header("Layanan") == "Service catalog"
    assert ext.normalize_header("Merek") == "Brands"
    _, errors, _ = ext.validate_snippets([{"header": "Warna", "values": ["a", "b", "c"]},
                                          {"header": "Types", "values": ["a", "b"]}])
    assert "tidak dikenal" in errors[0] and "3-10" in errors[1]


def test_normalize_phone_indonesia():
    assert ext.normalize_phone("+62 812-3456-7890") == "081234567890"
    assert ext.normalize_phone("021 5550 1234") == "02155501234"
    assert ext.normalize_phone("6221 555 0123") == "0215550123"
    with pytest.raises(ValueError):
        ext.normalize_phone("123")


# ---------------- operasi ----------------

def existing_search(cid, q):
    if "FROM campaign_asset" in q:
        return [
            {"campaign_asset.field_type": "SITELINK", "campaign_asset.status": "ENABLED", "asset.id": "101",
             "asset.sitelink_asset.link_text": "Harga Geotextile", "asset.final_urls": ["https://tigapilar.co.id/harga"]},
            {"campaign_asset.field_type": "CALLOUT", "campaign_asset.status": "ENABLED", "asset.id": "201",
             "asset.callout_asset.callout_text": "Stok Ready"},
            {"campaign_asset.field_type": "CALL", "campaign_asset.status": "ENABLED", "asset.id": "301",
             "asset.call_asset.phone_number": "0812-3456-7890", "asset.call_asset.country_code": "ID"},
        ]
    if "FROM ad_group_asset" in q:
        return [{"ad_group.id": "555", "ad_group.name": "Geotextile", "ad_group_asset.field_type": "CALLOUT",
                 "ad_group_asset.status": "ENABLED", "asset.id": "202", "asset.callout_asset.callout_text": "Woven"}]
    return []


def make(tmp_path, search=existing_search):
    ads = FakeAds(search)
    return ads, Extensions(Mutator(ads, AuditLog(tmp_path)))


def test_list_extensions(tmp_path):
    _, e = make(tmp_path)
    items = e.list(CID, CAMP)
    assert [i["jenis"] for i in items] == ["SITELINK", "CALLOUT", "CALL", "CALLOUT"]
    assert items[0]["final_url"] == "https://tigapilar.co.id/harga"
    assert items[2]["telepon"] == "0812-3456-7890"
    assert items[3]["level"] == "ad_group" and items[3]["ad_group_id"] == "555"


def test_add_sitelinks_atomic_and_dedup(tmp_path):
    ads, e = make(tmp_path)
    res = e.add(CID, CAMP, "SITELINK", ext.validate_sitelinks(SITELINKS)[0], confirm=False)
    assert res["status"] == "PRATINJAU"
    assert [s["link_text"] for s in res["sesudah"]["ditambahkan"]] == ["Minta Penawaran"]  # "Harga Geotextile" sudah ada
    name, request = ads.calls[-1]
    assert name == "mutate" and request["validate_only"] is True
    asset_op, link_op = request["mutate_operations"]
    a = asset_op.asset_operation.create
    assert a.resource_name == "customers/5827572362/assets/-1"
    assert a.sitelink_asset.link_text == "Minta Penawaran" and list(a.final_urls) == ["https://tigapilar.co.id/penawaran"]
    link = link_op.campaign_asset_operation.create
    assert link.asset == a.resource_name and link.field_type.name == "SITELINK"
    assert link.campaign == "customers/5827572362/campaigns/22806119404"


def test_add_callouts_to_ad_group_level(tmp_path):
    ads, e = make(tmp_path)
    res = e.add(CID, CAMP, "CALLOUT", [{"text": "Woven"}, {"text": "Harga Pabrik"}], confirm=True, ad_group_id="555")
    assert res["status"] == "DIJALANKAN"
    ops = ads.calls[-1][1]["mutate_operations"]
    assert len(ops) == 2  # "Woven" sudah ada di grup iklan 555
    assert ops[1].ad_group_asset_operation.create.ad_group == "customers/5827572362/adGroups/555"
    assert "terverifikasi" in res


def test_add_call_is_idempotent(tmp_path):
    ads, e = make(tmp_path)
    res = e.add(CID, CAMP, "CALL", [{"phone": "081234567890", "country_code": "ID"}], confirm=False)
    assert res["status"] == "TIDAK_ADA_PERUBAHAN" and ads.calls == []
    e.add(CID, CAMP, "CALL", [{"phone": "02155501234", "country_code": "ID", "conversion_action_id": "1064530250"}],
          confirm=False)
    call = ads.calls[-1][1]["mutate_operations"][0].asset_operation.create.call_asset
    assert call.phone_number == "02155501234" and call.country_code == "ID"
    assert call.call_conversion_action == "customers/5827572362/conversionActions/1064530250"


def test_add_snippet(tmp_path):
    ads, e = make(tmp_path)
    snippets, _, _ = ext.validate_snippets([{"header": "Jenis", "values": ["Woven", "Non Woven", "HDPE"]}])
    e.add(CID, CAMP, "STRUCTURED_SNIPPET", snippets, confirm=False)
    a = ads.calls[-1][1]["mutate_operations"][0].asset_operation.create.structured_snippet_asset
    assert a.header == "Types" and list(a.values) == ["Woven", "Non Woven", "HDPE"]


def test_replace_is_atomic(tmp_path):
    ads, e = make(tmp_path)
    res = e.replace(CID, CAMP, "201", {"text": "Harga Pabrik"}, confirm=False)
    ops = ads.calls[-1][1]["mutate_operations"]
    assert ops[0].asset_operation.create.callout_asset.callout_text == "Harga Pabrik"
    assert ops[1].campaign_asset_operation.create.field_type.name == "CALLOUT"
    assert ops[2].campaign_asset_operation.remove == "customers/5827572362/campaignAssets/22806119404~201~CALLOUT"
    assert res["sebelum"]["teks"] == "Stok Ready"
    # ganti aset level grup iklan
    e.replace(CID, CAMP, "202", {"text": "Non Woven"}, confirm=False)
    ops = ads.calls[-1][1]["mutate_operations"]
    assert ops[2].ad_group_asset_operation.remove == "customers/5827572362/adGroupAssets/555~202~CALLOUT"


def test_status_and_remove(tmp_path):
    ads, e = make(tmp_path)
    e.set_status(CID, CAMP, ["301"], "paused", confirm=False)
    op = ads.calls[-1][1]["mutate_operations"][0].campaign_asset_operation
    assert op.update.status.name == "PAUSED" and list(op.update_mask.paths) == ["status"]
    e.remove(CID, CAMP, ["101", "202"], confirm=False)
    ops = ads.calls[-1][1]["mutate_operations"]
    assert ops[0].campaign_asset_operation.remove.endswith("22806119404~101~SITELINK")
    assert ops[1].ad_group_asset_operation.remove.endswith("555~202~CALLOUT")
    with pytest.raises(ValueError):
        e.remove(CID, CAMP, [], confirm=False)
    with pytest.raises(ValueError):
        e.remove(CID, CAMP, ["999"], confirm=False)
    with pytest.raises(ValueError):
        e.set_status(CID, CAMP, ["301"], "REMOVED", confirm=False)


def test_server_tools_validate_and_detect_type(tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    from gads_mcp import server
    from gads_mcp.config import Safety

    ads = FakeAds(existing_search, Safety(audit_log_dir=tmp_path))
    monkeypatch.setattr(server, "_ads", ads)

    res = server.update_extension(CAMP, "201", text="Harga Pabrik")  # 201 = callout
    assert res["status"] == "PRATINJAU" and res["sesudah"] == {"text": "Harga Pabrik"}
    with pytest.raises(ToolError):
        server.update_extension(CAMP, "201", text="Klik di sini sekarang juga ya.")  # titik di akhir
    with pytest.raises(ToolError):
        server.add_callouts(CAMP, ["Gratis Ongkir."])
    res = server.add_call_extension(CAMP, "+62 21 5550 1234")
    assert res["sesudah"]["ditambahkan"][0]["phone"] == "02155501234"
    listed = server.list_extensions(CAMP)
    assert listed["jumlah_aktif"] == {"SITELINK": 1, "CALLOUT": 2, "STRUCTURED_SNIPPET": 0, "CALL": 1}


def test_list_queries_select_campaign_id_used_in_where():
    from gads_mcp import extensions
    seen = []

    class Ads:
        def search(self, cid, q):
            seen.append(q)
            return []

    class M:
        ads = Ads()

    extensions.Extensions(M()).list("1", "24416198777")
    assert len(seen) == 2
    for q in seen:
        assert "WHERE campaign.id" in q and q.split("FROM")[0].count("campaign.id") >= 1
