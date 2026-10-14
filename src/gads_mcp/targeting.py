"""Pengaturan kampanye & targeting: jaringan, AI Max, mode lokasi, lokasi/radius, jadwal iklan, perangkat.

Pengetahuan penting yang dipakai di sini:
- Search Partners & perluasan Display di kampanye Search biasanya berkonversi lebih buruk.
- AI Max memperluas pencarian di luar kata kunci (search terms bertipe "AI Maksimal").
- Mode lokasi bawaan Google (PRESENCE_OR_INTEREST) ikut menjangkau orang di luar area; untuk bisnis
  lokal/B2B biasanya lebih efisien PRESENCE.
- Jadwal iklan: begitu ada satu jendela, iklan HANYA tayang di jendela-jendela itu.
- Smart Bidding (Maks. konversi, Target CPA/ROAS, Maks. nilai) mengabaikan penyesuaian bid perangkat,
  jadwal, dan lokasi — kecuali -100% (mematikan).
"""

from __future__ import annotations

from typing import Any, Protocol

from .mutations import Mutator, _digits
from .safety import SafetyError

LOCATION_MODES = ("PRESENCE", "PRESENCE_OR_INTEREST", "SEARCH_INTEREST")
DEVICES = ("MOBILE", "DESKTOP", "TABLET")
SMART_BIDDING = ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE", "TARGET_CPA", "TARGET_ROAS")
REMOVABLE_TYPES = ("LOCATION", "PROXIMITY", "AD_SCHEDULE", "LANGUAGE")
DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
DAY_ALIASES = {
    "senin": ["MONDAY"], "selasa": ["TUESDAY"], "rabu": ["WEDNESDAY"], "kamis": ["THURSDAY"],
    "jumat": ["FRIDAY"], "jum'at": ["FRIDAY"], "sabtu": ["SATURDAY"], "minggu": ["SUNDAY"], "ahad": ["SUNDAY"],
    "hari_kerja": list(DAYS[:5]), "weekdays": list(DAYS[:5]), "senin-jumat": list(DAYS[:5]),
    "akhir_pekan": list(DAYS[5:]), "weekend": list(DAYS[5:]),
    "setiap_hari": list(DAYS), "all": list(DAYS), "semua": list(DAYS),
}
HARI = dict(zip(DAYS, ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")))
MINUTES = {0: "ZERO", 15: "FIFTEEN", 30: "THIRTY", 45: "FORTY_FIVE"}
MINUTE_VALUE = {v: k for k, v in MINUTES.items()}


class Searcher(Protocol):
    def search(self, customer_id: str, query: str) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Konversi & validasi murni
# ---------------------------------------------------------------------------

def pct_to_modifier(pct: float | None, low: float = -100, high: float = 900) -> float | None:
    """+20 -> 1.2, -30 -> 0.7, -100 -> 0.0 (mematikan)."""
    if pct is None:
        return None
    if not low <= pct <= high:
        raise ValueError(f"Penyesuaian bid harus {low:g}%..+{high:g}%, didapat {pct:g}%.")
    return round(1 + pct / 100, 4)


def modifier_to_pct(mod: float | None) -> float | None:
    """1.2 -> 20.0, 0.0 -> -100.0 (mati), None -> None (tanpa penyesuaian)."""
    return None if mod is None else round((float(mod) - 1) * 100, 1)


def _days(value: Any) -> list[str]:
    key = str(value).strip().lower().replace(" ", "_")
    if key.upper() in DAYS:
        return [key.upper()]
    if key in DAY_ALIASES:
        return DAY_ALIASES[key]
    raise ValueError(f"Hari '{value}' tidak dikenal (pakai Senin..Minggu, hari_kerja, akhir_pekan, setiap_hari).")


def _hm(value: Any, name: str, is_end: bool) -> tuple[int, int]:
    """'08:30' / 8 / 8.5 -> (8, 30)."""
    text = str(value).strip()
    if ":" in text:
        h, m = text.split(":", 1)
        hour, minute = int(h), int(m)
    else:
        f = float(text)
        hour, minute = int(f), int(round((f - int(f)) * 60))
    if minute not in MINUTES:
        raise ValueError(f"{name} {value!r}: menit harus 00, 15, 30, atau 45.")
    if not (0 <= hour <= (24 if is_end else 23)) or (hour == 24 and minute):
        raise ValueError(f"{name} {value!r} di luar rentang jam.")
    return hour, minute


def normalize_schedule(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{"day": "hari_kerja", "start": "08:00", "end": "17:00", "bid_pct": 0}] -> jendela per hari, tervalidasi."""
    out: list[dict[str, Any]] = []
    for i, w in enumerate(windows, 1):
        days = _days(w.get("day") or w.get("day_of_week") or "")
        sh, sm = _hm(w.get("start", w.get("start_hour", 0)), f"Jendela #{i} start", False)
        eh, em = _hm(w.get("end", w.get("end_hour", 24)), f"Jendela #{i} end", True)
        if (eh, em) <= (sh, sm):
            raise ValueError(f"Jendela #{i}: jam selesai harus setelah jam mulai (tidak bisa melewati tengah malam; "
                             "pecah jadi dua jendela).")
        mod = pct_to_modifier(w.get("bid_pct"), low=-90, high=900)
        for d in days:
            out.append({"day": d, "start": (sh, sm), "end": (eh, em), "bid_modifier": mod})
    # cek tumpang tindih per hari
    for d in DAYS:
        ws = sorted((w for w in out if w["day"] == d), key=lambda w: w["start"])
        for a, b in zip(ws, ws[1:]):
            if b["start"] < a["end"]:
                raise ValueError(f"Jadwal {HARI[d]} tumpang tindih: {fmt_window(a)} dan {fmt_window(b)}.")
        if len(ws) > 6:
            raise ValueError(f"Maksimal 6 jendela per hari ({HARI[d]}: {len(ws)}).")
    return out


def fmt_window(w: dict[str, Any]) -> str:
    (sh, sm), (eh, em) = w["start"], w["end"]
    return f"{HARI.get(w['day'], w['day'])} {sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"


# ---------------------------------------------------------------------------
# Baca & audit
# ---------------------------------------------------------------------------

def q_settings(campaign_id: int) -> str:
    return (
        "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
        "campaign.bidding_strategy_type, campaign.network_settings.target_google_search, "
        "campaign.network_settings.target_search_network, campaign.network_settings.target_content_network, "
        "campaign.geo_target_type_setting.positive_geo_target_type, "
        "campaign.geo_target_type_setting.negative_geo_target_type, campaign.ai_max_setting.enable_ai_max "
        f"FROM campaign WHERE campaign.id = {campaign_id}"
    )


def q_criteria(campaign_id: int) -> str:
    return (
        "SELECT campaign_criterion.criterion_id, campaign_criterion.type, campaign_criterion.negative, "
        "campaign_criterion.bid_modifier, campaign_criterion.location.geo_target_constant, "
        "campaign_criterion.proximity.radius, campaign_criterion.proximity.radius_units, "
        "campaign_criterion.proximity.address.city_name, campaign_criterion.proximity.address.street_address, "
        "campaign_criterion.ad_schedule.day_of_week, campaign_criterion.ad_schedule.start_hour, "
        "campaign_criterion.ad_schedule.start_minute, campaign_criterion.ad_schedule.end_hour, "
        "campaign_criterion.ad_schedule.end_minute, campaign_criterion.device.type, "
        "campaign_criterion.language.language_constant FROM campaign_criterion "
        f"WHERE campaign.id = {campaign_id} AND campaign_criterion.status != 'REMOVED' "
        "AND campaign_criterion.type IN ('LOCATION', 'PROXIMITY', 'AD_SCHEDULE', 'DEVICE', 'LANGUAGE')"
    )


def q_geo_names(resource_names: list[str]) -> str:
    names = ", ".join(f"'{r}'" for r in resource_names)
    return ("SELECT geo_target_constant.resource_name, geo_target_constant.id, geo_target_constant.name, "
            "geo_target_constant.canonical_name, geo_target_constant.target_type FROM geo_target_constant "
            f"WHERE geo_target_constant.resource_name IN ({names})")


def assemble(campaign_rows: list[dict], criteria: list[dict], geo_rows: list[dict]) -> dict[str, Any]:
    if not campaign_rows:
        raise ValueError("Kampanye tidak ditemukan.")
    c = campaign_rows[0]
    geo_by_rn = {r.get("geo_target_constant.resource_name"): r for r in geo_rows}
    geo_by_id = {f"geoTargetConstants/{r.get('geo_target_constant.id')}": r for r in geo_rows}
    locations, proximity, schedule, devices, languages = [], [], [], [], []
    for r in criteria:
        t = r.get("campaign_criterion.type")
        base = {"criterion_id": r.get("campaign_criterion.criterion_id"),
                "penyesuaian_bid_pct": modifier_to_pct(r.get("campaign_criterion.bid_modifier"))}
        if t == "LOCATION":
            rn = r.get("campaign_criterion.location.geo_target_constant")
            g = geo_by_rn.get(rn) or geo_by_id.get(rn) or {}
            locations.append({**base, "geo": rn, "nama": g.get("geo_target_constant.canonical_name") or rn,
                              "jenis": g.get("geo_target_constant.target_type"),
                              "dikecualikan": bool(r.get("campaign_criterion.negative"))})
        elif t == "PROXIMITY":
            proximity.append({**base, "radius": r.get("campaign_criterion.proximity.radius"),
                              "satuan": r.get("campaign_criterion.proximity.radius_units"),
                              "pusat": r.get("campaign_criterion.proximity.address.street_address")
                              or r.get("campaign_criterion.proximity.address.city_name")})
        elif t == "AD_SCHEDULE":
            w = {"day": r.get("campaign_criterion.ad_schedule.day_of_week"),
                 "start": (int(r.get("campaign_criterion.ad_schedule.start_hour") or 0),
                           MINUTE_VALUE.get(r.get("campaign_criterion.ad_schedule.start_minute"), 0)),
                 "end": (int(r.get("campaign_criterion.ad_schedule.end_hour") or 0),
                         MINUTE_VALUE.get(r.get("campaign_criterion.ad_schedule.end_minute"), 0))}
            schedule.append({**base, "jendela": fmt_window(w), "day": w["day"]})
        elif t == "DEVICE":
            devices.append({**base, "perangkat": r.get("campaign_criterion.device.type")})
        elif t == "LANGUAGE":
            languages.append(r.get("campaign_criterion.language.language_constant"))
    schedule.sort(key=lambda s: (DAYS.index(s["day"]) if s["day"] in DAYS else 9, s["jendela"]))
    settings = {
        "kampanye": {"id": c.get("campaign.id"), "nama": c.get("campaign.name"), "status": c.get("campaign.status"),
                     "jenis": c.get("campaign.advertising_channel_type"),
                     "bidding": c.get("campaign.bidding_strategy_type")},
        "jaringan": {"google_search": c.get("campaign.network_settings.target_google_search"),
                     "search_partners": bool(c.get("campaign.network_settings.target_search_network")),
                     "perluasan_display": bool(c.get("campaign.network_settings.target_content_network"))},
        "ai_max": bool(c.get("campaign.ai_max_setting.enable_ai_max")),
        "mode_lokasi": c.get("campaign.geo_target_type_setting.positive_geo_target_type"),
        "mode_lokasi_pengecualian": c.get("campaign.geo_target_type_setting.negative_geo_target_type"),
        "lokasi": locations,
        "radius": proximity,
        "jadwal": schedule,
        "perangkat": devices,
        "bahasa": languages,
    }
    settings["temuan"] = audit(settings)
    return settings


def audit(s: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def add(level: str, msg: str) -> None:
        out.append({"level": level, "pesan": msg})

    search = s["kampanye"]["jenis"] == "SEARCH"
    smart = s["kampanye"]["bidding"] in SMART_BIDDING
    if search and s["jaringan"]["search_partners"]:
        add("PERINGATAN", "Search Partners aktif — trafik situs mitra biasanya berkonversi lebih rendah. "
                          "Matikan bila CPA mitra lebih buruk (update_campaign_settings search_partners=false).")
    if search and s["jaringan"]["perluasan_display"]:
        add("PERINGATAN", "Perluasan Display aktif di kampanye Search — iklan ikut tayang di situs/aplikasi dengan "
                          "niat beli rendah. Biasanya lebih baik dimatikan (display_expansion=false).")
    if search and s["ai_max"]:
        add("INFO", "AI Max aktif: Google memperluas pencarian di luar kata kunci (search terms 'AI Maksimal'). "
                    "Pantau search_terms; bila banyak yang tidak relevan, pertimbangkan ai_max=false.")
    positives = [l for l in s["lokasi"] if not l["dikecualikan"]]
    if not positives and not s["radius"]:
        add("PERINGATAN", "Tidak ada target lokasi — iklan bisa tayang di SELURUH DUNIA. Tambahkan lokasi "
                          "(resolve_locations → add_location_targets).")
    if s["mode_lokasi"] == "PRESENCE_OR_INTEREST":
        add("PERINGATAN", "Mode lokasi 'Kehadiran atau minat' ikut menjangkau orang di luar area target. Untuk bisnis "
                          "lokal/B2B biasanya lebih efisien 'Kehadiran' (location_mode=PRESENCE).")
    if not s["jadwal"]:
        add("INFO", "Tanpa jadwal iklan (tayang 24 jam). Bila lead masuk via telepon/WA dan CS hanya aktif jam kerja, "
                    "pertimbangkan set_ad_schedule.")
    if smart:
        adjusted = [d for d in s["perangkat"] if d["penyesuaian_bid_pct"] not in (None, 0.0, -100.0)]
        adjusted += [w for w in s["jadwal"] if w["penyesuaian_bid_pct"] not in (None, 0.0)]
        adjusted += [l for l in s["lokasi"] if l["penyesuaian_bid_pct"] not in (None, 0.0) and not l["dikecualikan"]]
        if adjusted:
            add("INFO", f"Bidding {s['kampanye']['bidding']} (Smart Bidding) mengabaikan penyesuaian bid perangkat/"
                        "jadwal/lokasi kecuali -100% — penyesuaian yang ada tidak berpengaruh.")
    for d in s["perangkat"]:
        if d["perangkat"] == "MOBILE" and d["penyesuaian_bid_pct"] == -100.0:
            add("PERINGATAN", "Iklan dimatikan di HP (-100%) — mayoritas pencarian di Indonesia dari HP.")
    return out


def fetch_settings(ads: Searcher, cid: str, campaign_id: Any) -> dict[str, Any]:
    camp = int(_digits(campaign_id, "campaign_id"))
    campaign_rows = ads.search(cid, q_settings(camp))
    criteria = ads.search(cid, q_criteria(camp))
    geos = sorted({r.get("campaign_criterion.location.geo_target_constant") for r in criteria
                   if r.get("campaign_criterion.type") == "LOCATION" and r.get("campaign_criterion.location.geo_target_constant")})
    geo_rows = ads.search(cid, q_geo_names(geos)) if geos else []
    return assemble(campaign_rows, criteria, geo_rows)


# ---------------------------------------------------------------------------
# Operasi ubah
# ---------------------------------------------------------------------------

class Targeting:
    def __init__(self, mutator: Mutator):
        self.m = mutator

    @property
    def gc(self):
        return self.m.gc

    def _camp_path(self, cid: str, camp: str) -> str:
        return self.gc.get_service("CampaignService").campaign_path(cid, camp)

    def _crit_path(self, cid: str, camp: str, crit_id: Any) -> str:
        return self.gc.get_service("CampaignCriterionService").campaign_criterion_path(cid, camp, str(crit_id))

    # ----- pengaturan kampanye -----

    def update_settings(self, cid: str, campaign_id: str, confirm: bool, search_partners: bool | None = None,
                        display_expansion: bool | None = None, ai_max: bool | None = None,
                        location_mode: str | None = None) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        before = fetch_settings(self.m.ads, cid, camp)
        op = self.gc.get_type("CampaignOperation")
        c = op.update
        c.resource_name = self._camp_path(cid, camp)
        after: dict[str, Any] = {}
        notes: list[str] = []
        if search_partners is not None:
            c.network_settings.target_search_network = search_partners
            op.update_mask.paths.append("network_settings.target_search_network")
            after["search_partners"] = search_partners
        if display_expansion is not None:
            c.network_settings.target_content_network = display_expansion
            op.update_mask.paths.append("network_settings.target_content_network")
            after["perluasan_display"] = display_expansion
            if display_expansion and before["kampanye"]["jenis"] == "SEARCH":
                notes.append("Perluasan Display di kampanye Search biasanya menurunkan kualitas lead.")
        if ai_max is not None:
            c.ai_max_setting.enable_ai_max = ai_max
            op.update_mask.paths.append("ai_max_setting.enable_ai_max")
            after["ai_max"] = ai_max
            notes.append("AI Max diaktifkan: pantau search_terms 1-2 minggu ke depan." if ai_max else
                         "AI Max dimatikan: pencarian kembali mengikuti kata kunci & match type.")
        if location_mode is not None:
            mode = location_mode.upper()
            if mode not in LOCATION_MODES:
                raise ValueError(f"location_mode harus salah satu dari {LOCATION_MODES}")
            c.geo_target_type_setting.positive_geo_target_type = getattr(self.gc.enums.PositiveGeoTargetTypeEnum, mode)
            op.update_mask.paths.append("geo_target_type_setting.positive_geo_target_type")
            after["mode_lokasi"] = mode
        if not after:
            raise ValueError("Tidak ada pengaturan yang diubah.")
        snapshot = {k: before[k] for k in ("jaringan", "ai_max", "mode_lokasi")}
        return self.m._finish(
            cid=cid, action="update_campaign_settings",
            target={"campaign_id": camp, "kampanye": before["kampanye"]["nama"]}, before=snapshot, after=after,
            service="CampaignService", method="mutate_campaigns", operations=[op], confirm=confirm,
            notes=notes or None,
            verify=lambda: {k: v for k, v in fetch_settings(self.m.ads, cid, camp).items()
                            if k in ("jaringan", "ai_max", "mode_lokasi")},
        )

    # ----- lokasi -----

    def resolve_locations(self, queries: list[str], country_code: str | None = "ID", locale: str = "id") -> list[dict]:
        if not queries:
            raise ValueError("queries tidak boleh kosong.")
        svc = self.gc.get_service("GeoTargetConstantService")
        request = {"locale": locale, "location_names": {"names": list(queries)[:25]}}
        if country_code:
            request["country_code"] = country_code.upper()
        response = svc.suggest_geo_target_constants(request=request)
        out = []
        for s in response.geo_target_constant_suggestions:
            g = s.geo_target_constant
            out.append({"query": s.search_term, "geo_id": str(g.id), "nama": g.canonical_name or g.name,
                        "jenis": g.target_type, "negara": g.country_code, "jangkauan": int(s.reach or 0)})
        return out

    def add_locations(self, cid: str, campaign_id: str, geo_ids: list[str], confirm: bool, exclude: bool = False,
                      bid_pct: float | None = None) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        ids = [_digits(str(g).split("/")[-1], "geo_id") for g in geo_ids]
        if not ids:
            raise ValueError("geo_ids tidak boleh kosong (dapatkan dari resolve_locations).")
        current = fetch_settings(self.m.ads, cid, camp)
        have = {(l["geo"].split("/")[-1], l["dikecualikan"]) for l in current["lokasi"]}
        new = [g for g in ids if (g, exclude) not in have]
        if not new:
            return {"status": "TIDAK_ADA_PERUBAHAN", "pesan": "Lokasi sudah ditargetkan dengan cara yang sama."}
        mod = pct_to_modifier(bid_pct, low=-90, high=900) if (bid_pct is not None and not exclude) else None
        ops = []
        for g in new:
            op = self.gc.get_type("CampaignCriterionOperation")
            crit = op.create
            crit.campaign = self._camp_path(cid, camp)
            crit.location.geo_target_constant = f"geoTargetConstants/{g}"
            crit.negative = exclude
            if mod is not None:
                crit.bid_modifier = mod
            ops.append(op)
        notes = []
        if not exclude and not [l for l in current["lokasi"] if not l["dikecualikan"]] and not current["radius"]:
            notes.append("Sebelumnya tanpa target lokasi (seluruh dunia); setelah ini iklan hanya tayang di lokasi ini.")
        if mod is not None and current["kampanye"]["bidding"] in SMART_BIDDING:
            notes.append("Smart Bidding mengabaikan penyesuaian bid lokasi.")
        return self.m._finish(
            cid=cid, action="exclude_locations" if exclude else "add_locations",
            target={"campaign_id": camp, "kampanye": current["kampanye"]["nama"]},
            before={"lokasi": [l["nama"] for l in current["lokasi"]]},
            after={"geo_ids": new, "dikecualikan": exclude, "penyesuaian_bid_pct": bid_pct},
            service="CampaignCriterionService", method="mutate_campaign_criteria", operations=ops,
            confirm=confirm, notes=notes or None,
            verify=lambda: [l["nama"] for l in fetch_settings(self.m.ads, cid, camp)["lokasi"]],
        )

    def add_proximity(self, cid: str, campaign_id: str, radius_km: float, confirm: bool,
                      latitude: float | None = None, longitude: float | None = None,
                      street_address: str | None = None, city_name: str | None = None,
                      province_name: str | None = None, postal_code: str | None = None,
                      country_code: str = "ID", bid_pct: float | None = None) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        if not 1 <= float(radius_km) <= 800:
            raise ValueError("radius_km harus 1-800.")
        has_point = latitude is not None and longitude is not None
        if not has_point and not (street_address or city_name or postal_code):
            raise ValueError("Isi latitude+longitude, atau alamat (street_address/city_name/postal_code).")
        if has_point and not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Koordinat tidak valid.")
        op = self.gc.get_type("CampaignCriterionOperation")
        crit = op.create
        crit.campaign = self._camp_path(cid, camp)
        p = crit.proximity
        p.radius = float(radius_km)
        p.radius_units = self.gc.enums.ProximityRadiusUnitsEnum.KILOMETERS
        if has_point:
            p.geo_point.latitude_in_micro_degrees = int(round(latitude * 1_000_000))
            p.geo_point.longitude_in_micro_degrees = int(round(longitude * 1_000_000))
        else:
            for field, value in (("street_address", street_address), ("city_name", city_name),
                                 ("province_name", province_name), ("postal_code", postal_code)):
                if value:
                    setattr(p.address, field, value)
            p.address.country_code = country_code.upper()
        mod = pct_to_modifier(bid_pct, low=-90, high=900)
        if mod is not None:
            crit.bid_modifier = mod
        center = (f"{latitude},{longitude}" if has_point
                  else ", ".join(v for v in (street_address, city_name, province_name, postal_code) if v))
        return self.m._finish(
            cid=cid, action="add_proximity", target={"campaign_id": camp}, before=None,
            after={"pusat": center, "radius_km": radius_km, "penyesuaian_bid_pct": bid_pct},
            service="CampaignCriterionService", method="mutate_campaign_criteria", operations=[op], confirm=confirm,
            verify=lambda: fetch_settings(self.m.ads, cid, camp)["radius"],
        )

    def remove_criteria(self, cid: str, campaign_id: str, criterion_ids: list[str], confirm: bool,
                        allow_no_location: bool = False) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        ids = {_digits(i, "criterion_id") for i in criterion_ids}
        if not ids:
            raise ValueError("criterion_ids tidak boleh kosong (lihat campaign_settings).")
        cur = fetch_settings(self.m.ads, cid, camp)
        removable = {str(x["criterion_id"]): ("LOCATION", x) for x in cur["lokasi"]}
        removable.update({str(x["criterion_id"]): ("PROXIMITY", x) for x in cur["radius"]})
        removable.update({str(x["criterion_id"]): ("AD_SCHEDULE", x) for x in cur["jadwal"]})
        unknown = ids - set(removable)
        if unknown:
            raise ValueError(f"Kriteria {sorted(unknown)} bukan lokasi/radius/jadwal di kampanye ini "
                             "(perangkat tidak bisa dihapus — pakai set_device_bid_adjustments).")
        remaining_geo = [x for x in cur["lokasi"] if not x["dikecualikan"] and str(x["criterion_id"]) not in ids]
        remaining_geo += [x for x in cur["radius"] if str(x["criterion_id"]) not in ids]
        had_geo = [x for x in cur["lokasi"] if not x["dikecualikan"]] + cur["radius"]
        if had_geo and not remaining_geo and not allow_no_location:
            raise SafetyError("Menghapus semua target lokasi membuat iklan tayang ke SELURUH DUNIA. "
                              "Tambahkan lokasi pengganti dulu, atau ulangi dengan allow_no_location=true.")
        ops = []
        for i in sorted(ids):
            op = self.gc.get_type("CampaignCriterionOperation")
            op.remove = self._crit_path(cid, camp, i)
            ops.append(op)
        notes = []
        if any(removable[i][0] == "AD_SCHEDULE" for i in ids) and len(cur["jadwal"]) == len(
                [i for i in ids if removable[i][0] == "AD_SCHEDULE"]):
            notes.append("Semua jendela jadwal dihapus — iklan kembali tayang 24 jam.")
        return self.m._finish(
            cid=cid, action="remove_targeting", target={"campaign_id": camp},
            before=[{"jenis": removable[i][0], **removable[i][1]} for i in sorted(ids)], after=None,
            service="CampaignCriterionService", method="mutate_campaign_criteria", operations=ops, confirm=confirm,
            notes=notes or None, verify=lambda: {k: v for k, v in fetch_settings(self.m.ads, cid, camp).items()
                                                 if k in ("lokasi", "radius", "jadwal")},
        )

    # ----- jadwal iklan (ganti seluruh jadwal, atomik) -----

    def set_schedule(self, cid: str, campaign_id: str, windows: list[dict[str, Any]], confirm: bool) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        if not windows:
            raise ValueError("windows kosong. Untuk tayang 24 jam, hapus jadwal lewat remove_targeting.")
        norm = normalize_schedule(windows)
        cur = fetch_settings(self.m.ads, cid, camp)
        ops = []
        for w in cur["jadwal"]:
            mo = self.gc.get_type("MutateOperation")
            mo.campaign_criterion_operation.remove = self._crit_path(cid, camp, w["criterion_id"])
            ops.append(mo)
        for w in norm:
            mo = self.gc.get_type("MutateOperation")
            crit = mo.campaign_criterion_operation.create
            crit.campaign = self._camp_path(cid, camp)
            s = crit.ad_schedule
            s.day_of_week = getattr(self.gc.enums.DayOfWeekEnum, w["day"])
            s.start_hour, s.end_hour = w["start"][0], w["end"][0]
            s.start_minute = getattr(self.gc.enums.MinuteOfHourEnum, MINUTES[w["start"][1]])
            s.end_minute = getattr(self.gc.enums.MinuteOfHourEnum, MINUTES[w["end"][1]])
            if w["bid_modifier"] is not None:
                crit.bid_modifier = w["bid_modifier"]
            ops.append(mo)
        days_on = sorted({w["day"] for w in norm}, key=DAYS.index)
        notes = ["Iklan HANYA tayang di jendela ini (zona waktu akun)."]
        off = [HARI[d] for d in DAYS if d not in days_on]
        if off:
            notes.append(f"Tidak tayang sama sekali pada: {', '.join(off)}.")
        if any(w["bid_modifier"] not in (None, 1.0) for w in norm) and cur["kampanye"]["bidding"] in SMART_BIDDING:
            notes.append("Smart Bidding mengabaikan penyesuaian bid jadwal.")
        return self.m._finish(
            cid=cid, action="set_ad_schedule", target={"campaign_id": camp, "kampanye": cur["kampanye"]["nama"]},
            before=[w["jendela"] for w in cur["jadwal"]] or "24 jam",
            after=[fmt_window(w) + (f" ({(w['bid_modifier'] - 1) * 100:+.0f}%)" if w["bid_modifier"] not in (None, 1.0) else "")
                   for w in norm],
            service="GoogleAdsService", method="mutate", operations=ops, confirm=confirm, notes=notes,
            verify=lambda: [w["jendela"] for w in fetch_settings(self.m.ads, cid, camp)["jadwal"]],
        )

    # ----- perangkat -----

    def set_devices(self, cid: str, confirm: bool, adjustments: dict[str, float], campaign_id: str | None = None,
                    ad_group_id: str | None = None) -> dict:
        if bool(campaign_id) == bool(ad_group_id):
            raise ValueError("Isi salah satu: campaign_id ATAU ad_group_id.")
        adj = {k.upper(): v for k, v in adjustments.items()}
        bad = set(adj) - set(DEVICES)
        if bad:
            raise ValueError(f"Perangkat tidak dikenal {sorted(bad)}; pilihan: {DEVICES}.")
        mods = {d: pct_to_modifier(p) for d, p in adj.items()}
        if campaign_id:
            camp = _digits(campaign_id, "campaign_id")
            cur = fetch_settings(self.m.ads, cid, camp)
            existing = {d["perangkat"]: d for d in cur["perangkat"]}
            bidding, target = cur["kampanye"]["bidding"], {"campaign_id": camp, "kampanye": cur["kampanye"]["nama"]}
        else:
            ag = _digits(ad_group_id, "ad_group_id")
            rows = self.m.ads.search(cid, (
                "SELECT ad_group_bid_modifier.criterion_id, ad_group_bid_modifier.device.type, "
                "ad_group_bid_modifier.bid_modifier, campaign.bidding_strategy_type FROM ad_group_bid_modifier "
                f"WHERE ad_group.id = {ag}"))
            existing = {r.get("ad_group_bid_modifier.device.type"): {
                "criterion_id": r.get("ad_group_bid_modifier.criterion_id"),
                "penyesuaian_bid_pct": modifier_to_pct(r.get("ad_group_bid_modifier.bid_modifier"))}
                for r in rows if r.get("ad_group_bid_modifier.device.type")}
            bidding = rows[0].get("campaign.bidding_strategy_type") if rows else None
            target = {"ad_group_id": ag}
        final = {d: (mods[d] if d in mods else (1 + (existing.get(d, {}).get("penyesuaian_bid_pct") or 0) / 100))
                 for d in DEVICES}
        if all(v == 0 for v in final.values()):
            raise SafetyError("Semua perangkat -100% berarti iklan tidak tayang sama sekali. Jeda kampanye bila itu tujuannya.")
        ops = []
        for d, mod in mods.items():
            if campaign_id:
                op = self.gc.get_type("CampaignCriterionOperation")
                if d in existing:
                    op.update.resource_name = self._crit_path(cid, camp, existing[d]["criterion_id"])
                    op.update.bid_modifier = mod
                    op.update_mask.paths.append("bid_modifier")
                else:
                    op.create.campaign = self._camp_path(cid, camp)
                    op.create.device.type_ = getattr(self.gc.enums.DeviceEnum, d)
                    op.create.bid_modifier = mod
            else:
                op = self.gc.get_type("AdGroupBidModifierOperation")
                if d in existing:
                    op.update.resource_name = self.gc.get_service("AdGroupBidModifierService").ad_group_bid_modifier_path(
                        cid, ag, str(existing[d]["criterion_id"]))
                    op.update.bid_modifier = mod
                    op.update_mask.paths.append("bid_modifier")
                else:
                    op.create.ad_group = self.gc.get_service("AdGroupService").ad_group_path(cid, ag)
                    op.create.device.type_ = getattr(self.gc.enums.DeviceEnum, d)
                    op.create.bid_modifier = mod
            ops.append(op)
        notes = []
        if bidding in SMART_BIDDING and any(m != 0 for m in mods.values()):
            notes.append(f"Bidding {bidding} (Smart Bidding) mengabaikan penyesuaian perangkat selain -100%.")
        if mods.get("MOBILE") == 0:
            notes.append("Iklan dimatikan di HP — mayoritas pencarian di Indonesia dari HP.")
        if campaign_id:
            service, method = "CampaignCriterionService", "mutate_campaign_criteria"
            verify = lambda: fetch_settings(self.m.ads, cid, camp)["perangkat"]  # noqa: E731
        else:
            service, method = "AdGroupBidModifierService", "mutate_ad_group_bid_modifiers"
            verify = None
        return self.m._finish(
            cid=cid, action="set_device_bid_adjustments", target=target,
            before={d: v.get("penyesuaian_bid_pct") for d, v in existing.items()},
            after={d: adj[d] for d in mods}, service=service, method=method, operations=ops, confirm=confirm,
            notes=notes or None, verify=verify,
        )
