"""Putaran 7 (dokumentasi Adspirer): penerjemah error, log kegagalan, strategi klien, diagnosis, bid & hapus
kata kunci."""

import importlib
from datetime import date

import pytest
from google.ads.googleads.errors import GoogleAdsException

from gads_mcp import client as client_mod
from gads_mcp import diagnostics, errors, strategy
from gads_mcp.analysis import Alert
from gads_mcp.config import Safety
from gads_mcp.mutations import Mutator
from gads_mcp.safety import AuditLog, FailureLog, SafetyError

from fakes import FakeAds

CID = "5827572362"


def _gads_exception(ads, *codes):
    failure = ads.client.get_type("GoogleAdsFailure")
    for kind, name in codes:
        err = ads.client.get_type("GoogleAdsError")
        module = importlib.import_module(f"google.ads.googleads.v25.errors.types.{kind}")
        enum = getattr(getattr(module, _enum(kind)), _enum(kind)[:-4])
        setattr(err.error_code, kind, enum[name])
        err.message = f"pesan {name}"
        failure.errors.append(err)
    return GoogleAdsException(None, None, failure, "req-1")


def _enum(kind):
    return "".join(p.capitalize() for p in kind.split("_")) + "Enum"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

def test_hint_codes_exist_in_api():
    """Setiap kode di HINTS/RETRYABLE harus nama enum yang benar-benar ada di Google Ads API."""
    import importlib
    import pkgutil

    import google.ads.googleads.v25.errors.types as types
    names = set()
    for m in pkgutil.iter_modules(types.__path__):
        mod = importlib.import_module(f"{types.__name__}.{m.name}")
        for attr in dir(mod):
            for inner in dir(getattr(mod, attr)):
                e = getattr(getattr(mod, attr), inner, None)
                if hasattr(e, "__members__"):
                    names |= set(e.__members__)
    assert not (set(errors.HINTS) | errors.RETRYABLE) - names


def test_describe_adds_hints_and_codes():
    ads = FakeAds()
    ex = _gads_exception(ads, ("context_error", "OPERATION_NOT_PERMITTED_FOR_CONTEXT"))
    err = client_mod.ads_error(ex)
    assert err.codes == ["OPERATION_NOT_PERMITTED_FOR_CONTEXT"]
    assert "request_id=req-1" in str(err) and "Saran:" in str(err) and "portofolio" in str(err)


def test_retry_only_transient():
    ads = FakeAds()
    sleeps = []
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _gads_exception(ads, ("database_error", "CONCURRENT_MODIFICATION"))
        return "ok"

    assert client_mod.with_retry(flaky, sleep=sleeps.append) == "ok"
    assert sleeps == [1.0, 3.0]

    def permanent():
        attempts["n"] += 1
        raise _gads_exception(ads, ("authorization_error", "USER_PERMISSION_DENIED"))

    attempts["n"] = 0
    with pytest.raises(client_mod.AdsError) as info:
        client_mod.with_retry(permanent, sleep=sleeps.append)
    assert attempts["n"] == 1 and "MCC" in str(info.value)


def test_explain_text():
    out = errors.explain("authorization_error: USER_PERMISSION_DENIED blah")
    assert out["kode_dikenali"] == ["USER_PERMISSION_DENIED"]
    assert "tidak dikenali" in errors.explain("something odd")["saran"][0]


def test_failure_log_and_why(tmp_path):
    log = FailureLog(tmp_path)
    log.write("update_budget", {"customer_id": CID, "campaign_id": "1", "confirm": True}, "x", ["CONCURRENT_MODIFICATION"])
    log.write("list_campaigns", {"customer_id": CID}, "Login OAuth gagal (invalid_grant)")
    entries = log.read(5)
    assert [e["tool"] for e in entries] == ["list_campaigns", "update_budget"]
    assert entries[1]["confirm"] is True and "confirm" not in entries[1]["argumen"]
    assert log.read(5, tool="update_budget")[0]["kode"] == ["CONCURRENT_MODIFICATION"]
    why = diagnostics.why_failed(entries)
    assert "LOGIN Google Ads" in why["ringkasan"] and why["kegagalan"][1]["saran"]
    assert diagnostics.why_failed([])["kegagalan"] == []


