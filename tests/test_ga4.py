from datetime import date

import pytest

from gads_mcp import ga4
from test_gbp import Resp, Session

REPORT = {
    "dimensionHeaders": [{"name": "sessionDefaultChannelGroup"}],
    "metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"}, {"name": "engagementRate", "type": "TYPE_FLOAT"},
                      {"name": "keyEvents", "type": "TYPE_FLOAT"}],
    "rows": [{"dimensionValues": [{"value": "Paid Search"}],
              "metricValues": [{"value": "100"}, {"value": "0.5"}, {"value": "10"}]},
             {"dimensionValues": [{"value": "Organic Search"}],
              "metricValues": [{"value": "50"}, {"value": "0.7"}, {"value": "2"}]}],
}


def test_rows_parse_numbers():
    r = ga4.rows(REPORT)
    assert r[0] == {"sessionDefaultChannelGroup": "Paid Search", "sessions": 100, "engagementRate": 0.5,
                    "keyEvents": 10}
    assert ga4.rows({}) == []


def test_date_range_ends_yesterday():
    assert ga4.date_range(7, date(2026, 10, 27)) == ("2026-10-20", "2026-10-26")
    with pytest.raises(ga4.Ga4Error):
        ga4.date_range(0)


def test_pick_property():
    props = [{"properti": "properties/1", "nama": "Tiga Pilar", "akun": "Budi"},
             {"properti": "properties/2", "nama": "RS Sari Asih", "akun": "RSSA"}]
    assert ga4.pick_property(props, "sari")["properti"] == "properties/2"
    assert ga4.pick_property(props, "1")["properti"] == "properties/1"
    with pytest.raises(ga4.Ga4Error, match="beberapa"):
        ga4.pick_property(props, None)
    with pytest.raises(ga4.Ga4Error, match="Viewer"):
        ga4.pick_property([], None)


def test_findings():
    channels = ga4.rows(REPORT)
    pages = [{"landingPagePlusQueryString": "/plastik-cor-tangerang/", "sessions": 80, "engagementRate": 0.3}]
    events = [{"eventName": "KlikWhatsapp", "eventCount": 31}]
    f = ga4.overview_findings(channels, pages, events)
    assert "67% kunjungan" in f[0]
    assert any("Paid Search" in x and "10.0%" in x for x in f)
    assert any("/plastik-cor-tangerang/" in x for x in f) and any("KlikWhatsapp 31x" in x for x in f)
    no_key = [{"sessionDefaultChannelGroup": "Direct", "sessions": 10, "keyEvents": 0}]
    assert any("key event" in x for x in ga4.overview_findings(no_key, [], []))


def test_errors_and_missing_token():
    api = ga4.Ga4(Session({("GET", "/accountSummaries"): (403, {"error": {"message": "denied"}})}))
    with pytest.raises(ga4.Ga4Error, match="Viewer"):
        api.properties()
    assert "API belum aktif" in ga4.explain(403, {"error": {"message": "SERVICE_DISABLED"}})

    class S:
        ga4_refresh_token = ""
    with pytest.raises(ga4.Ga4Error, match="GA4_REFRESH_TOKEN"):
        ga4.Ga4.from_settings(S())


def _server(monkeypatch, tmp_path, session):
    from fakes import FakeAds

    from gads_mcp import server
    from gads_mcp.config import Safety

    monkeypatch.setattr(server, "_ads", FakeAds(safety=Safety(audit_log_dir=tmp_path)))
    monkeypatch.setattr(server, "_ga4", ga4.Ga4(session))
    return server


def test_server_overview_and_report(monkeypatch, tmp_path):
    session = Session({
        ("GET", "/accountSummaries"): {"accountSummaries": [{"displayName": "Budi", "propertySummaries": [
            {"property": "properties/77", "displayName": "Tiga Pilar"}]}]},
        ("POST", "/properties/77:runReport"): REPORT,
    })
    server = _server(monkeypatch, tmp_path, session)
    out = server.ga4_overview(days=7)
    assert out["properti"] == "properties/77" and out["total"]["kunjungan"] == 150 and out["temuan"]
    rep = server.ga4_report(["eventName"], ["eventCount"], contains={"eventName": "whatsapp"})
    body = session.calls[-1][3]
    assert body["dimensionFilter"]["filter"]["stringFilter"]["value"] == "whatsapp"
    assert rep["jumlah_baris"] == 2


def test_server_properties_not_ready(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path, Session({("GET", "/accountSummaries"): (401, {"error": {}})}))
    assert server.ga4_properties()["status"] == "BELUM_SIAP"
