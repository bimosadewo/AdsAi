"""Struktur kampanye (grup iklan, kata kunci, iklan RSA, ekstensi, negatif) + audit struktur otomatis."""

from __future__ import annotations

import math
from typing import Any, Protocol

INCLUDE = ("all", "summary", "keywords", "ads")
MIN_SITELINKS, MIN_CALLOUTS = 4, 4
MAX_KEYWORDS_PER_GROUP = 20
WEAK_STRENGTH = ("POOR", "AVERAGE")


class Searcher(Protocol):
    def search(self, customer_id: str, query: str) -> list[dict[str, Any]]: ...


def _id(value: Any, name: str) -> int:
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"{name} harus berupa angka ID, didapat: {value!r}")
    return int(text)


def queries(campaign_id: int) -> dict[str, str]:
    c = f"campaign.id = {campaign_id}"
    return {
        "campaign": (
            "SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
            "campaign.bidding_strategy_type, campaign_budget.amount_micros FROM campaign WHERE " + c
        ),
        "ad_groups": (
            "SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.type, ad_group.cpc_bid_micros "
            f"FROM ad_group WHERE {c} AND ad_group.status != 'REMOVED' ORDER BY ad_group.name"
        ),
        "keywords": (
            "SELECT ad_group.id, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, "
            "ad_group_criterion.keyword.match_type, ad_group_criterion.status, ad_group_criterion.negative, "
            "ad_group_criterion.cpc_bid_micros, ad_group_criterion.quality_info.quality_score "
            f"FROM ad_group_criterion WHERE {c} AND ad_group_criterion.type = 'KEYWORD' "
            "AND ad_group_criterion.status != 'REMOVED'"
        ),
        "ads": (
            "SELECT ad_group.id, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status, "
            "ad_group_ad.ad_strength, ad_group_ad.policy_summary.approval_status, ad_group_ad.ad.final_urls, "
            "ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
            "ad_group_ad.ad.responsive_search_ad.path1, ad_group_ad.ad.responsive_search_ad.path2 "
            f"FROM ad_group_ad WHERE {c} AND ad_group_ad.status != 'REMOVED'"
        ),
        "assets": (
            "SELECT campaign_asset.field_type, campaign_asset.status, asset.id, asset.sitelink_asset.link_text, "
            "asset.callout_asset.callout_text, asset.structured_snippet_asset.header, "
            "asset.structured_snippet_asset.values, asset.call_asset.phone_number "
            f"FROM campaign_asset WHERE {c} AND campaign_asset.status != 'REMOVED'"
        ),
        "negatives": (
            "SELECT campaign_criterion.criterion_id, campaign_criterion.keyword.text, "
            "campaign_criterion.keyword.match_type FROM campaign_criterion "
            f"WHERE {c} AND campaign_criterion.negative = TRUE AND campaign_criterion.type = 'KEYWORD'"
        ),
        "shared_lists": (
            "SELECT shared_set.id, shared_set.name, shared_set.member_count, campaign_shared_set.status "
            f"FROM campaign_shared_set WHERE {c} AND shared_set.type = 'NEGATIVE_KEYWORDS' "
            "AND campaign_shared_set.status = 'ENABLED'"
        ),
    }


def _ad(r: dict[str, Any]) -> dict[str, Any]:
    def texts(key: str) -> list[dict[str, Any]]:
        return [{"text": a.get("text"), **({"pin": a["pinned_field"]} if a.get("pinned_field") else {})}
                for a in (r.get(key) or [])]
    return {
        "ad_id": r.get("ad_group_ad.ad.id"),
        "jenis": r.get("ad_group_ad.ad.type"),
        "status": r.get("ad_group_ad.status"),
        "ad_strength": r.get("ad_group_ad.ad_strength"),
        "persetujuan": r.get("ad_group_ad.policy_summary.approval_status"),
        "final_urls": r.get("ad_group_ad.ad.final_urls") or [],
        "headlines": texts("ad_group_ad.ad.responsive_search_ad.headlines"),
        "descriptions": texts("ad_group_ad.ad.responsive_search_ad.descriptions"),
        "path1": r.get("ad_group_ad.ad.responsive_search_ad.path1"),
        "path2": r.get("ad_group_ad.ad.responsive_search_ad.path2"),
    }


def _keyword(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{r.get('ad_group.id')}~{r.get('ad_group_criterion.criterion_id')}",
        "text": r.get("ad_group_criterion.keyword.text"),
        "match_type": r.get("ad_group_criterion.keyword.match_type"),
        "status": r.get("ad_group_criterion.status"),
        "cpc_bid": r.get("ad_group_criterion.cpc_bid"),
        "quality_score": r.get("ad_group_criterion.quality_info.quality_score"),
    }


