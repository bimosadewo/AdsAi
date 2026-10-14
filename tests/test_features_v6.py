"""Tes fitur: shared negative list, struktur kampanye, iklan RSA."""

import pytest

from gads_mcp import rsa, structure
from gads_mcp.mutations import Mutator
from gads_mcp.safety import AuditLog

from fakes import FakeAds

CID = "5827572362"
H = ["Jual Geotextile Non Woven", "Harga Pabrik Geotextile", "Stok Ready Surabaya", "Kirim Seluruh Indonesia",
     "Gratis Konsultasi Proyek", "Geotextile 150-400 gsm", "Bisa Potong Sesuai Ukuran", "Tiga Pilar Geosintetik",
     "Minta Penawaran Hari Ini", "Melayani Proyek Pemerintah"]
D = ["Supplier geotextile woven & non woven untuk jalan, tambak, dan drainase. Minta penawaran.",
     "Stok besar, kirim cepat ke seluruh Indonesia. Konsultasi gratis dengan tim teknis kami.",
     "Tersedia berbagai gramasi dan lebar roll. Harga grosir untuk kontraktor.",
     "Hubungi kami via WhatsApp untuk harga terbaru dan sampel produk."]


# ---------------- RSA validator ----------------

def test_rsa_valid_copy():
    c = rsa.validate(H, D, "geotextile", "non-woven")
    assert c.ok, c.errors
    assert c.warnings == []


def test_rsa_hard_errors():
    c = rsa.validate(["Satu", "Dua!"], ["x" * 91], "path dengan spasi", None)
    joined = " | ".join(c.errors)
    assert "Jumlah Headline harus 3-15" in joined
    assert "tanda seru" in joined
    assert "Jumlah Deskripsi harus 2-4" in joined
    assert "terlalu panjang (91/90)" in joined
    assert "spasi" in joined
    c = rsa.validate(H[:3] + [H[0]], D[:2], None, "only-path2")
    assert any("duplikat" in e for e in c.errors)
    assert any("path2 tidak boleh" in e for e in c.errors)
    c = rsa.validate(H[:2] + ["JUAL GEOTEXTILE MURAH"], D[:2])
    assert any("kapital berlebihan" in e for e in c.errors)


def test_rsa_warnings_and_pins():
    c = rsa.validate(H[:3] + ["Supplier Terbaik di Jatim"], [{"text": D[0], "pin": 1}, D[1]])
    assert c.ok
    assert any("superlatif" in w for w in c.warnings)
    assert any("10-15 headline" in w for w in c.warnings)
    assert c.descriptions[0]["pin"] == 1
    c = rsa.validate([{"text": h, "pin": 4} for h in H[:3]], D[:2])
    assert any("pin harus" in e for e in c.errors)


def test_rsa_partial_update_skips_missing_groups():
    c = rsa.validate(None, D[:2], partial=True)
    assert c.ok


# ---------------- Struktur kampanye ----------------

def structure_data():
    return {
        "campaign": [{"campaign.id": "22806119404", "campaign.name": "Lead-Search", "campaign.status": "ENABLED",
                      "campaign.advertising_channel_type": "SEARCH", "campaign_budget.amount": 50_000.0}],
        "ad_groups": [{"ad_group.id": str(i), "ad_group.name": f"Grup {i}", "ad_group.status": "ENABLED"}
                      for i in range(1, 8)],
        "keywords": [{"ad_group.id": "1", "ad_group_criterion.criterion_id": str(k),
                      "ad_group_criterion.keyword.text": f"geotextile {k}", "ad_group_criterion.keyword.match_type": "PHRASE",
                      "ad_group_criterion.status": "ENABLED", "ad_group_criterion.quality_info.quality_score": 3}
                     for k in range(25)] + [
                     {"ad_group.id": "1", "ad_group_criterion.criterion_id": "900",
                      "ad_group_criterion.keyword.text": "gratis", "ad_group_criterion.negative": True,
                      "ad_group_criterion.status": "ENABLED"}],
        "ads": [{"ad_group.id": "1", "ad_group_ad.ad.id": "555", "ad_group_ad.ad.type": "RESPONSIVE_SEARCH_AD",
                 "ad_group_ad.status": "ENABLED", "ad_group_ad.ad_strength": "POOR",
                 "ad_group_ad.policy_summary.approval_status": "DISAPPROVED",
                 "ad_group_ad.ad.responsive_search_ad.headlines": [{"text": "A", "pinned_field": "HEADLINE_1"}, {"text": "B"}],
                 "ad_group_ad.ad.responsive_search_ad.descriptions": [{"text": "C"}]}],
        "assets": [{"campaign_asset.field_type": "SITELINK", "campaign_asset.status": "ENABLED",
                    "asset.sitelink_asset.link_text": "Harga"},
                   {"campaign_asset.field_type": "CALLOUT", "campaign_asset.status": "REMOVED",
                    "asset.callout_asset.callout_text": "lama"}],
        "negatives": [{"campaign_criterion.keyword.text": "beda", "campaign_criterion.keyword.match_type": "PHRASE"}],
        "shared_lists": [{"shared_set.id": "77", "shared_set.name": "Negatif Umum", "shared_set.member_count": 26}],
    }


