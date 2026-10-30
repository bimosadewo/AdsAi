"""Buat kampanye Meta baru dari iklan yang SUDAH TERBUKTI — selalu dalam keadaan DIJEDA.

Cara kerja (tidak menebak pengaturan WhatsApp/halaman):
1. Baca iklan sumber → materi iklannya (creative) dipakai ulang apa adanya, jadi teks, gambar/video, dan bila
   materinya berupa postingan, like & komentar lama ikut tampil.
2. Baca set iklan sumber → tujuan optimasi, tujuan pesan (WhatsApp), halaman/nomor WA (promoted_object), dan
   targeting disalin; yang diubah hanya yang diminta (budget harian, umur, gender, penempatan).
3. Kampanye mengikuti tujuan kampanye sumber (mis. OUTCOME_ENGAGEMENT), budget di level set iklan (ABO).

Pratinjau memakai validate_only Meta. Saat dijalankan, bila satu langkah gagal, kampanye yang baru dibuat langsung
dihapus lagi supaya tidak ada sisa setengah jadi.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .meta import NO_CENTS, Meta, MetaError, account_id

CREATIVE_FIELDS = "id,name,adset_id,campaign_id,effective_status,creative{id,name,body,title,object_story_id,effective_object_story_id}"
ADSET_FIELDS = ("name,campaign_id,optimization_goal,billing_event,bid_strategy,bid_amount,promoted_object,"
                "destination_type,targeting,attribution_spec,daily_budget,lifetime_budget,is_dynamic_creative")
CAMPAIGN_FIELDS = "name,objective,special_ad_categories,buying_type,daily_budget,lifetime_budget,bid_strategy"

PLACEMENTS = {
    # Feed & Reels saja: tanpa Stories, Marketplace, kolom kanan, Audience Network, Messenger.
    "feed_reels": {"publisher_platforms": ["facebook", "instagram"],
                   "facebook_positions": ["feed", "facebook_reels"],
                   "instagram_positions": ["stream", "reels"]},
    "feed": {"publisher_platforms": ["facebook", "instagram"], "facebook_positions": ["feed"],
             "instagram_positions": ["stream"]},
}
PLACEMENT_KEYS = ("publisher_platforms", "facebook_positions", "instagram_positions", "messenger_positions",
                  "audience_network_positions", "device_platforms")
GENDERS = {"pria": [1], "laki-laki": [1], "male": [1], "wanita": [2], "perempuan": [2], "female": [2],
           "semua": None, "all": None}


def to_minor(amount: float, currency: str) -> int:
    """Nilai budget untuk Meta: IDR dkk. dalam satuan utuh, mata uang lain dalam sen."""
    return int(round(amount * (1 if currency.upper() in NO_CENTS else 100)))


def claims(text: str) -> list[str]:
    """Kalimat di teks iklan lama yang mungkin sudah tidak berlaku (pengalaman X tahun, diskon, tahun lama)."""
    out = []
    t = text or ""
    for m in re.finditer(r"\b\d{1,2}\s*(?:\+\s*)?tahun\b", t, re.I):
        out.append(f"klaim '{m.group(0)}'")
    if re.search(r"\b(diskon|promo|potongan|cashback|gratis ongkir|harga spesial)\b|\d+\s*%", t, re.I):
        out.append("menyebut promo/diskon")
    for y in sorted(set(re.findall(r"\b(20[12]\d)\b", t))):
        if int(y) < date.today().year:
            out.append(f"menyebut tahun {y}")
    return out


def targeting_for(source: dict, spec: dict) -> tuple[dict, list[str]]:
    t = {k: v for k, v in (source or {}).items()}
    notes = []
    if spec.get("age_min") is not None:
        t["age_min"] = int(spec["age_min"])
    if spec.get("age_max") is not None:
        t["age_max"] = int(spec["age_max"])
    if not 13 <= t.get("age_min", 18) <= t.get("age_max", 65) <= 65:
        raise MetaError("Rentang umur harus 13–65 dan age_min ≤ age_max.")
    if spec.get("gender"):
        key = str(spec["gender"]).lower()
        if key not in GENDERS:
            raise MetaError("gender: pria | wanita | semua.")
        if GENDERS[key] is None:
            t.pop("genders", None)
        else:
            t["genders"] = GENDERS[key]
    place = spec.get("placements")
    if place:
        if place == "otomatis":
            for k in PLACEMENT_KEYS:
                t.pop(k, None)
        elif place in PLACEMENTS:
            for k in PLACEMENT_KEYS:
                t.pop(k, None)
            t.update(PLACEMENTS[place])
        else:
            raise MetaError("placements: feed_reels | feed | otomatis.")
    if (t.get("targeting_automation") or {}).get("advantage_audience") == 1:
        notes.append("Set iklan sumber memakai Advantage+ audience: umur/gender dianggap saran, Meta boleh "
                     "menjangkau di luar itu.")
    return t, notes


def describe_targeting(t: dict) -> dict[str, Any]:
    geo = t.get("geo_locations") or {}
    places = [*(f"FB {p}" for p in t.get("facebook_positions", [])),
              *(f"IG {p}" for p in t.get("instagram_positions", []))]
    return {"umur": f"{t.get('age_min', 18)}–{t.get('age_max', 65)}",
            "gender": {(1,): "pria", (2,): "wanita"}.get(tuple(t.get("genders") or []), "semua"),
            "lokasi": geo.get("countries") or [c.get("name") or c.get("key") for c in
                                              (geo.get("regions") or []) + (geo.get("cities") or [])],
            "penempatan": places or ("otomatis (Advantage+)" if not t.get("publisher_platforms")
                                     else t.get("publisher_platforms"))}


class Launcher:
    def __init__(self, api: Meta, act: str, currency: str):
        self.api, self.act, self.currency = api, account_id(act), currency

    def plan(self, name: str, ad_sets: list[dict], source_adset: str | None = None) -> dict[str, Any]:
        if not name or not name.strip():
            raise MetaError("Nama kampanye wajib diisi.")
        if not ad_sets:
            raise MetaError("Isi minimal satu set iklan: [{name, daily_budget, ads: [ID iklan terbukti]}].")
        ads_cache: dict[str, dict] = {}
        for s in ad_sets:
            if not s.get("name") or not s.get("ads"):
                raise MetaError("Setiap set iklan butuh name dan ads (ID iklan sumber).")
            if not s.get("daily_budget") or float(s["daily_budget"]) <= 0:
                raise MetaError(f"Set '{s['name']}': daily_budget harus > 0.")
            for ad_id in s["ads"]:
                if str(ad_id) not in ads_cache:
                    ads_cache[str(ad_id)] = self.api.get(str(ad_id), CREATIVE_FIELDS)
        first = ads_cache[str(ad_sets[0]["ads"][0])]
        src_set = self.api.get(str(source_adset or first["adset_id"]), ADSET_FIELDS)
        src_camp = self.api.get(src_set["campaign_id"], CAMPAIGN_FIELDS)
        if src_set.get("is_dynamic_creative"):
            raise MetaError("Set iklan sumber memakai Dynamic Creative — tidak bisa disalin otomatis. Pilih set lain.")

        notes: list[str] = []
        campaign = {"name": name.strip(), "objective": src_camp["objective"], "status": "PAUSED",
                    "buying_type": src_camp.get("buying_type") or "AUCTION",
                    "special_ad_categories": src_camp.get("special_ad_categories") or [],
                    "is_adset_budget_sharing_enabled": False}
        sets = []
        for s in ad_sets:
            targeting, tnotes = targeting_for(src_set.get("targeting") or {}, s)
            notes += [n for n in tnotes if n not in notes]
            fields = {"name": s["name"].strip(), "status": "PAUSED",
                      "daily_budget": to_minor(float(s["daily_budget"]), self.currency),
                      "billing_event": src_set.get("billing_event") or "IMPRESSIONS",
                      "optimization_goal": src_set["optimization_goal"],
                      "bid_strategy": src_set.get("bid_strategy") or "LOWEST_COST_WITHOUT_CAP",
                      "promoted_object": src_set.get("promoted_object"),
                      "destination_type": src_set.get("destination_type"),
                      "attribution_spec": src_set.get("attribution_spec"),
                      "targeting": targeting}
            if src_set.get("bid_amount") and fields["bid_strategy"] != "LOWEST_COST_WITHOUT_CAP":
                fields["bid_amount"] = src_set["bid_amount"]
            ads = []
            for ad_id in s["ads"]:
                src = ads_cache[str(ad_id)]
                cr = src.get("creative") or {}
                if not cr.get("id"):
                    raise MetaError(f"Iklan {ad_id} tidak punya materi (creative) yang bisa dipakai ulang.")
                text = f"{cr.get('title') or ''} {cr.get('body') or ''}"
                ads.append({"fields": {"name": src.get("name") or cr.get("name"), "status": "PAUSED",
                                       "creative": {"creative_id": cr["id"]}},
                            "sumber": ad_id, "teks": (cr.get("body") or "")[:300],
                            "postingan_lama": bool(cr.get("effective_object_story_id") or cr.get("object_story_id")),
                            "cek": claims(text)})
            sets.append({"fields": fields, "ads": ads})
        if src_camp.get("daily_budget") or src_camp.get("lifetime_budget"):
            notes.append("Kampanye sumber memakai budget kampanye (CBO); kampanye baru memakai budget per set iklan "
                         "(ABO) sesuai rencana.")
        for s in sets:
            for a in s["ads"]:
                if a["cek"]:
                    notes.append(f"Iklan '{a['fields']['name']}': {', '.join(a['cek'])} — pastikan masih berlaku. "
                                 "Materi dipakai apa adanya; bila perlu diubah, edit di Ads Manager setelah dibuat.")
        return {"campaign": campaign, "sets": sets, "source_set": src_set, "source_campaign": src_camp,
                "notes": notes}

    def summary(self, p: dict) -> dict[str, Any]:
        total = sum(s["fields"]["daily_budget"] for s in p["sets"])
        div = 1 if self.currency.upper() in NO_CENTS else 100
        return {
            "kampanye": {"nama": p["campaign"]["name"], "tujuan": p["campaign"]["objective"], "status": "DIJEDA",
                         "budget": "per set iklan (ABO)", "total_harian": total / div},
            "disalin_dari": {"set_iklan": p["source_set"].get("name"), "kampanye": p["source_campaign"].get("name"),
                             "optimasi": p["source_set"].get("optimization_goal"),
                             "tujuan_pesan": p["source_set"].get("destination_type")},
            "set_iklan": [{"nama": s["fields"]["name"], "budget_harian": s["fields"]["daily_budget"] / div,
                           "target": describe_targeting(s["fields"]["targeting"]),
                           "iklan": [{"nama": a["fields"]["name"], "dari_iklan": a["sumber"],
                                      "pakai_postingan_lama": a["postingan_lama"], "teks": a["teks"]}
                                     for a in s["ads"]]} for s in p["sets"]],
        }

    def validate(self, p: dict) -> list[str]:
        """validate_only di Meta: kampanye; set iklan & iklan diuji terhadap kampanye/set sumber (tujuan sama)."""
        checked = []
        self.api.create(self.act, "campaigns", p["campaign"], validate_only=True)
        checked.append("kampanye")
        cbo = p["source_campaign"].get("daily_budget") or p["source_campaign"].get("lifetime_budget")
        for s in p["sets"]:
            if not cbo:
                self.api.create(self.act, "adsets", {**s["fields"], "campaign_id": p["source_set"]["campaign_id"]},
                                validate_only=True)
                checked.append(f"set '{s['fields']['name']}'")
            for a in s["ads"]:
                self.api.create(self.act, "ads", {**a["fields"], "adset_id": p["source_set"]["id"]},
                                validate_only=True)
        if any(s["ads"] for s in p["sets"]):
            checked.append("iklan")
        return checked

    def execute(self, p: dict) -> dict[str, Any]:
        camp = self.api.create(self.act, "campaigns", p["campaign"])
        cid = camp["id"]
        created: dict[str, Any] = {"kampanye": cid, "set_iklan": []}
        try:
            for s in p["sets"]:
                sid = self.api.create(self.act, "adsets", {**s["fields"], "campaign_id": cid})["id"]
                ad_ids = [self.api.create(self.act, "ads", {**a["fields"], "adset_id": sid})["id"] for a in s["ads"]]
                created["set_iklan"].append({"id": sid, "iklan": ad_ids})
        except MetaError as ex:
            try:
                self.api.delete(cid)
                rolled = "Kampanye setengah jadi sudah dihapus lagi; akun kembali seperti semula."
            except MetaError:
                rolled = f"PERHATIAN: kampanye {cid} gagal dihapus otomatis — hapus manual di Ads Manager."
            raise MetaError(f"{ex} || Dibatalkan: {rolled}") from ex
        return created

    def verify(self, campaign_id: str) -> dict[str, Any]:
        c = self.api.get(campaign_id, "name,effective_status,adsets{name,daily_budget,effective_status},"
                                      "ads{name,effective_status}")
        div = 1 if self.currency.upper() in NO_CENTS else 100
        return {"kampanye": c.get("name"), "status": c.get("effective_status"),
                "set_iklan": [{"nama": s.get("name"), "status": s.get("effective_status"),
                               "budget_harian": float(s.get("daily_budget") or 0) / div}
                              for s in (c.get("adsets") or {}).get("data", [])],
                "iklan": [{"nama": a.get("name"), "status": a.get("effective_status")}
                          for a in (c.get("ads") or {}).get("data", [])]}
