from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
from google.protobuf.json_format import MessageToDict

from gads_mcp.gaql import flatten, humanize


def test_reserved_field_names_restored_from_real_row():
    row = GoogleAdsRow()
    row.campaign_criterion.type_ = 7          # LOCATION
    row.campaign_criterion.location.geo_target_constant = "geoTargetConstants/20438"
    row.conversion_action.type_ = 8
    raw = MessageToDict(GoogleAdsRow.pb(row), preserving_proto_field_name=True)
    out = humanize(flatten(raw))
    assert out["campaign_criterion.type"] == "LOCATION"
    assert out["campaign_criterion.location.geo_target_constant"] == "geoTargetConstants/20438"
    assert "conversion_action.type" in out and not any(k.endswith("_") for k in out)