def _extensions(assets: list[dict[str, Any]]) -> dict[str, list]:
    ext: dict[str, list] = {"sitelink": [], "callout": [], "snippet": [], "telepon": [], "lainnya": []}
    for a in assets:
        if a.get("campaign_asset.status") not in (None, "ENABLED"):
            continue
        ft = a.get("campaign_asset.field_type")
        if ft == "SITELINK":
            ext["sitelink"].append(a.get("asset.sitelink_asset.link_text"))
        elif ft == "CALLOUT":
            ext["callout"].append(a.get("asset.callout_asset.callout_text"))
        elif ft == "STRUCTURED_SNIPPET":
            ext["snippet"].append({"header": a.get("asset.structured_snippet_asset.header"),
                                   "values": a.get("asset.structured_snippet_asset.values") or []})
        elif ft == "CALL":
            ext["telepon"].append(a.get("asset.call_asset.phone_number"))
        else:
            ext["lainnya"].append(ft)
    return ext


def audit(campaign: dict[str, Any], groups: list[dict[str, Any]], ext: dict[str, list]) -> list[dict[str, str]]:
    """Temuan struktur: level PERINGATAN / INFO dengan pesan Bahasa Indonesia."""
    out: list[dict[str, str]] = []

    def add(level: str, msg: str) -> None:
        out.append({"level": level, "pesan": msg})

    if campaign.get("jenis") == "SEARCH":
        if len(ext["sitelink"]) < MIN_SITELINKS:
            add("PERINGATAN", f"Sitelink baru {len(ext['sitelink'])}; pasang minimal {MIN_SITELINKS}.")
        if len(ext["callout"]) < MIN_CALLOUTS:
            add("PERINGATAN", f"Callout baru {len(ext['callout'])}; pasang 4-6 (mis. 'Gratis Ongkir', 'Stok Ready').")
        if not ext["snippet"]:
            add("INFO", "Belum ada structured snippet (mis. Jenis: Woven, Non Woven, HDPE).")
        if not ext["telepon"]:
            add("INFO", "Belum ada ekstensi telepon — berguna untuk lead B2B.")
    for g in groups:
        if g["status"] != "ENABLED":
            continue
        name = g["nama"]
        active_kw = [k for k in g.get("_all_keywords", []) if k["status"] == "ENABLED"]
        active_ads = [a for a in g.get("_all_ads", []) if a["status"] == "ENABLED"]
        if not active_kw and campaign.get("jenis") == "SEARCH":
            add("PERINGATAN", f"Grup iklan '{name}' aktif tanpa kata kunci aktif.")
        if not active_ads:
            add("PERINGATAN", f"Grup iklan '{name}' aktif tanpa iklan aktif.")
        if len(active_kw) > MAX_KEYWORDS_PER_GROUP:
            add("INFO", f"Grup iklan '{name}' punya {len(active_kw)} kata kunci aktif — kemungkinan terlalu luas; "
                        "pecah per produk/tema agar iklan & landing page lebih relevan.")
        for a in active_ads:
            if a["ad_strength"] in WEAK_STRENGTH:
                add("PERINGATAN", f"Iklan {a['ad_id']} di '{name}' ad strength {a['ad_strength']} — tambah headline "
                                  "beragam (10-15) dan 4 deskripsi.")
            if a["persetujuan"] == "DISAPPROVED":
                add("PERINGATAN", f"Iklan {a['ad_id']} di '{name}' DITOLAK Google — cek kebijakan di UI.")
        low_qs = [k["text"] for k in active_kw if k.get("quality_score") and int(k["quality_score"]) <= 4]
        if low_qs:
            add("INFO", f"'{name}': {len(low_qs)} kata kunci Quality Score ≤4 (mis. {', '.join(low_qs[:3])}).")
    return out


