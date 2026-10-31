"""Ekstensi (aset) iklan: sitelink, callout, structured snippet, telepon.

- Validasi murni (tanpa API) mengikuti batas Google & praktik terbaik.
- Tambah = buat aset + tautkan ke kampanye/grup iklan dalam SATU request atomik (GoogleAdsService.mutate).
- Ubah  = buat aset baru + tautkan + lepas tautan lama, juga atomik (aset Google tidak bisa diedit teksnya).
- Lepas = hapus tautan; aset tetap ada di library akun dan bisa dipakai lagi.
"""

from __future__ import annotations

import re
from typing import Any

from .mutations import Mutator, _digits
from .rsa import validate_urls

TYPES = ("SITELINK", "CALLOUT", "STRUCTURED_SNIPPET", "CALL")
LINK_STATUSES = ("ENABLED", "PAUSED")
SITELINK_TEXT, SITELINK_DESC, CALLOUT_LEN, SNIPPET_VALUE = 25, 35, 25, 25
MAX_VISIBLE_SITELINKS = 8

# Header structured snippet resmi Google (string API) + alias Indonesia/enum.
SNIPPET_HEADERS = {
    "amenities": "Amenities", "fasilitas": "Amenities",
    "brands": "Brands", "merek": "Brands", "merk": "Brands",
    "courses": "Courses", "kursus": "Courses", "kelas": "Courses",
    "degree programs": "Degree programs", "degree_programs": "Degree programs", "program studi": "Degree programs",
    "destinations": "Destinations", "tujuan": "Destinations", "destinasi": "Destinations",
    "featured hotels": "Featured hotels", "featured_hotels": "Featured hotels",
    "insurance coverage": "Insurance coverage", "insurance_coverage": "Insurance coverage", "asuransi": "Insurance coverage",
    "models": "Models", "model": "Models",
    "neighborhoods": "Neighborhoods", "lingkungan": "Neighborhoods", "area": "Neighborhoods",
    "service catalog": "Service catalog", "service_catalog": "Service catalog", "services": "Service catalog",
    "layanan": "Service catalog", "jasa": "Service catalog",
    "shows": "Shows", "acara": "Shows",
    "styles": "Styles", "gaya": "Styles",
    "types": "Types", "jenis": "Types", "tipe": "Types", "produk": "Types",
}
CTA_WORDS = re.compile(r"\b(klik|click|beli|buy|pesan sekarang|order sekarang|order now|hubungi|telepon|call|"
                       r"daftar sekarang|sign up|kunjungi)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Validasi
# ---------------------------------------------------------------------------

def validate_sitelinks(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    out, errors, warnings = [], [], []
    seen_text: set[str] = set()
    urls: list[str] = []
    for i, raw in enumerate(items, 1):
        text = " ".join(str(raw.get("link_text") or raw.get("text") or "").split())
        url = str(raw.get("final_url") or raw.get("url") or "").strip()
        d1 = " ".join(str(raw.get("description1") or "").split())
        d2 = " ".join(str(raw.get("description2") or "").split())
        if not text:
            errors.append(f"Sitelink #{i}: link_text kosong.")
        elif len(text) > SITELINK_TEXT:
            errors.append(f"Sitelink #{i}: link_text terlalu panjang ({len(text)}/{SITELINK_TEXT}): {text!r}")
        if not url:
            errors.append(f"Sitelink #{i}: final_url wajib diisi.")
        errors += [f"Sitelink #{i}: {e}" for e in validate_urls([url] if url else [])]
        if bool(d1) != bool(d2):
            errors.append(f"Sitelink #{i}: description1 dan description2 harus diisi berpasangan.")
        for name, d in (("description1", d1), ("description2", d2)):
            if len(d) > SITELINK_DESC:
                errors.append(f"Sitelink #{i}: {name} terlalu panjang ({len(d)}/{SITELINK_DESC}): {d!r}")
        if text.lower() in seen_text:
            errors.append(f"Sitelink #{i}: teks duplikat {text!r}.")
        seen_text.add(text.lower())
        if url and url in urls:
            warnings.append(f"Sitelink #{i} memakai URL yang sama dengan sitelink lain — arahkan ke halaman berbeda.")
        urls.append(url)
        if not d1:
            warnings.append(f"Sitelink #{i} tanpa deskripsi — deskripsi membuat sitelink tampil lebih besar.")
        out.append({"link_text": text, "final_url": url, "description1": d1 or None, "description2": d2 or None})
    return out, errors, warnings


def validate_callouts(items: list[str]) -> tuple[list[str], list[str], list[str]]:
    out, errors, warnings = [], [], []
    seen: set[str] = set()
    for i, raw in enumerate(items, 1):
        text = " ".join(str(raw).split())
        if not text:
            errors.append(f"Callout #{i} kosong.")
            continue
        if len(text) > CALLOUT_LEN:
            errors.append(f"Callout #{i} terlalu panjang ({len(text)}/{CALLOUT_LEN}): {text!r}")
        if text.endswith(".") or "!" in text:
            errors.append(f"Callout #{i} tidak boleh diakhiri titik atau memakai tanda seru: {text!r}")
        if CTA_WORDS.search(text):
            warnings.append(f"Callout #{i} berbentuk ajakan ({text!r}) — callout sebaiknya fakta/keunggulan, "
                            "mis. 'Stok Ready', 'Harga Pabrik'.")
        if text.lower() in seen:
            errors.append(f"Callout #{i} duplikat: {text!r}")
        seen.add(text.lower())
        out.append(text)
    return out, errors, warnings


def normalize_header(header: str) -> str:
    key = " ".join(str(header).replace("-", " ").split()).lower()
    if key in SNIPPET_HEADERS:
        return SNIPPET_HEADERS[key]
    if key.replace(" ", "_") in SNIPPET_HEADERS:
        return SNIPPET_HEADERS[key.replace(" ", "_")]
    raise ValueError(
        f"Header snippet '{header}' tidak dikenal. Pilihan: Jenis/Types, Merek/Brands, Layanan/Service catalog, "
        "Model/Models, Gaya/Styles, Kursus/Courses, Destinasi/Destinations, Fasilitas/Amenities, "
        "Lingkungan/Neighborhoods, Asuransi/Insurance coverage, Program studi/Degree programs, Acara/Shows, "
        "Featured hotels."
    )


def validate_snippets(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    out, errors, warnings = [], [], []
    for i, raw in enumerate(items, 1):
        try:
            header = normalize_header(raw.get("header", ""))
        except ValueError as ex:
            errors.append(f"Snippet #{i}: {ex}")
            continue
        values = [" ".join(str(v).split()) for v in raw.get("values") or [] if str(v).strip()]
        if not 3 <= len(values) <= 10:
            errors.append(f"Snippet #{i} ({header}): jumlah nilai harus 3-10, didapat {len(values)}.")
        for v in values:
            if len(v) > SNIPPET_VALUE:
                errors.append(f"Snippet #{i}: nilai terlalu panjang ({len(v)}/{SNIPPET_VALUE}): {v!r}")
        if len({v.lower() for v in values}) != len(values):
            errors.append(f"Snippet #{i}: ada nilai duplikat.")
        out.append({"header": header, "values": values})
    return out, errors, warnings


def normalize_phone(phone: str, country_code: str = "ID") -> str:
    """Nomor Indonesia: '+62 812-3456-7890' / '6281234567890' -> '081234567890'."""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if country_code.upper() == "ID":
        if digits.startswith("62"):
            digits = "0" + digits[2:]
        if not digits.startswith("0"):
            digits = "0" + digits
    if not 8 <= len(digits) <= 15:
        raise ValueError(f"Nomor telepon tidak valid: {phone!r}")
    return digits


# ---------------------------------------------------------------------------
# Operasi
# ---------------------------------------------------------------------------

class Extensions:
    def __init__(self, mutator: Mutator):
        self.m = mutator

    @property
    def gc(self):
        return self.m.gc

    # ----- baca -----

    def list(self, cid: str, campaign_id: str) -> list[dict[str, Any]]:
        camp = _digits(campaign_id, "campaign_id")
        fields = ("asset.id, asset.type, asset.final_urls, asset.sitelink_asset.link_text, "
                  "asset.sitelink_asset.description1, asset.sitelink_asset.description2, "
                  "asset.callout_asset.callout_text, asset.structured_snippet_asset.header, "
                  "asset.structured_snippet_asset.values, asset.call_asset.phone_number, "
                  "asset.call_asset.country_code")
        types = "('SITELINK', 'CALLOUT', 'STRUCTURED_SNIPPET', 'CALL')"
        rows = [dict(r, level="campaign") for r in self.m.ads.search(cid, (
            # campaign.id wajib ikut di SELECT bila dipakai di WHERE (EXPECTED_REFERENCED_FIELD_IN_SELECT_CLAUSE, v25).
            f"SELECT campaign.id, campaign_asset.field_type, campaign_asset.status, {fields} FROM campaign_asset "
            f"WHERE campaign.id = {camp} AND campaign_asset.status != 'REMOVED' "
            f"AND campaign_asset.field_type IN {types}"))]
        rows += [dict(r, level="ad_group") for r in self.m.ads.search(cid, (
            f"SELECT campaign.id, ad_group.id, ad_group.name, ad_group_asset.field_type, ad_group_asset.status, "
            f"{fields} "
            f"FROM ad_group_asset WHERE campaign.id = {camp} AND ad_group_asset.status != 'REMOVED' "
            f"AND ad_group_asset.field_type IN {types}"))]
        out = []
        for r in rows:
            prefix = "campaign_asset" if r["level"] == "campaign" else "ad_group_asset"
            ft = r.get(f"{prefix}.field_type")
            item = {"asset_id": r.get("asset.id"), "jenis": ft, "status": r.get(f"{prefix}.status"), "level": r["level"]}
            if r["level"] == "ad_group":
                item["ad_group_id"], item["ad_group"] = r.get("ad_group.id"), r.get("ad_group.name")
            if ft == "SITELINK":
                item.update(teks=r.get("asset.sitelink_asset.link_text"),
                            description1=r.get("asset.sitelink_asset.description1"),
                            description2=r.get("asset.sitelink_asset.description2"),
                            final_url=(r.get("asset.final_urls") or [None])[0])
            elif ft == "CALLOUT":
                item["teks"] = r.get("asset.callout_asset.callout_text")
            elif ft == "STRUCTURED_SNIPPET":
                item.update(header=r.get("asset.structured_snippet_asset.header"),
                            values=r.get("asset.structured_snippet_asset.values") or [])
            elif ft == "CALL":
                item.update(telepon=r.get("asset.call_asset.phone_number"), negara=r.get("asset.call_asset.country_code"))
            out.append(item)
        return out

    @staticmethod
    def _key(item: dict[str, Any]) -> str:
        if item["jenis"] == "STRUCTURED_SNIPPET":
            return f"{item.get('header', '')}|{','.join(item.get('values') or [])}".lower()
        if item["jenis"] == "CALL":
            return "".join(ch for ch in str(item.get("telepon") or "") if ch.isdigit())[-9:]
        return str(item.get("teks") or "").lower()

    # ----- builder -----

    def _asset_op(self, cid: str, temp_id: int, kind: str, spec: dict[str, Any]):
        mo = self.gc.get_type("MutateOperation")
        asset = mo.asset_operation.create
        asset.resource_name = self.gc.get_service("AssetService").asset_path(cid, str(temp_id))
        if kind == "SITELINK":
            asset.final_urls.append(spec["final_url"])
            asset.sitelink_asset.link_text = spec["link_text"]
            if spec.get("description1"):
                asset.sitelink_asset.description1 = spec["description1"]
                asset.sitelink_asset.description2 = spec["description2"]
        elif kind == "CALLOUT":
            asset.callout_asset.callout_text = spec["text"]
        elif kind == "STRUCTURED_SNIPPET":
            asset.structured_snippet_asset.header = spec["header"]
            asset.structured_snippet_asset.values.extend(spec["values"])
        elif kind == "CALL":
            asset.call_asset.phone_number = spec["phone"]
            asset.call_asset.country_code = spec["country_code"]
            if spec.get("conversion_action_id"):
                asset.call_asset.call_conversion_action = self.gc.get_service(
                    "ConversionActionService").conversion_action_path(cid, spec["conversion_action_id"])
                asset.call_asset.call_conversion_reporting_state = (
                    self.gc.enums.CallConversionReportingStateEnum.USE_RESOURCE_LEVEL_CALL_CONVERSION_ACTION)
        return mo

    def _link_op(self, cid: str, asset_rn: str, kind: str, campaign_id: str, ad_group_id: str | None):
        mo = self.gc.get_type("MutateOperation")
        field_type = getattr(self.gc.enums.AssetFieldTypeEnum, kind)
        if ad_group_id:
            link = mo.ad_group_asset_operation.create
            link.ad_group = self.gc.get_service("AdGroupService").ad_group_path(cid, ad_group_id)
        else:
            link = mo.campaign_asset_operation.create
            link.campaign = self.gc.get_service("CampaignService").campaign_path(cid, campaign_id)
        link.asset = asset_rn
        link.field_type = field_type
        return mo

    def _unlink_op(self, cid: str, item: dict[str, Any], campaign_id: str):
        mo = self.gc.get_type("MutateOperation")
        if item["level"] == "ad_group":
            mo.ad_group_asset_operation.remove = self.gc.get_service("AdGroupAssetService").ad_group_asset_path(
                cid, item["ad_group_id"], item["asset_id"], item["jenis"])
        else:
            mo.campaign_asset_operation.remove = self.gc.get_service("CampaignAssetService").campaign_asset_path(
                cid, campaign_id, item["asset_id"], item["jenis"])
        return mo

    def _status_op(self, cid: str, item: dict[str, Any], campaign_id: str, status: str):
        mo = self.gc.get_type("MutateOperation")
        enum = getattr(self.gc.enums.AssetLinkStatusEnum, status)
        if item["level"] == "ad_group":
            op = mo.ad_group_asset_operation
            op.update.resource_name = self.gc.get_service("AdGroupAssetService").ad_group_asset_path(
                cid, item["ad_group_id"], item["asset_id"], item["jenis"])
        else:
            op = mo.campaign_asset_operation
            op.update.resource_name = self.gc.get_service("CampaignAssetService").campaign_asset_path(
                cid, campaign_id, item["asset_id"], item["jenis"])
        op.update.status = enum
        op.update_mask.paths.append("status")
        return mo

    # ----- tambah -----

    def add(self, cid: str, campaign_id: str, kind: str, specs: list[dict[str, Any]], confirm: bool,
            ad_group_id: str | None = None, warnings: list[str] | None = None) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        ag = _digits(ad_group_id, "ad_group_id") if ad_group_id else None
        existing = [e for e in self.list(cid, camp) if e["jenis"] == kind
                    and (e["level"] == "ad_group") == bool(ag) and (not ag or e.get("ad_group_id") == ag)]
        have = {self._key(e) for e in existing}
        fresh, skipped = [], []
        for spec in specs:
            probe = {"jenis": kind, "teks": spec.get("link_text") or spec.get("text"), "header": spec.get("header"),
                     "values": spec.get("values"), "telepon": spec.get("phone")}
            (skipped if self._key(probe) in have else fresh).append(spec)
        if not fresh:
            return {"status": "TIDAK_ADA_PERUBAHAN", "pesan": "Semua ekstensi sudah terpasang.", "dilewati": skipped}
        ops = []
        for i, spec in enumerate(fresh, 1):
            temp = -i
            ops.append(self._asset_op(cid, temp, kind, spec))
            ops.append(self._link_op(cid, self.gc.get_service("AssetService").asset_path(cid, str(temp)), kind, camp, ag))
        notes = list(warnings or [])
        if skipped:
            notes.append(f"{len(skipped)} ekstensi dilewati karena sudah terpasang.")
        if kind == "SITELINK" and len(existing) + len(fresh) > MAX_VISIBLE_SITELINKS:
            notes.append(f"Total sitelink akan {len(existing) + len(fresh)}; Google menampilkan maksimal "
                         f"±{MAX_VISIBLE_SITELINKS} — lepas yang lama/kurang relevan.")
        notes.append("Ekstensi baru ditinjau Google sebelum tampil.")
        level = {"campaign_id": camp, **({"ad_group_id": ag} if ag else {})}
        return self.m._finish(
            cid=cid, action=f"add_{kind.lower()}", target={**level, "jenis": kind},
            before={"jumlah": len(existing)}, after={"ditambahkan": fresh},
            service="GoogleAdsService", method="mutate", operations=ops, confirm=confirm, notes=notes,
            verify=lambda: {"jumlah": len([e for e in self.list(cid, camp) if e["jenis"] == kind])},
        )

    # ----- ubah (ganti atomik) -----

    def _find(self, cid: str, campaign_id: str, asset_ids: list[str]) -> list[dict[str, Any]]:
        ids = {str(_digits(a, "asset_id")) for a in asset_ids}
        items = [e for e in self.list(cid, campaign_id) if str(e["asset_id"]) in ids]
        missing = ids - {str(e["asset_id"]) for e in items}
        if missing:
            raise ValueError(f"Aset {sorted(missing)} tidak tertaut di kampanye {campaign_id} (lihat list_extensions).")
        return items

    def replace(self, cid: str, campaign_id: str, asset_id: str, spec: dict[str, Any], confirm: bool,
                warnings: list[str] | None = None) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        (old,) = self._find(cid, camp, [asset_id])
        kind = old["jenis"]
        ag = old.get("ad_group_id") if old["level"] == "ad_group" else None
        ops = [self._asset_op(cid, -1, kind, spec),
               self._link_op(cid, self.gc.get_service("AssetService").asset_path(cid, "-1"), kind, camp, ag),
               self._unlink_op(cid, old, camp)]
        return self.m._finish(
            cid=cid, action=f"update_{kind.lower()}", target={"campaign_id": camp, "asset_id": old["asset_id"], "jenis": kind},
            before=old, after=spec, service="GoogleAdsService", method="mutate", operations=ops, confirm=confirm,
            notes=list(warnings or []) + ["Aset lama dilepas (tetap di library akun); aset baru ditinjau Google."],
            verify=lambda: [e for e in self.list(cid, camp) if e["jenis"] == kind],
        )

    # ----- status & lepas -----

    def set_status(self, cid: str, campaign_id: str, asset_ids: list[str], status: str, confirm: bool) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        status = status.upper()
        if status not in LINK_STATUSES:
            raise ValueError(f"status harus {LINK_STATUSES}")
        items = self._find(cid, camp, asset_ids)
        ops = [self._status_op(cid, it, camp, status) for it in items]
        return self.m._finish(
            cid=cid, action="set_extension_status", target={"campaign_id": camp, "asset_ids": [i["asset_id"] for i in items]},
            before=[{k: i.get(k) for k in ("asset_id", "jenis", "status")} for i in items], after={"status": status},
            service="GoogleAdsService", method="mutate", operations=ops, confirm=confirm,
            verify=lambda: [{k: e.get(k) for k in ("asset_id", "jenis", "status")}
                            for e in self.list(cid, camp) if str(e["asset_id"]) in {str(i["asset_id"]) for i in items}],
        )

    def remove(self, cid: str, campaign_id: str, asset_ids: list[str], confirm: bool) -> dict:
        camp = _digits(campaign_id, "campaign_id")
        if not asset_ids:
            raise ValueError("asset_ids wajib diisi (lihat list_extensions). Melepas semua sekaligus tidak didukung.")
        items = self._find(cid, camp, asset_ids)
        ops = [self._unlink_op(cid, it, camp) for it in items]
        return self.m._finish(
            cid=cid, action="remove_extensions", target={"campaign_id": camp, "asset_ids": [i["asset_id"] for i in items]},
            before=items, after=None, service="GoogleAdsService", method="mutate", operations=ops, confirm=confirm,
            notes=["Hanya tautan yang dilepas; aset tetap ada di library akun dan statistik historisnya tetap tersimpan."],
            verify=lambda: {"tersisa": len(self.list(cid, camp))},
        )


def build_spec(kind: str, fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validasi satu spesifikasi ekstensi dari field datar (dipakai untuk 'ubah'). Kembalikan (spec, peringatan)."""
    if kind == "SITELINK":
        items, errors, warnings = validate_sitelinks([fields])
        spec = items[0] if items else {}
    elif kind == "CALLOUT":
        items, errors, warnings = validate_callouts([fields.get("text") or ""])
        spec = {"text": items[0]} if items else {}
    elif kind == "STRUCTURED_SNIPPET":
        items, errors, warnings = validate_snippets([{"header": fields.get("header"), "values": fields.get("values")}])
        spec = items[0] if items else {}
    elif kind == "CALL":
        errors, warnings = [], []
        try:
            country = (fields.get("country_code") or "ID").upper()
            spec = {"phone": normalize_phone(fields.get("phone_number") or "", country), "country_code": country}
            if fields.get("conversion_action_id"):
                spec["conversion_action_id"] = _digits(fields["conversion_action_id"], "conversion_action_id")
        except ValueError as ex:
            errors, spec = [str(ex)], {}
    else:
        raise ValueError(f"Jenis ekstensi '{kind}' tidak didukung. Pilihan: {TYPES}")
    if errors:
        raise ValueError("Ekstensi belum valid:\n- " + "\n- ".join(errors))
    return spec, warnings