def test_structure_pagination_summary_and_audit():
    s = structure.assemble(structure_data(), page=1, page_size=5)
    assert s["ringkasan"] == {"grup_iklan": 7, "grup_iklan_aktif": 7, "kata_kunci": 25, "iklan": 1,
                              "negatif_kampanye": 1, "shared_negative_list": 1}
    assert s["halaman"] == {"page": 1, "total_pages": 2, "has_more": True}
    g1 = s["grup_iklan"][0]
    assert g1["jumlah_kata_kunci"] == 25 and g1["kata_kunci"][0]["id"] == "1~0"
    assert g1["negatif_grup"][0]["text"] == "gratis"
    assert g1["iklan"][0]["headlines"][0] == {"text": "A", "pin": "HEADLINE_1"}
    assert s["ekstensi"]["sitelink"] == ["Harga"] and s["ekstensi"]["callout"] == []
    assert s["shared_negative_list"] == [{"id": "77", "nama": "Negatif Umum", "jumlah": 26}]
    msgs = " | ".join(f["pesan"] for f in s["temuan"])
    for expected in ("Sitelink baru 1", "Callout baru 0", "structured snippet", "telepon",
                     "25 kata kunci aktif", "ad strength POOR", "DITOLAK", "Quality Score ≤4", "tanpa iklan aktif"):
        assert expected in msgs, expected

    p2 = structure.assemble(structure_data(), page=2, page_size=5, include="summary")
    assert p2["halaman"]["has_more"] is False and "ekstensi" not in p2
    assert "kata_kunci" not in p2["grup_iklan"][0]
    one = structure.assemble(structure_data(), ad_group_id="1", include="ads")
    assert [g["id"] for g in one["grup_iklan"]] == ["1"] and "iklan" in one["grup_iklan"][0]
    with pytest.raises(ValueError):
        structure.assemble(structure_data(), ad_group_id="99")
    with pytest.raises(ValueError):
        structure.assemble({"campaign": []})


def test_fetch_structure_runs_all_queries():
    ads = FakeAds(lambda cid, q: structure_data()["campaign"] if "FROM campaign WHERE" in q else [])
    s = structure.fetch_structure(ads, CID, "22806119404")
    assert s["kampanye"]["nama"] == "Lead-Search"
    assert len(ads.queries) == 7
    with pytest.raises(ValueError):
        structure.fetch_structure(ads, CID, "1 OR 1=1")


# ---------------- Shared negative list ----------------

def mut(tmp_path, search):
    ads = FakeAds(search)
    return ads, Mutator(ads, AuditLog(tmp_path))


def list_search(cid, q):
    if "FROM shared_set WHERE shared_set.id" in q:
        return [{"shared_set.id": "77", "shared_set.name": "Negatif Umum", "shared_set.type": "NEGATIVE_KEYWORDS",
                 "shared_set.member_count": 1, "shared_set.reference_count": 2}]
    if "FROM shared_criterion" in q:
        return [{"shared_criterion.criterion_id": "5", "shared_criterion.keyword.text": "beda",
                 "shared_criterion.keyword.match_type": "PHRASE"}]
    if "FROM campaign_shared_set" in q:
        return [{"campaign.id": "111"}]
    if "FROM shared_set WHERE shared_set.type" in q:
        return [{"shared_set.id": "77", "shared_set.name": "Negatif Umum"}]
    return []


def test_create_list_rejects_duplicate_name(tmp_path):
    ads, m = mut(tmp_path, list_search)
    with pytest.raises(ValueError):
        m.create_negative_list(CID, "negatif umum", confirm=False)
    res = m.create_negative_list(CID, "Kompetitor", confirm=True)
    op = ads.calls[-1][1]["operations"][0]
    assert op.create.name == "Kompetitor" and op.create.type_.name == "NEGATIVE_KEYWORDS"
    assert res["shared_set_id"] == "0"  # resource palsu "res/0"


def test_add_to_list_dedupes_and_targets_set(tmp_path):
    ads, m = mut(tmp_path, list_search)
    res = m.add_to_negative_list(CID, "77", [{"text": "beda"}, {"text": "hibritex"}], confirm=False)
    assert res["sesudah"]["ditambahkan"] == [{"text": "hibritex", "match_type": "PHRASE"}]
    op = ads.calls[-1][1]["operations"][0]
    assert op.create.shared_set == "customers/5827572362/sharedSets/77"
    assert any("2 kampanye" in n for n in res["catatan"])
    assert m.add_to_negative_list(CID, "77", [{"text": "beda"}], confirm=False)["status"] == "TIDAK_ADA_PERUBAHAN"