def assemble(
    data: dict[str, list[dict[str, Any]]],
    page: int = 1,
    page_size: int = 10,
    include: str = "all",
    ad_group_id: str | None = None,
) -> dict[str, Any]:
    if include not in INCLUDE:
        raise ValueError(f"include harus salah satu dari {INCLUDE}")
    if not data.get("campaign"):
        raise ValueError("Kampanye tidak ditemukan.")
    c = data["campaign"][0]
    campaign = {
        "id": c.get("campaign.id"),
        "nama": c.get("campaign.name"),
        "status": c.get("campaign.status"),
        "jenis": c.get("campaign.advertising_channel_type"),
        "bidding": c.get("campaign.bidding_strategy_type"),
        "budget_harian": c.get("campaign_budget.amount"),
    }
    kw_by_group: dict[str, list] = {}
    neg_by_group: dict[str, list] = {}
    for r in data.get("keywords", []):
        target = neg_by_group if r.get("ad_group_criterion.negative") else kw_by_group
        target.setdefault(r.get("ad_group.id"), []).append(_keyword(r))
    ads_by_group: dict[str, list] = {}
    for r in data.get("ads", []):
        ads_by_group.setdefault(r.get("ad_group.id"), []).append(_ad(r))

    groups = []
    for g in data.get("ad_groups", []):
        gid = g.get("ad_group.id")
        groups.append({
            "id": gid,
            "nama": g.get("ad_group.name"),
            "status": g.get("ad_group.status"),
            "cpc_bid": g.get("ad_group.cpc_bid"),
            "_all_keywords": kw_by_group.get(gid, []),
            "_all_ads": ads_by_group.get(gid, []),
            "_negatives": neg_by_group.get(gid, []),
        })
    ext = _extensions(data.get("assets", []))
    findings = audit(campaign, groups, ext)

    if ad_group_id:
        selected = [g for g in groups if g["id"] == str(ad_group_id)]
        if not selected:
            raise ValueError(f"Grup iklan {ad_group_id} tidak ada di kampanye ini.")
        total_pages, page = 1, 1
    else:
        page_size = max(1, min(int(page_size), 50))
        total_pages = max(1, math.ceil(len(groups) / page_size))
        page = max(1, min(int(page), total_pages))
        selected = groups[(page - 1) * page_size: page * page_size]

    def render(g: dict[str, Any]) -> dict[str, Any]:
        base = {k: v for k, v in g.items() if not k.startswith("_")}
        base["jumlah_kata_kunci"] = len(g["_all_keywords"])
        base["jumlah_iklan"] = len(g["_all_ads"])
        if include in ("all", "keywords"):
            base["kata_kunci"] = g["_all_keywords"]
            base["negatif_grup"] = g["_negatives"]
        if include in ("all", "ads"):
            base["iklan"] = g["_all_ads"]
        return base

    result: dict[str, Any] = {
        "kampanye": campaign,
        "ringkasan": {
            "grup_iklan": len(groups),
            "grup_iklan_aktif": sum(1 for g in groups if g["status"] == "ENABLED"),
            "kata_kunci": sum(len(g["_all_keywords"]) for g in groups),
            "iklan": sum(len(g["_all_ads"]) for g in groups),
            "negatif_kampanye": len(data.get("negatives", [])),
            "shared_negative_list": len(data.get("shared_lists", [])),
        },
        "grup_iklan": [render(g) for g in selected],
        "halaman": {"page": page, "total_pages": total_pages, "has_more": page < total_pages},
        "temuan": findings,
    }
    if page == 1 or ad_group_id:
        result["ekstensi"] = ext
        result["negatif_kampanye"] = [
            {"text": r.get("campaign_criterion.keyword.text"), "match_type": r.get("campaign_criterion.keyword.match_type")}
            for r in data.get("negatives", [])
        ]
        result["shared_negative_list"] = [
            {"id": r.get("shared_set.id"), "nama": r.get("shared_set.name"), "jumlah": r.get("shared_set.member_count")}
            for r in data.get("shared_lists", [])
        ]
    return result


def fetch_structure(ads: Searcher, cid: str, campaign_id: Any, **kwargs) -> dict[str, Any]:
    qs = queries(_id(campaign_id, "campaign_id"))
    data = {key: ads.search(cid, q) for key, q in qs.items()}
    return assemble(data, **kwargs)


def q_ad_performance(date_sql: str, campaign_id: Any = None, ad_group_id: Any = None) -> str:
    filters = [date_sql, "ad_group_ad.status != 'REMOVED'"]
    if campaign_id:
        filters.append(f"campaign.id = {_id(campaign_id, 'campaign_id')}")
    if ad_group_id:
        filters.append(f"ad_group.id = {_id(ad_group_id, 'ad_group_id')}")
    return (
        "SELECT campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, "
        "ad_group_ad.status, ad_group_ad.ad_strength, metrics.impressions, metrics.clicks, metrics.ctr, "
        "metrics.cost_micros, metrics.conversions, metrics.cost_per_conversion FROM ad_group_ad WHERE "
        + " AND ".join(filters) + " ORDER BY metrics.cost_micros DESC"
    )
