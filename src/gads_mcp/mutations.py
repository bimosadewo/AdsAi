"""Operasi yang mengubah akun Google Ads (Fase 2).

Alur setiap operasi:
  1. Ambil kondisi saat ini (untuk log "sebelum" dan pengecekan pengaman).
  2. Cek aturan pengaman AdsAi (mode baca-saja, batas budget, dll.).
  3. Kirim ke Google dengan validate_only=True  -> pratinjau (tidak ada yang berubah).
  4. Hanya bila confirm=True: kirim sungguhan, lalu tulis log audit.
"""

from __future__ import annotations

from typing import Any, Iterable

from google.ads.googleads.errors import GoogleAdsException

from .client import Ads, AdsError, _describe
from .safety import AuditLog, SafetyError, check_budget, ensure_writable, to_micros

MATCH_TYPES = ("EXACT", "PHRASE", "BROAD")
STATUSES = ("ENABLED", "PAUSED")
LEVELS = ("campaign", "ad_group", "keyword")
BID_STRATEGIES = ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CLICKS", "MANUAL_CPC")
COUNTING_TYPES = ("ONE_PER_CLICK", "MANY_PER_CLICK")


def _digits(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"{name} harus berupa angka ID, didapat: {value!r}")
    return text


def normalize_keywords(keywords: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Validasi & rapikan daftar kata kunci [{text, match_type}] (duplikat dibuang)."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kw in keywords:
        text = " ".join(str(kw.get("text", "")).lower().split())
        match = str(kw.get("match_type", "PHRASE")).upper()
        if not text:
            raise ValueError("Teks kata kunci tidak boleh kosong.")
        if match not in MATCH_TYPES:
            raise ValueError(f"match_type '{match}' tidak valid. Pilihan: {', '.join(MATCH_TYPES)}")
        if len(text) > 80 or len(text.split()) > 10:
            raise ValueError(f"Kata kunci terlalu panjang (maks 80 karakter / 10 kata): {text!r}")
        if (text, match) in seen:
            continue
        seen.add((text, match))
        out.append({"text": text, "match_type": match})
    if not out:
        raise ValueError("Daftar kata kunci kosong.")
    return out


class Mutator:
    def __init__(self, ads: Ads, audit: AuditLog):
        self.ads = ads
        self.audit = audit

    @property
    def safety(self):
        return self.ads.settings.safety

    @property
    def gc(self):
        return self.ads.client

    # ---------- infrastruktur ----------

    def _send(self, service: str, method: str, cid: str, operations: list, validate_only: bool) -> list[str]:
        svc = self.gc.get_service(service)
        request = {"customer_id": cid, "operations": operations, "validate_only": validate_only}
        try:
            response = getattr(svc, method)(request=request)
        except GoogleAdsException as ex:
            raise AdsError(_describe(ex)) from ex
        return [r.resource_name for r in response.results] if not validate_only else []

    def _finish(
        self,
        *,
        cid: str,
        action: str,
        target: dict,
        before: Any,
        after: Any,
        service: str,
        method: str,
        operations: list,
        confirm: bool,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        ensure_writable(self.safety)
        # Selalu validasi dulu ke Google; bila gagal, perubahan sungguhan tidak pernah dikirim.
        self._send(service, method, cid, operations, validate_only=True)
        base = {"customer_id": cid, "aksi": action, "target": target, "sebelum": before, "sesudah": after}
        if notes:
            base["catatan"] = notes
        if not confirm:
            return {
                "status": "PRATINJAU",
                **base,
                "pesan": "Lolos validasi Google, BELUM dijalankan. Tampilkan rencana ini ke pengguna; "
                "jalankan ulang dengan confirm=true setelah pengguna setuju.",
            }
        resources = self._send(service, method, cid, operations, validate_only=False)
        self.audit.write(cid, action, target, before, after, "BERHASIL", "; ".join(notes or []) or None)
        return {"status": "DIJALANKAN", **base, "resource": resources}

    # ---------- negative keywords ----------

    def _campaign_negatives(self, cid: str, campaign_id: str) -> list[dict[str, Any]]:
        q = (
            "SELECT campaign_criterion.criterion_id, campaign_criterion.keyword.text, "
            "campaign_criterion.keyword.match_type FROM campaign_criterion "
            f"WHERE campaign.id = {campaign_id} AND campaign_criterion.negative = TRUE "
            "AND campaign_criterion.type = 'KEYWORD'"
        )
        return self.ads.search(cid, q)

    def add_negative_keywords(self, cid: str, campaign_id: str, keywords: list[dict], confirm: bool) -> dict:
        campaign_id = _digits(campaign_id, "campaign_id")
        wanted = normalize_keywords(keywords)
        existing = {
            (r.get("campaign_criterion.keyword.text", "").lower(), r.get("campaign_criterion.keyword.match_type"))
            for r in self._campaign_negatives(cid, campaign_id)
        }
        new = [k for k in wanted if (k["text"], k["match_type"]) not in existing]
        skipped = [k for k in wanted if (k["text"], k["match_type"]) in existing]
        if not new:
            return {"status": "TIDAK_ADA_PERUBAHAN", "pesan": "Semua kata kunci negatif sudah terpasang.", "dilewati": skipped}
        campaign_path = self.gc.get_service("CampaignService").campaign_path(cid, campaign_id)
        ops = []
        for k in new:
            op = self.gc.get_type("CampaignCriterionOperation")
            crit = op.create
            crit.campaign = campaign_path
            crit.negative = True
            crit.keyword.text = k["text"]
            crit.keyword.match_type = getattr(self.gc.enums.KeywordMatchTypeEnum, k["match_type"])
            ops.append(op)
        notes = [f"{len(skipped)} kata kunci dilewati karena sudah terpasang."] if skipped else None
        return self._finish(
            cid=cid, action="add_negative_keywords", target={"campaign_id": campaign_id},
            before={"jumlah_negatif": len(existing)}, after={"ditambahkan": new},
            service="CampaignCriterionService", method="mutate_campaign_criteria",
            operations=ops, confirm=confirm, notes=notes,
        )

    def remove_negative_keywords(self, cid: str, campaign_id: str, texts: list[str], confirm: bool) -> dict:
        campaign_id = _digits(campaign_id, "campaign_id")
        wanted = {" ".join(t.lower().split()) for t in texts}
        rows = [r for r in self._campaign_negatives(cid, campaign_id)
                if r.get("campaign_criterion.keyword.text", "").lower() in wanted]
        if not rows:
            return {"status": "TIDAK_ADA_PERUBAHAN", "pesan": "Tidak ada kata kunci negatif yang cocok."}
        svc = self.gc.get_service("CampaignCriterionService")
        ops = []
        for r in rows:
            op = self.gc.get_type("CampaignCriterionOperation")
            op.remove = svc.campaign_criterion_path(cid, campaign_id, r["campaign_criterion.criterion_id"])
            ops.append(op)
        removed = [
            {"text": r["campaign_criterion.keyword.text"], "match_type": r.get("campaign_criterion.keyword.match_type")}
            for r in rows
        ]
        return self._finish(
            cid=cid, action="remove_negative_keywords", target={"campaign_id": campaign_id},
            before={"dihapus": removed}, after=None,
            service="CampaignCriterionService", method="mutate_campaign_criteria",
            operations=ops, confirm=confirm,
        )

    # ---------- budget ----------

    def update_budget(self, cid: str, campaign_id: str, daily_budget: float, confirm: bool,
                      allow_shared: bool = False) -> dict:
        campaign_id = _digits(campaign_id, "campaign_id")
        rows = self.ads.search(cid, (
            "SELECT campaign.name, campaign_budget.resource_name, campaign_budget.amount_micros, "
            "campaign_budget.explicitly_shared, campaign_budget.reference_count "
            f"FROM campaign WHERE campaign.id = {campaign_id}"
        ))
        if not rows:
            raise ValueError(f"Kampanye {campaign_id} tidak ditemukan.")
        row = rows[0]
        current = row.get("campaign_budget.amount")
        notes = check_budget(self.safety, current, daily_budget)
        if row.get("campaign_budget.explicitly_shared") and int(row.get("campaign_budget.reference_count") or 0) > 1:
            if not allow_shared:
                raise SafetyError(
                    "Budget ini dipakai bersama beberapa kampanye (shared budget). Mengubahnya memengaruhi "
                    "semua kampanye tersebut. Ulangi dengan allow_shared=true bila memang disengaja."
                )
            notes.append("Budget bersama: perubahan berlaku untuk semua kampanye yang memakainya.")
        budget_resource = self._budget_resource(cid, campaign_id)
        op = self.gc.get_type("CampaignBudgetOperation")
        op.update.resource_name = budget_resource
        op.update.amount_micros = to_micros(daily_budget)
        op.update_mask.paths.append("amount_micros")
        return self._finish(
            cid=cid, action="update_budget",
            target={"campaign_id": campaign_id, "kampanye": row.get("campaign.name")},
            before={"budget_harian": current}, after={"budget_harian": daily_budget},
            service="CampaignBudgetService", method="mutate_campaign_budgets",
            operations=[op], confirm=confirm, notes=notes,
        )

    def _budget_resource(self, cid: str, campaign_id: str) -> str:
        # humanize() membuang resource_name, jadi ambil langsung dari API.
        svc = self.gc.get_service("GoogleAdsService")
        q = f"SELECT campaign.campaign_budget FROM campaign WHERE campaign.id = {campaign_id}"
        try:
            for batch in svc.search_stream(customer_id=cid, query=q):
                for r in batch.results:
                    return r.campaign.campaign_budget
        except GoogleAdsException as ex:
            raise AdsError(_describe(ex)) from ex
        raise ValueError(f"Budget untuk kampanye {campaign_id} tidak ditemukan.")

    # ---------- status ----------

    def set_status(self, cid: str, level: str, ids: list[str], status: str, confirm: bool) -> dict:
        level = level.lower()
        status = status.upper()
        if level not in LEVELS:
            raise ValueError(f"level harus salah satu dari {LEVELS}")
        if status not in STATUSES:
            raise ValueError(f"status harus salah satu dari {STATUSES} (menghapus permanen tidak didukung).")
        if not ids:
            raise ValueError("ids tidak boleh kosong.")
        ops = []
        if level == "campaign":
            svc = self.gc.get_service("CampaignService")
            ids = [_digits(i, "campaign_id") for i in ids]
            before = self.ads.search(cid, f"SELECT campaign.id, campaign.name, campaign.status FROM campaign "
                                          f"WHERE campaign.id IN ({','.join(ids)})")
            for i in ids:
                op = self.gc.get_type("CampaignOperation")
                op.update.resource_name = svc.campaign_path(cid, i)
                op.update.status = getattr(self.gc.enums.CampaignStatusEnum, status)
                op.update_mask.paths.append("status")
                ops.append(op)
            service, method = "CampaignService", "mutate_campaigns"
        elif level == "ad_group":
            svc = self.gc.get_service("AdGroupService")
            ids = [_digits(i, "ad_group_id") for i in ids]
            before = self.ads.search(cid, f"SELECT ad_group.id, ad_group.name, ad_group.status FROM ad_group "
                                          f"WHERE ad_group.id IN ({','.join(ids)})")
            for i in ids:
                op = self.gc.get_type("AdGroupOperation")
                op.update.resource_name = svc.ad_group_path(cid, i)
                op.update.status = getattr(self.gc.enums.AdGroupStatusEnum, status)
                op.update_mask.paths.append("status")
                ops.append(op)
            service, method = "AdGroupService", "mutate_ad_groups"
        else:
            svc = self.gc.get_service("AdGroupCriterionService")
            pairs = []
            for i in ids:
                if "~" not in str(i):
                    raise ValueError("ID kata kunci harus berformat 'ad_group_id~criterion_id'.")
                ag, crit = str(i).split("~", 1)
                pairs.append((_digits(ag, "ad_group_id"), _digits(crit, "criterion_id")))
            before = [{"ad_group_id": ag, "criterion_id": crit} for ag, crit in pairs]
            for ag, crit in pairs:
                op = self.gc.get_type("AdGroupCriterionOperation")
                op.update.resource_name = svc.ad_group_criterion_path(cid, ag, crit)
                op.update.status = getattr(self.gc.enums.AdGroupCriterionStatusEnum, status)
                op.update_mask.paths.append("status")
                ops.append(op)
            service, method = "AdGroupCriterionService", "mutate_ad_group_criteria"
        return self._finish(
            cid=cid, action=f"set_status_{level}", target={"level": level, "ids": ids},
            before=before, after={"status": status},
            service=service, method=method, operations=ops, confirm=confirm,
        )

    # ---------- conversion action ----------

    def update_conversion_action(
        self,
        cid: str,
        conversion_action_id: str,
        confirm: bool,
        primary_for_goal: bool | None = None,
        counting_type: str | None = None,
        click_through_days: int | None = None,
        view_through_days: int | None = None,
        status: str | None = None,
    ) -> dict:
        ca_id = _digits(conversion_action_id, "conversion_action_id")
        rows = self.ads.search(cid, (
            "SELECT conversion_action.id, conversion_action.name, conversion_action.type, "
            "conversion_action.primary_for_goal, conversion_action.counting_type, "
            "conversion_action.click_through_lookback_window_days, "
            "conversion_action.view_through_lookback_window_days, conversion_action.status "
            f"FROM conversion_action WHERE conversion_action.id = {ca_id}"
        ))
        if not rows:
            raise ValueError(f"Conversion action {ca_id} tidak ditemukan.")
        before = rows[0]
        op = self.gc.get_type("ConversionActionOperation")
        ca = op.update
        ca.resource_name = self.gc.get_service("ConversionActionService").conversion_action_path(cid, ca_id)
        after: dict[str, Any] = {}
        if primary_for_goal is not None:
            ca.primary_for_goal = primary_for_goal
            op.update_mask.paths.append("primary_for_goal")
            after["primary_for_goal"] = primary_for_goal
        if counting_type is not None:
            ct = counting_type.upper()
            if ct not in COUNTING_TYPES:
                raise ValueError(f"counting_type harus salah satu dari {COUNTING_TYPES}")
            ca.counting_type = getattr(self.gc.enums.ConversionActionCountingTypeEnum, ct)
            op.update_mask.paths.append("counting_type")
            after["counting_type"] = ct
        if click_through_days is not None:
            if not 1 <= click_through_days <= 90:
                raise ValueError("click_through_days harus 1-90.")
            ca.click_through_lookback_window_days = click_through_days
            op.update_mask.paths.append("click_through_lookback_window_days")
            after["click_through_days"] = click_through_days
        if view_through_days is not None:
            if not 1 <= view_through_days <= 30:
                raise ValueError("view_through_days harus 1-30.")
            ca.view_through_lookback_window_days = view_through_days
            op.update_mask.paths.append("view_through_lookback_window_days")
            after["view_through_days"] = view_through_days
        if status is not None:
            st = status.upper()
            if st not in ("ENABLED", "HIDDEN"):
                raise ValueError("status conversion action hanya ENABLED atau HIDDEN.")
            ca.status = getattr(self.gc.enums.ConversionActionStatusEnum, st)
            op.update_mask.paths.append("status")
            after["status"] = st
        if not after:
            raise ValueError("Tidak ada field yang diubah.")
        return self._finish(
            cid=cid, action="update_conversion_action",
            target={"conversion_action_id": ca_id, "nama": before.get("conversion_action.name")},
            before=before, after=after,
            service="ConversionActionService", method="mutate_conversion_actions",
            operations=[op], confirm=confirm,
            notes=["Konversi buatan Google (mis. 'Clicks to call', 'Local actions') biasanya hanya bisa "
                   "diubah dari UI Google Ads."] if "GOOGLE_HOSTED" in str(before.get("conversion_action.type")) else None,
        )

    # ---------- bidding ----------

    def update_bid_strategy(self, cid: str, campaign_id: str, strategy: str, confirm: bool,
                            target_cpa: float | None = None, max_cpc: float | None = None) -> dict:
        campaign_id = _digits(campaign_id, "campaign_id")
        strategy = strategy.upper()
        if strategy not in BID_STRATEGIES:
            raise ValueError(f"strategy harus salah satu dari {BID_STRATEGIES}")
        rows = self.ads.search(cid, "SELECT campaign.name, campaign.bidding_strategy_type FROM campaign "
                                    f"WHERE campaign.id = {campaign_id}")
        if not rows:
            raise ValueError(f"Kampanye {campaign_id} tidak ditemukan.")
        op = self.gc.get_type("CampaignOperation")
        camp = op.update
        camp.resource_name = self.gc.get_service("CampaignService").campaign_path(cid, campaign_id)
        after: dict[str, Any] = {"strategi": strategy}
        if strategy == "MAXIMIZE_CONVERSIONS":
            camp.maximize_conversions.target_cpa_micros = to_micros(target_cpa) if target_cpa else 0
            op.update_mask.paths.append("maximize_conversions.target_cpa_micros")
            after["target_cpa"] = target_cpa
        elif strategy == "MAXIMIZE_CLICKS":
            camp.target_spend.cpc_bid_ceiling_micros = to_micros(max_cpc) if max_cpc else 0
            op.update_mask.paths.append("target_spend.cpc_bid_ceiling_micros")
            after["max_cpc"] = max_cpc
        else:
            camp.manual_cpc.enhanced_cpc_enabled = False
            op.update_mask.paths.append("manual_cpc.enhanced_cpc_enabled")
        notes = ["Mengganti strategi bidding memicu fase belajar ulang 1-2 minggu."]
        return self._finish(
            cid=cid, action="update_bid_strategy",
            target={"campaign_id": campaign_id, "kampanye": rows[0].get("campaign.name")},
            before={"strategi": rows[0].get("campaign.bidding_strategy_type")}, after=after,
            service="CampaignService", method="mutate_campaigns", operations=[op],
            confirm=confirm, notes=notes,
        )

    # ---------- keywords ----------

    def add_keywords(self, cid: str, ad_group_id: str, keywords: list[dict], confirm: bool,
                     cpc_bid: float | None = None, paused: bool = False) -> dict:
        ad_group_id = _digits(ad_group_id, "ad_group_id")
        wanted = normalize_keywords(keywords)
        ag_path = self.gc.get_service("AdGroupService").ad_group_path(cid, ad_group_id)
        status = self.gc.enums.AdGroupCriterionStatusEnum.PAUSED if paused else self.gc.enums.AdGroupCriterionStatusEnum.ENABLED
        ops = []
        for k in wanted:
            op = self.gc.get_type("AdGroupCriterionOperation")
            crit = op.create
            crit.ad_group = ag_path
            crit.status = status
            crit.keyword.text = k["text"]
            crit.keyword.match_type = getattr(self.gc.enums.KeywordMatchTypeEnum, k["match_type"])
            if cpc_bid:
                crit.cpc_bid_micros = to_micros(cpc_bid)
            ops.append(op)
        return self._finish(
            cid=cid, action="add_keywords", target={"ad_group_id": ad_group_id},
            before=None, after={"kata_kunci": wanted, "cpc_bid": cpc_bid, "dijeda": paused},
            service="AdGroupCriterionService", method="mutate_ad_group_criteria",
            operations=ops, confirm=confirm,
        )
