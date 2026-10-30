"""Buat kampanye Penelusuran (Search) lengkap dalam SATU permintaan atomik.

Budget, kampanye, lokasi, bahasa, jadwal, kata kunci negatif, grup iklan, kata kunci, dan RSA dikirim bersama
lewat GoogleAdsService.mutate dengan ID sementara (-1, -2, ...): gagal satu bagian → tidak ada yang tercipta,
jadi tidak ada kampanye setengah jadi. Pratinjau memakai validate_only (Google memeriksa semuanya tanpa
membuat apa pun). Kampanye SELALU dibuat DIJEDA; pengguna mengaktifkan setelah memeriksa.

Pengaman bawaan (pelajaran dari PMax yang tayang di YouTube/game dan AI Max yang 57% di luar kata kunci):
hanya jaringan Google Search — Search Partners & perluasan Display MATI; AI Max mati; mode lokasi Kehadiran.
"""

from __future__ import annotations

from typing import Any

from . import rsa
from .mutations import Mutator, _digits, normalize_keywords
from .safety import check_budget, to_micros
from .targeting import MINUTES, fmt_window, normalize_schedule

LANGUAGES = {"id": "1025", "en": "1000"}
BIDDING = ("MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS")


def plan(spec: dict[str, Any]) -> dict[str, Any]:
    """Murni: validasi & rapikan rancangan. Melempar ValueError dengan semua masalah sekaligus."""
    errs: list[str] = []
    name = str(spec.get("name") or "").strip()
    if not name:
        errs.append("name (nama kampanye) wajib diisi.")
    try:
        budget = float(spec.get("daily_budget") or 0)
    except (TypeError, ValueError):
        budget = 0
    if budget <= 0:
        errs.append("daily_budget harus > 0 (Rupiah, mis. 30000).")
    geo_ids = [str(g).split("/")[-1].strip() for g in spec.get("geo_ids") or []]
    if not geo_ids:
        errs.append("geo_ids wajib diisi — kampanye tanpa lokasi tayang di seluruh dunia (cari lewat resolve_locations).")
    elif not all(g.isdigit() for g in geo_ids):
        errs.append("geo_ids harus angka (dari resolve_locations).")
    bidding = str(spec.get("bidding") or "MAXIMIZE_CLICKS").upper()
    if bidding not in BIDDING:
        errs.append(f"bidding harus salah satu: {', '.join(BIDDING)}.")
    cpc_cap = spec.get("max_cpc")
    langs = [LANGUAGES.get(str(l).lower(), str(l)) for l in (spec.get("languages") or ["id", "en"])]
    schedule = []
    if spec.get("schedule"):
        try:
            schedule = normalize_schedule(spec["schedule"])
        except ValueError as ex:
            errs.append(f"schedule: {ex}")
    negatives = []
    if spec.get("negative_keywords"):
        try:
            negatives = normalize_keywords(
                [k if isinstance(k, dict) else {"text": k, "match_type": "PHRASE"} for k in spec["negative_keywords"]])
        except ValueError as ex:
            errs.append(f"negative_keywords: {ex}")
    groups = []
    raw_groups = spec.get("ad_groups") or []
    if not raw_groups:
        errs.append("ad_groups wajib diisi (minimal 1 grup dengan kata kunci dan iklan).")
    if len(raw_groups) > 20:
        errs.append("Maksimal 20 grup iklan per pembuatan.")
    for i, g in enumerate(raw_groups, 1):
        gname = str(g.get("name") or "").strip() or f"Grup {i}"
        try:
            kws = normalize_keywords(
                [k if isinstance(k, dict) else {"text": k, "match_type": "PHRASE"} for k in g.get("keywords") or []])
        except ValueError as ex:
            errs.append(f"Grup '{gname}' kata kunci: {ex}")
            kws = []
        urls = g.get("final_urls") or spec.get("final_urls") or []
        copy = rsa.validate(g.get("headlines") or spec.get("headlines") or [],
                            g.get("descriptions") or spec.get("descriptions") or [],
                            g.get("path1", spec.get("path1")), g.get("path2", spec.get("path2")))
        bad = copy.errors + rsa.validate_urls(urls) + ([] if urls else ["final_urls wajib diisi."])
        errs += [f"Grup '{gname}' iklan: {e}" for e in bad]
        groups.append({"name": gname, "keywords": kws, "copy": copy, "final_urls": list(urls)})
    loc_set = str(spec.get("location_asset_set_id") or "").split("/")[-1].strip()
    if loc_set and not loc_set.isdigit():
        errs.append("location_asset_set_id harus angka (ID grup lokasi dari asset_set).")
    if errs:
        raise ValueError("Rancangan kampanye belum valid:\n- " + "\n- ".join(errs))
    return {"location_asset_set_id": loc_set or None, "name": name, "daily_budget": budget, "geo_ids": geo_ids, "bidding": bidding,
            "max_cpc": float(cpc_cap) if cpc_cap else None, "languages": langs, "schedule": schedule,
            "negatives": negatives, "groups": groups}