def test_remove_from_list(tmp_path):
    ads, m = mut(tmp_path, list_search)
    m.remove_from_negative_list(CID, "77", ["Beda"], confirm=False)
    assert ads.calls[-1][1]["operations"][0].remove == "customers/5827572362/sharedCriteria/77~5"


def test_attach_and_detach_list(tmp_path):
    ads, m = mut(tmp_path, list_search)
    res = m.attach_negative_list(CID, "77", ["111", "222"], confirm=True)
    ops = ads.calls[-1][1]["operations"]
    assert len(ops) == 1 and ops[0].create.campaign == "customers/5827572362/campaigns/222"
    assert res["sesudah"] == {"ditambahkan": ["222"]} and "terverifikasi" in res
    assert m.attach_negative_list(CID, "77", ["111"], confirm=False)["status"] == "TIDAK_ADA_PERUBAHAN"
    m.detach_negative_list(CID, "77", ["111", "333"], confirm=False)
    ops = ads.calls[-1][1]["operations"]
    assert [o.remove for o in ops] == ["customers/5827572362/campaignSharedSets/111~77"]


def test_list_must_be_negative_keywords(tmp_path):
    _, m = mut(tmp_path, lambda cid, q: [{"shared_set.type": "NEGATIVE_PLACEMENTS"}] if "FROM shared_set" in q else [])
    with pytest.raises(ValueError):
        m.add_to_negative_list(CID, "1", [{"text": "x"}], confirm=False)


# ---------------- RSA create / update / status ----------------

def test_create_rsa_paused_with_pins(tmp_path):
    ads, m = mut(tmp_path, lambda cid, q: [])
    headlines = [{"text": H[0], "pin": 1}] + H[1:]
    res = m.create_rsa(CID, "555", headlines, D, ["https://tigapilar.co.id/geotextile"], confirm=False,
                       path1="geotextile", path2="non-woven")
    aga = ads.calls[-1][1]["operations"][0].create
    assert aga.status.name == "PAUSED"
    assert aga.ad_group == "customers/5827572362/adGroups/555"
    assert len(aga.ad.responsive_search_ad.headlines) == 10
    assert aga.ad.responsive_search_ad.headlines[0].pinned_field.name == "HEADLINE_1"
    assert aga.ad.responsive_search_ad.path2 == "non-woven"
    assert list(aga.ad.final_urls) == ["https://tigapilar.co.id/geotextile"]
    assert any("DIJEDA" in n for n in res["catatan"])


def test_create_rsa_rejects_invalid(tmp_path):
    ads, m = mut(tmp_path, lambda cid, q: [])
    with pytest.raises(ValueError) as ex:
        m.create_rsa(CID, "555", ["Satu", "Dua"], D, ["bukan-url"], confirm=False)
    assert "Headline" in str(ex.value) and "URL tidak valid" in str(ex.value)
    assert ads.calls == []  # tidak ada yang dikirim ke Google


def test_update_rsa_mask_and_type_guard(tmp_path):
    row = [{"ad_group_ad.ad.id": "9", "ad_group_ad.ad.type": "RESPONSIVE_SEARCH_AD", "ad_group_ad.ad_strength": "POOR",
            "ad_group_ad.ad.responsive_search_ad.headlines": [{"text": "lama"}]}]
    ads, m = mut(tmp_path, lambda cid, q: row)
    res = m.update_rsa(CID, "555", "9", confirm=True, headlines=H, final_urls=["https://tigapilar.co.id/"])
    op = ads.calls[-1][1]["operations"][0]
    assert op.update.resource_name == "customers/5827572362/ads/9"
    assert sorted(op.update_mask.paths) == ["final_urls", "responsive_search_ad.headlines"]
    assert res["sebelum"]["ad_strength"] == "POOR" and res["terverifikasi"]["ad_strength"] == "POOR"
    with pytest.raises(ValueError):
        m.update_rsa(CID, "555", "9", confirm=False)
    _, m2 = mut(tmp_path, lambda cid, q: [{"ad_group_ad.ad.type": "EXPANDED_TEXT_AD"}])
    with pytest.raises(ValueError):
        m2.update_rsa(CID, "555", "9", confirm=False, descriptions=D)


def test_set_status_ad_level(tmp_path):
    ads, m = mut(tmp_path, lambda cid, q: [])
    m.set_status(CID, "ad", ["555~9"], "ENABLED", confirm=False)
    op = ads.calls[-1][1]["operations"][0]
    assert op.update.resource_name == "customers/5827572362/adGroupAds/555~9" and op.update.status.name == "ENABLED"
    with pytest.raises(ValueError):
        m.set_status(CID, "ad", ["9"], "PAUSED", confirm=False)
