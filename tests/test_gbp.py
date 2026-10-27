from datetime import date, timedelta

import pytest

from gads_mcp import gbp

FUTURE = (date.today() + timedelta(days=30)).isoformat()
LATER = (date.today() + timedelta(days=31)).isoformat()


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.content = b"x" if data is not None else b""
        self.text = str(data)

    def json(self):
        return self._data


class Session:
    """Session palsu: routes = {(METHOD, url-suffix): data | (status, data)}; mencatat setiap panggilan."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls.append((method, url, params, json))
        for (m, suffix), data in self.routes.items():
            if m == method and url.endswith(suffix):
                return Resp(*data) if isinstance(data, tuple) else Resp(200, data)
        return Resp(404, {"error": {"message": "not found"}})


FULL = {
    "name": "locations/1", "title": "Tiga Pilar Tangerang", "storeCode": "TGR",
    "metadata": {"hasVoiceOfMerchant": True, "mapsUri": "https://maps", "newReviewUri": "https://review"},
    "categories": {"primaryCategory": {"displayName": "Toko bahan bangunan"},
                   "additionalCategories": [{"displayName": "Pemasok"}]},
    "phoneNumbers": {"primaryPhone": "021 555 1234"}, "websiteUri": "https://tigapilar.co.id",
    "regularHours": {"periods": [{"openDay": "MONDAY"}]}, "profile": {"description": "x" * 300},
    "storefrontAddress": {"addressLines": ["Jl. Raya 1"], "locality": "Tangerang"},
    "openInfo": {"status": "OPEN"},
}
BARE = {"name": "locations/2", "title": "Tiga Pilar Bekasi", "storefrontAddress": {"locality": "Bekasi"}}


def test_audit_scores_and_fixes():
    a = gbp.audit([FULL, BARE])
    full, bare = (next(r for r in a["lokasi"] if r["nama"] == n) for n in ("Tiga Pilar Tangerang", "Tiga Pilar Bekasi"))
    assert full["skor_kelengkapan"] == 100 and full["perlu_dilengkapi"] == []
    assert bare["skor_kelengkapan"] == 15   # alamat (10) + status buka default (5)
    assert any("Profil terverifikasi" in f for f in bare["perlu_dilengkapi"])
    assert a["lokasi"][0]["nama"] == "Tiga Pilar Bekasi"   # skor terendah dulu
    assert a["rata_rata_skor"] == 58 and any("skornya < 80" in t for t in a["temuan"])


def test_names_and_filters():
    assert gbp.account_name("123") == "accounts/123"
    assert gbp.location_name("accounts/1/locations/9") == "locations/9"
    assert gbp.filter_locations([FULL, BARE], None) == [FULL, BARE]
    assert gbp.filter_locations([FULL, BARE], ["bekasi"]) == [BARE]
    assert gbp.filter_locations([FULL, BARE], ["tgr", "2"]) == [FULL, BARE]
    with pytest.raises(gbp.GbpError, match="Bogor"):
        gbp.filter_locations([FULL, BARE], ["Bogor"])


def test_pick_account():
    accs = [{"name": "accounts/1", "accountName": "Budi", "type": "PERSONAL"},
            {"name": "accounts/2", "accountName": "RS Sari Asih", "type": "LOCATION_GROUP"}]
    assert gbp.pick_account(accs, None)["name"] == "accounts/2"
    assert gbp.pick_account(accs, "budi")["name"] == "accounts/1"
    assert gbp.pick_account(accs, "2")["name"] == "accounts/2"
    with pytest.raises(gbp.GbpError, match="tidak ditemukan"):
        gbp.pick_account(accs, "xyz")


def test_render_per_branch():
    assert gbp.render("Promo di {nama}, {kota}!", FULL) == "Promo di Tiga Pilar Tangerang, Tangerang!"
    with pytest.raises(gbp.GbpError, match="{harga}"):
        gbp.render("{harga}", FULL)


def test_build_post_variants():
    body, notes = gbp.build_post("Halo", cta_type="CALL")
    assert body == {"languageCode": "id", "summary": "Halo", "topicType": "STANDARD",
                    "callToAction": {"actionType": "CALL"}}
    assert any("Tanpa gambar" in n for n in notes)
    body, _ = gbp.build_post("Diskon", topic="offer", title="Promo", start=FUTURE, end=LATER, coupon="HEMAT",
                             image_url="https://x/img.jpg", cta_type="LEARN_MORE", cta_url="https://x")
    assert body["event"]["schedule"]["endDate"]["day"] == date.fromisoformat(LATER).day
    assert body["offer"] == {"couponCode": "HEMAT"} and body["media"][0]["sourceUrl"] == "https://x/img.jpg"
    _, notes = gbp.build_post("Hubungi 081234567890")
    assert any("nomor telepon" in n for n in notes)
    for kwargs, msg in [({"topic": "OFFER"}, "butuh title"), ({"cta_type": "BOOK"}, "butuh cta_url"),
                        ({"image_url": "http://x"}, "https"), ({"cta_type": "BELI"}, "cta_type")]:
        with pytest.raises(gbp.GbpError, match=msg):
            gbp.build_post("x", **kwargs)
    with pytest.raises(gbp.GbpError, match="maksimal"):
        gbp.build_post("x" * 1501)


def test_special_hours_merge_keeps_other_dates():
    new = gbp.special_periods([FUTURE])
    d = date.fromisoformat(LATER)
    existing = {"specialHourPeriods": [
        {"startDate": {"year": d.year, "month": d.month, "day": d.day}, "closed": True},
        {"startDate": {"year": 2020, "month": 1, "day": 1}, "closed": True},          # sudah lewat → dibuang
        {**new[0], "closed": False, "openTime": {"hours": 8}, "closeTime": {"hours": 12}},  # tanggal sama → ditimpa
    ]}
    merged = gbp.merge_special_hours(existing, new)["specialHourPeriods"]
    assert len(merged) == 2 and merged[0] == new[0] and merged[0]["closed"] is True
    assert gbp.describe_period(merged[0]).endswith("TUTUP")
    jam = gbp.special_periods([FUTURE], closed=False, open_time="08.00", close_time="12:30")[0]
    assert jam["openTime"] == {"hours": 8, "minutes": 0} and gbp.describe_period(jam).endswith("08:00–12:30")
    with pytest.raises(gbp.GbpError, match="sudah lewat"):
        gbp.special_periods(["2020-01-01"])
    with pytest.raises(gbp.GbpError, match="open_time"):
        gbp.special_periods([FUTURE], closed=False)


def test_reviews_and_findings():
    session = Session({("GET", "/accounts/1/locations/1/reviews"): {
        "averageRating": 4.2, "totalReviewCount": 2,
        "reviews": [{"name": "accounts/1/locations/1/reviews/a", "starRating": "ONE", "comment": "Lama",
                     "reviewer": {"displayName": "Ani"}},
                    {"name": "accounts/1/locations/1/reviews/b", "starRating": "FIVE",
                     "reviewReply": {"comment": "Terima kasih"}}]}})
    data = gbp.Gbp(session).reviews("1", "locations/1")
    rows = [gbp.review_row(r) for r in data["reviews"]]
    assert rows[0]["bintang"] == 1 and rows[1]["balasan"] == "Terima kasih"
    assert any("bintang 1–2" in f for f in gbp.review_findings(rows))
    assert gbp.check_reply("Terima kasih Bu Ani") == []
    with pytest.raises(gbp.GbpError):
        gbp.check_reply(" ")


def test_pagination_and_special_hours_validate_only():
    session = Session({("GET", "/accounts"): {"accounts": [{"name": "accounts/1"}]}})
    pages = iter([{"locations": [FULL], "nextPageToken": "p2"}, {"locations": [BARE]}])
    session.routes[("GET", "/accounts/1/locations")] = None
    orig = session.request

    def request(method, url, params=None, json=None, timeout=None):
        if url.endswith("/accounts/1/locations"):
            session.calls.append((method, url, dict(params), json))
            return Resp(200, next(pages))
        return orig(method, url, params, json, timeout)

    session.request = request
    api = gbp.Gbp(session)
    assert [l["name"] for l in api.locations("1")] == ["locations/1", "locations/2"]
    assert session.calls[-1][2]["pageToken"] == "p2"
    session.routes[("PATCH", "/locations/1")] = {}
    api.patch_special_hours("locations/1", {"specialHourPeriods": []}, validate_only=True)
    method, url, params, body = session.calls[-1]
    assert params == {"updateMask": "specialHours", "validateOnly": "true"} and "specialHours" in body


def test_errors_explained():
    api = gbp.Gbp(Session({("GET", "/accounts"): (429, {"error": {"message": "Quota exceeded", "status":
                                                                  "RESOURCE_EXHAUSTED"}})}))
    with pytest.raises(gbp.GbpError, match="belum disetujui"):
        api.accounts()
    assert "diaktifkan" in gbp.explain(403, {"error": {"message": "x", "details": ["SERVICE_DISABLED"]}})
    assert "Pemilik/Pengelola" in gbp.explain(403, {"error": {"message": "denied"}})


def test_missing_token():
    class S:
        gbp_refresh_token = ""
    with pytest.raises(gbp.GbpError, match="GBP_REFRESH_TOKEN"):
        gbp.Gbp.from_settings(S())


def test_fan_out_partial_failure():
    def act(loc):
        if loc["name"] == "locations/2":
            raise gbp.GbpError("ditolak")
        return {"name": "ok"}
    ok, failed = gbp.fan_out([FULL, BARE], act)
    assert [r["lokasi"] for r in ok] == ["locations/1"] and failed[0]["error"] == "ditolak"


def _server_with(monkeypatch, tmp_path, session, read_only=False):
    from fakes import FakeAds

    from gads_mcp import server
    from gads_mcp.config import Safety

    monkeypatch.setattr(server, "_ads", FakeAds(safety=Safety(read_only=read_only, audit_log_dir=tmp_path)))
    monkeypatch.setattr(server, "_gbp", gbp.Gbp(session))
    return server


def _branches():
    return Session({("GET", "/accounts"): {"accounts": [{"name": "accounts/9", "accountName": "Tiga Pilar"}]},
                    ("GET", "/accounts/9/locations"): {"locations": [FULL, BARE]},
                    ("POST", "/localPosts"): {"name": "accounts/9/locations/x/localPosts/p", "state": "PROCESSING"},
                    ("PATCH", "/locations/1"): {"specialHours": {}}, ("PATCH", "/locations/2"): {}})


def test_server_post_preview_then_confirm(monkeypatch, tmp_path):
    session = _branches()
    server = _server_with(monkeypatch, tmp_path, session)
    assert {"gbp_post", "gbp_reply_review", "gbp_special_hours"} <= set(server.WRITE_TOOLS)
    prev = server.gbp_post("Stok baru di {nama} ({kota})", cta_type="CALL")
    assert prev["status"] == "PRATINJAU" and prev["target"]["jumlah_cabang"] == 2
    assert prev["per_cabang"][1]["teks"] == "Stok baru di Tiga Pilar Bekasi (Bekasi)"
    assert not [c for c in session.calls if c[0] == "POST"]
    done = server.gbp_post("Stok baru di {nama}", locations=["bekasi"], cta_type="CALL", confirm=True)
    posts = [c for c in session.calls if c[0] == "POST"]
    assert done["status"] == "DIJALANKAN" and len(posts) == 1 and "/locations/2/localPosts" in posts[0][1]
    assert (tmp_path / "gbp-9.jsonl").exists()


def test_server_special_hours_validates_in_preview(monkeypatch, tmp_path):
    session = _branches()
    server = _server_with(monkeypatch, tmp_path, session)
    prev = server.gbp_special_hours([FUTURE])
    patches = [c for c in session.calls if c[0] == "PATCH"]
    assert prev["status"] == "PRATINJAU" and len(patches) == 2
    assert all(p[2].get("validateOnly") == "true" for p in patches)


def test_server_read_only_blocks_gbp_writes(monkeypatch, tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    session = _branches()
    server = _server_with(monkeypatch, tmp_path, session, read_only=True)
    with pytest.raises(ToolError, match="baca-saja"):
        server.gbp_post("Halo", cta_type="CALL", confirm=True)
    assert not [c for c in session.calls if c[0] == "POST"]


def test_server_status_not_ready(monkeypatch, tmp_path):
    session = Session({("GET", "/accounts"): (429, {"error": {"message": "Quota exceeded"}})})
    server = _server_with(monkeypatch, tmp_path, session)
    res = server.gbp_status()
    assert res["status"] == "BELUM_SIAP" and "belum disetujui" in res["pesan"]