def summary(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "nama": p["name"], "status_awal": "DIJEDA", "budget_harian": p["daily_budget"],
        "jaringan": "Hanya Google Search (Search Partners OFF, Display OFF) — tidak tayang di YouTube/aplikasi/game",
        "ai_max": False, "mode_lokasi": "PRESENCE (Kehadiran)",
        "bidding": p["bidding"] + (f" (CPC maks Rp{p['max_cpc']:,.0f})".replace(",", ".") if p["max_cpc"] else ""),
        "lokasi_geo_ids": p["geo_ids"], "bahasa": p["languages"],
        "aset_lokasi_maps": (f"hanya grup lokasi {p['location_asset_set_id']}" if p.get("location_asset_set_id")
                             else "semua aset lokasi akun"),
        "jadwal": [fmt_window(w) for w in p["schedule"]] or "24 jam",
        "kata_kunci_negatif": [k["text"] for k in p["negatives"]],
        "grup_iklan": [{"nama": g["name"], "kata_kunci": [f"{k['text']} [{k['match_type']}]" for k in g["keywords"]],
                        "judul": len(g["copy"].headlines), "deskripsi": len(g["copy"].descriptions),
                        "final_url": g["final_urls"][0]} for g in p["groups"]],
    }


class CampaignBuilder:
    def __init__(self, mutator: Mutator):
        self.m = mutator

    @property
    def gc(self):
        return self.m.gc

    def _op(self):
        return self.gc.get_type("MutateOperation")

    def operations(self, cid: str, p: dict[str, Any]) -> list:
        gc, e = self.gc, self.gc.enums
        ops = []
        budget_rn = f"customers/{cid}/campaignBudgets/-1"
        camp_rn = f"customers/{cid}/campaigns/-2"

        o = self._op()
        b = o.campaign_budget_operation.create
        b.resource_name = budget_rn
        b.name = f"{p['name']} budget"
        b.amount_micros = to_micros(p["daily_budget"])
        b.delivery_method = e.BudgetDeliveryMethodEnum.STANDARD
        b.explicitly_shared = False
        ops.append(o)

        o = self._op()
        c = o.campaign_operation.create
        c.resource_name = camp_rn
        c.name = p["name"]
        c.status = e.CampaignStatusEnum.PAUSED
        c.advertising_channel_type = e.AdvertisingChannelTypeEnum.SEARCH
        c.campaign_budget = budget_rn
        c.network_settings.target_google_search = True
        c.network_settings.target_search_network = False
        c.network_settings.target_content_network = False
        c.network_settings.target_partner_search_network = False
        c.geo_target_type_setting.positive_geo_target_type = e.PositiveGeoTargetTypeEnum.PRESENCE
        c.geo_target_type_setting.negative_geo_target_type = e.NegativeGeoTargetTypeEnum.PRESENCE
        c.contains_eu_political_advertising = e.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
        # Strategi bidding adalah oneof: pesan harus DIPASANG (bukan hanya diisi 0) agar Google melihatnya.
        if p["bidding"] == "MAXIMIZE_CLICKS":
            ts = gc.get_type("TargetSpend")
            if p["max_cpc"]:
                ts.cpc_bid_ceiling_micros = to_micros(p["max_cpc"])
            gc.copy_from(c.target_spend, ts)
        else:
            gc.copy_from(c.maximize_conversions, gc.get_type("MaximizeConversions"))
        ops.append(o)

        def crit():
            o = self._op()
            cc = o.campaign_criterion_operation.create
            cc.campaign = camp_rn
            ops.append(o)
            return cc

        for g in p["geo_ids"]:
            crit().location.geo_target_constant = f"geoTargetConstants/{g}"
        for lang in p["languages"]:
            crit().language.language_constant = f"languageConstants/{lang}"
        for w in p["schedule"]:
            s = crit().ad_schedule
            s.day_of_week = getattr(e.DayOfWeekEnum, w["day"])
            s.start_hour, s.end_hour = w["start"][0], w["end"][0]
            s.start_minute = getattr(e.MinuteOfHourEnum, MINUTES[w["start"][1]])
            s.end_minute = getattr(e.MinuteOfHourEnum, MINUTES[w["end"][1]])
        for k in p["negatives"]:
            cc = crit()
            cc.negative = True
            cc.keyword.text = k["text"]
            cc.keyword.match_type = getattr(e.KeywordMatchTypeEnum, k["match_type"])

        if p.get("location_asset_set_id"):
            # Grup lokasi di level kampanye: hanya lokasi di grup ini yang tampil (bukan semua lokasi akun).
            o = self._op()
            cas = o.campaign_asset_set_operation.create
            cas.campaign = camp_rn
            cas.asset_set = f"customers/{cid}/assetSets/{p['location_asset_set_id']}"
            ops.append(o)

        for i, g in enumerate(p["groups"]):
            ag_rn = f"customers/{cid}/adGroups/{-10 - i}"
            o = self._op()
            ag = o.ad_group_operation.create
            ag.resource_name = ag_rn
            ag.name = g["name"]
            ag.campaign = camp_rn
            ag.status = e.AdGroupStatusEnum.ENABLED
            ag.type_ = e.AdGroupTypeEnum.SEARCH_STANDARD
            ops.append(o)
            for k in g["keywords"]:
                o = self._op()
                kw = o.ad_group_criterion_operation.create
                kw.ad_group = ag_rn
                kw.status = e.AdGroupCriterionStatusEnum.ENABLED
                kw.keyword.text = k["text"]
                kw.keyword.match_type = getattr(e.KeywordMatchTypeEnum, k["match_type"])
                ops.append(o)
            o = self._op()
            ad = o.ad_group_ad_operation.create
            ad.ad_group = ag_rn
            ad.status = e.AdGroupAdStatusEnum.ENABLED
            ad.ad.final_urls.extend(g["final_urls"])
            r = ad.ad.responsive_search_ad
            self.m._fill_assets(r.headlines, g["copy"].headlines, rsa.HEADLINE_PINS)
            self.m._fill_assets(r.descriptions, g["copy"].descriptions, rsa.DESC_PINS)
            if g["copy"].path1:
                r.path1 = g["copy"].path1
            if g["copy"].path2:
                r.path2 = g["copy"].path2
            ops.append(o)
        return ops

    def create(self, cid: str, spec: dict[str, Any], confirm: bool) -> dict[str, Any]:
        p = plan(spec)
        notes = check_budget(self.m.safety, None, p["daily_budget"])
        dup = self.m.ads.search(cid, "SELECT campaign.id FROM campaign WHERE campaign.status != 'REMOVED' AND "
                                     f"campaign.name = '{p['name'].replace(chr(39), '')}'")
        if dup:
            raise ValueError(f"Sudah ada kampanye bernama '{p['name']}' (ID {dup[0].get('campaign.id')}). Pakai nama lain.")
        for g in p["groups"]:
            notes += g["copy"].warnings
        notes += ["Kampanye dibuat DIJEDA. " + (
                  "Aset lokasi dibatasi ke grup lokasi yang dipilih — hanya lokasi itu yang muncul di Google Maps."
                  if p.get("location_asset_set_id") else
                  "Aset lokasi (Google Business Profile) di level akun otomatis ikut — itulah yang membuat iklan bisa "
                  "muncul di Google Maps."),
                  "Iklan baru ditinjau Google (biasanya < 1 hari).",
                  "Aktifkan dengan set_status level='campaign' setelah dicek; lalu verify_campaign_is_live."]
        ops = self.operations(cid, p)

        def verify():
            rows = self.m.ads.search(cid, (
                "SELECT campaign.id, campaign.name, campaign.status, campaign.network_settings.target_search_network, "
                "campaign.network_settings.target_content_network, campaign_budget.amount_micros FROM campaign "
                f"WHERE campaign.name = '{p['name'].replace(chr(39), '')}' AND campaign.status != 'REMOVED'"))
            if not rows:
                return "Kampanye belum terbaca."
            r = rows[0]
            cid_ = r.get("campaign.id")
            n_ag = len(self.m.ads.search(cid, f"SELECT ad_group.id FROM ad_group WHERE campaign.id = {cid_}"))
            n_kw = len(self.m.ads.search(cid, "SELECT ad_group_criterion.criterion_id FROM ad_group_criterion WHERE "
                                              f"campaign.id = {cid_} AND ad_group_criterion.type = 'KEYWORD' "
                                              "AND ad_group_criterion.negative = FALSE"))
            return {"campaign_id": cid_, "nama": r.get("campaign.name"), "status": r.get("campaign.status"),
                    "budget_harian": r.get("campaign_budget.amount"),
                    "search_partners": r.get("campaign.network_settings.target_search_network"),
                    "display": r.get("campaign.network_settings.target_content_network"),
                    "grup_iklan": n_ag, "kata_kunci": n_kw}

        return self.m._finish(
            cid=cid, action="create_search_campaign", target={"kampanye": p["name"]}, before=None,
            after=summary(p), service="GoogleAdsService", method="mutate", operations=ops, confirm=confirm,
            notes=notes, verify=verify)