def test_server_records_failures(tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    from gads_mcp import server

    monkeypatch.setattr(server, "_ads", FakeAds(safety=Safety(audit_log_dir=tmp_path)))
    with pytest.raises(ToolError):
        server.remove_keywords(ids=["bukan-format"], customer_id=CID)
    res = server.why_did_this_fail()
    assert res["kegagalan"][0]["tool"] == "remove_keywords"
    assert "ad_group_id~criterion_id" in res["kegagalan"][0]["pesan"]


# ---------------------------------------------------------------------------
# strategi
# ---------------------------------------------------------------------------

def test_strategy_roundtrip(tmp_path):
    text = strategy.template("PT X")
    text = strategy.add_directive(text, strategy.build_directive("avoid", '"gratis"', "tidak ada sampel", "Google Ads"),
                                  "gratis", date(2026, 10, 14))
    text = strategy.add_directive(text, strategy.build_directive("PREFER", '"geotextile" & "geomembran"', "margin",
                                                                 "Google Ads"), "produk", date(2026, 10, 15))
    parsed = strategy.parse(text)
    assert [d.jenis for d in parsed["arahan"]] == ["AVOID", "PREFER"]
    assert parsed["arahan"][0].alasan == "tidak ada sampel"
    assert parsed["keputusan"][0] == "2026-10-15 — produk"
    assert strategy.protected_terms(parsed["arahan"]) == ["geomembran", "geotextile"]
    assert "[No active directives]" in text.split("### Google Ads")[0]        # bagian lain tetap kosong
    assert "[No active directives]" not in text.split("### Google Ads")[1].split("###")[0]
    with pytest.raises(ValueError):
        strategy.build_directive("MAYBE", "x", "y", "Google Ads")
    with pytest.raises(ValueError):
        strategy.build_directive("AVOID", "x", "", "Google Ads")


def test_strategy_drift():
    ds = strategy.parse("## Active Directives\n### Google Ads\n- AVOID: \"gratis\" — x\n")["arahan"]
    rows = [{"search_term_view.search_term": "geotextile gratis", "metrics.cost": 30000},
            {"search_term_view.search_term": "gratisan", "metrics.cost": 9000},          # bukan kata utuh
            {"search_term_view.search_term": "sampel gratis", "metrics.cost": 0}]
    out = strategy.drift(rows, ds)
    assert [d["search_term"] for d in out] == ["geotextile gratis"]


def test_add_strategy_directive_tool(tmp_path, monkeypatch):
    from gads_mcp import server
    from gads_mcp.clients import Client
    from gads_mcp.service import Context

    monkeypatch.setenv("ADSAI_STRATEGY_DIR", str(tmp_path / "strategi"))
    cl = Client("tigapilar", "PT Tiga Pilar", CID)
    ads = FakeAds(safety=Safety(audit_log_dir=tmp_path))
    monkeypatch.setattr(server, "ctx", lambda: Context(ads.settings, ads, {"tigapilar": cl}, AuditLog(tmp_path)))
    prev = server.add_strategy_directive("tigapilar", "AVOID", '"gratis"', "tidak melayani sampel")
    assert prev["status"] == "PRATINJAU" and not (tmp_path / "strategi" / "tigapilar.md").exists()
    done = server.add_strategy_directive("tigapilar", "AVOID", '"gratis"', "tidak melayani sampel", confirm=True)
    assert done["status"] == "DISIMPAN" and done["terverifikasi"][0]["istilah"] == ["gratis"]
    again = server.add_strategy_directive("tigapilar", "AVOID", '"gratis"', "tidak melayani sampel", confirm=True)
    assert again["status"] == "TIDAK_BERUBAH"
    assert server.client_strategy("tigapilar")["arahan"][0]["jenis"] == "AVOID"


# ---------------------------------------------------------------------------
# diagnosis
# ---------------------------------------------------------------------------

def test_next_actions_order():
    alerts = [Alert("INFO", "PACING_KURANG", "pacing"), Alert("KRITIS", "KONVERSI_NOL", "nol"),
              Alert("PERINGATAN", "ANOMALI_BIAYA", "lonjakan")]
    drift = [{"biaya": 30000.0}]
    out = diagnostics.next_actions(alerts, "STABIL", drift)
    assert [o["prioritas"] for o in out] == ["KRITIS", "PERINGATAN", "PERINGATAN", "INFO", "RUTIN"]
    assert out[0]["tool"].startswith("conversion_actions") and "AVOID" in out[1]["alasan"]
    assert diagnostics.next_actions([], "BERTUMBUH")[0]["prioritas"] == "RUTIN"


def test_assess_live():
    base = {"kampanye": [{"campaign.id": "1", "campaign.name": "Lead", "campaign.status": "ENABLED",
                          "campaign.primary_status": "ELIGIBLE", "campaign.advertising_channel_type": "SEARCH",
                          "campaign_budget.amount": 50000.0}],
            "grup": [{"ad_group.id": "9"}],
            "iklan": [{"ad_group_ad.policy_summary.approval_status": "APPROVED"}],
            "kata_kunci": [{"ad_group_criterion.criterion_id": "5"}],
            "kemarin": [{"metrics.impressions": 120, "metrics.cost": 40000.0}]}
    assert diagnostics.assess_live(base)["vonis"] == "TAYANG"
    bad = {**base, "kampanye": [{**base["kampanye"][0], "campaign.primary_status": "NOT_ELIGIBLE",
                                 "campaign.primary_status_reasons": ["CAMPAIGN_PENDING_BILLING"]}],
           "iklan": [{"ad_group_ad.policy_summary.approval_status": "DISAPPROVED"}]}
    res = diagnostics.assess_live(bad)
    assert res["vonis"] == "TIDAK_TAYANG" and len(res["saran"]) == 2
    assert "CAMPAIGN_PENDING_BILLING" in res["cek"][2]["nilai"]
    assert diagnostics.assess_live({"kampanye": []})["vonis"] == "TIDAK_DITEMUKAN"


# ---------------------------------------------------------------------------
# kata kunci: bid & hapus
# ---------------------------------------------------------------------------

def kw_search(strategy_type="MANUAL_CPC", bid=2000.0):
    def search(cid, q):
        if "FROM ad_group_criterion" in q:
            return [{"ad_group.id": "11", "ad_group_criterion.criterion_id": "22",
                     "ad_group_criterion.keyword.text": "geotextile", "ad_group_criterion.keyword.match_type": "PHRASE",
                     "ad_group_criterion.cpc_bid": bid, "campaign.name": "Lead",
                     "campaign.bidding_strategy_type": strategy_type}]
        return []
    return search


def test_update_keyword_bids(tmp_path):
    ads = FakeAds(kw_search())
    m = Mutator(ads, AuditLog(tmp_path))
    res = m.update_keyword_bids(CID, [{"id": "11~22", "cpc_bid": 3000}], confirm=True)
    assert res["status"] == "DIJALANKAN" and res["sebelum"][0]["cpc_bid"] == 2000.0
    op = ads.calls[-1][1]["operations"][0]
    assert op.update.cpc_bid_micros == 3_000_000_000 and list(op.update_mask.paths) == ["cpc_bid_micros"]
    with pytest.raises(SafetyError):
        m.update_keyword_bids(CID, [{"id": "11~22", "cpc_bid": 5000}], confirm=False)   # > 2x
    with pytest.raises(ValueError):
        m.update_keyword_bids(CID, [{"id": "11~99", "cpc_bid": 1000}], confirm=False)   # tidak ada
    smart = Mutator(FakeAds(kw_search("MAXIMIZE_CONVERSIONS")), AuditLog(tmp_path))
    assert "DIABAIKAN" in smart.update_keyword_bids(CID, [{"id": "11~22", "cpc_bid": 2500}], False)["catatan"][0]


def test_remove_keywords_needs_confirm_delete(tmp_path):
    ads = FakeAds(kw_search())
    m = Mutator(ads, AuditLog(tmp_path))
    prev = m.remove_keywords(CID, ["11~22"], confirm=False)
    assert prev["status"] == "PRATINJAU" and prev["sebelum"][0]["kata_kunci"] == "geotextile"
    with pytest.raises(SafetyError):
        m.remove_keywords(CID, ["11~22"], confirm=True)
    done = m.remove_keywords(CID, ["11~22"], confirm=True, confirm_delete=True)
    assert done["status"] == "DIJALANKAN"
    assert ads.calls[-1][1]["operations"][0].remove.endswith("adGroupCriteria/11~22")
    assert AuditLog(tmp_path).read(CID)[0]["aksi"] == "remove_keywords"
