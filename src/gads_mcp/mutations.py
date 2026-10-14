"""Operasi yang mengubah akun Google Ads (Fase 2).

Alur setiap operasi:
  1. Ambil kondisi saat ini (untuk log "sebelum" dan pengecekan pengaman).
  2. Cek aturan pengaman AdsAi (mode baca-saja, batas budget, dll.).
  3. Kirim ke Google dengan validate_only=True  -> pratinjau (tidak ada yang berubah).
  4. Hanya bila confirm=True: kirim sungguhan, tulis log audit, lalu baca ulang kondisi
     (verifikasi) agar yang dilaporkan adalah yang benar-benar terjadi di akun.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from google.ads.googleads.errors import GoogleAdsException

from .client import Ads, AdsError, _describe
from . import rsa
from .safety import AuditLog, SafetyError, check_budget, ensure_writable, to_micros

MATCH_TYPES = ("EXACT", "PHRASE", "BROAD")
STATUSES = ("ENABLED", "PAUSED")
LEVELS = ("campaign", "ad_group", "keyword", "ad")
BID_STRATEGIES = ("MAXIMIZE_CONVERSIONS", "MAXIMIZE_CLICKS", "MANUAL_CPC")
COUNTING_TYPES = ("ONE_PER_CLICK", "MANY_PER_CLICK")
MIN_CONVERSIONS_FOR_TARGET = 30  # konversi 30 hari minimum sebelum memasang Target CPA


def _response_resource(r: Any) -> str:
    """resource_name dari MutateOperationResponse (oneof: asset_result, campaign_asset_result, ...)."""
    direct = getattr(r, "resource_name", None)
    if direct:
        return direct
    field_name = type(r).pb(r).WhichOneof("response")
    return getattr(r, field_name).resource_name if field_name else ""


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
        """Kirim operasi. method='mutate' (GoogleAdsService) = beberapa jenis operasi dalam satu
        request atomik: gagal satu, batal semua."""
        svc = self.gc.get_service(service)
        key = "mutate_operations" if method == "mutate" else "operations"
        request = {"customer_id": cid, key: operations, "validate_only": validate_only}
        try:
            response = getattr(svc, method)(request=request)
        except GoogleAdsException as ex:
            raise AdsError(_describe(ex)) from ex
        if validate_only:
            return []
        if method == "mutate":
            return [_response_resource(r) for r in response.mutate_operation_responses]
        return [r.resource_name for r in response.results]

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
        verify: Callable[[], Any] | None = None,
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
        result = {"status": "DIJALANKAN", **base, "resource": resources}
        if verify:
            try:
                result["terverifikasi"] = verify()
            except Exception as ex:  # perubahan sudah terjadi; verifikasi gagal tidak membatalkannya
                result["terverifikasi"] = f"Gagal membaca ulang: {ex}"
        return result

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
            verify=lambda: {"jumlah_negatif": len(self._campaign_negatives(cid, campaign_id))},
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
            verify=lambda: {"jumlah_negatif": len(self._campaign_negatives(cid, campaign_id))},
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
            verify=lambda: {"budget_harian": (self.ads.search(cid, (
                f"SELECT campaign_budget.amount_micros FROM campaign WHERE campaign.id = {campaign_id}"
            )) or [{}])[0].get("campaign_budget.amount")},
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
        elif level == "ad":
            svc = self.gc.get_service("AdGroupAdService")
            pairs = []
            for i in ids:
                if "~" not in str(i):
                    raise ValueError("ID iklan harus berformat 'ad_group_id~ad_id'.")
                ag, ad = str(i).split("~", 1)
                pairs.append((_digits(ag, "ad_group_id"), _digits(ad, "ad_id")))
            before = [{"ad_group_id": ag, "ad_id": ad} for ag, ad in pairs]
            for ag, ad in pairs:
                op = self.gc.get_type("AdGroupAdOperation")
                op.update.resource_name = svc.ad_group_ad_path(cid, ag, ad)
                op.update.status = getattr(self.gc.enums.AdGroupAdStatusEnum, status)
                op.update_mask.paths.append("status")
                ops.append(op)
            service, method = "AdGroupAdService", "mutate_ad_group_ads"
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
        verify = None
        if level == "campaign":
            verify = lambda: self.ads.search(cid, "SELECT campaign.id, campaign.status FROM campaign "  # noqa: E731
                                                  f"WHERE campaign.id IN ({','.join(ids)})")
        elif level == "ad_group":
            verify = lambda: self.ads.search(cid, "SELECT ad_group.id, ad_group.status FROM ad_group "  # noqa: E731
                                                  f"WHERE ad_group.id IN ({','.join(ids)})")
        return self._finish(
            cid=cid, action=f"set_status_{level}", target={"level": level, "ids": ids},
            before=before, after={"status": status},
            service=service, method=method, operations=ops, confirm=confirm, verify=verify,
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
        q_ca = (
            "SELECT conversion_action.id, conversion_action.name, conversion_action.type, "
            "conversion_action.primary_for_goal, conversion_action.counting_type, "
            "conversion_action.click_through_lookback_window_days, "
            "conversion_action.view_through_lookback_window_days, conversion_action.status "
            f"FROM conversion_action WHERE conversion_action.id = {ca_id}"
        )
        rows = self.ads.search(cid, q_ca)
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
        ca_type = str(before.get("conversion_action.type"))
        notes = None
        if "GOOGLE_HOSTED" in ca_type or "WEBPAGE_CODELESS" in ca_type:
            notes = ["Konversi buatan Google (mis. 'Clicks to call', 'Local actions', 'Add to basket (page load)') "
                     "biasanya ditolak API ('Mutates are not allowed') dan hanya bisa diubah dari UI Google Ads."]
        elif "AD_CALL" in ca_type:
            notes = ["Untuk 'Calls from ads', sebagian field (mis. view-through, cara hitung) hanya bisa diubah di UI."]
        return self._finish(
            cid=cid, action="update_conversion_action",
            target={"conversion_action_id": ca_id, "nama": before.get("conversion_action.name")},
            before=before, after=after,
            service="ConversionActionService", method="mutate_conversion_actions",
            operations=[op], confirm=confirm, notes=notes,
            verify=lambda: (self.ads.search(cid, q_ca) or [{}])[0],
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
        notes = ["Mengganti strategi bidding memicu fase belajar ulang 1-2 minggu."]
        if strategy == "MAXIMIZE_CONVERSIONS" and target_cpa:
            hist = self.ads.search(cid, "SELECT metrics.conversions FROM campaign "
                                        f"WHERE campaign.id = {campaign_id} AND segments.date DURING LAST_30_DAYS")
            conv30 = sum(float(r.get("metrics.conversions") or 0) for r in hist)
            if conv30 < MIN_CONVERSIONS_FOR_TARGET:
                notes.append(
                    f"Hanya {conv30:g} konversi dalam 30 hari (disarankan >= {MIN_CONVERSIONS_FOR_TARGET}). "
                    "Target CPA tanpa histori cukup biasanya membuat iklan kurang tayang; pertimbangkan "
                    "Maksimalkan Konversi tanpa target dulu."
                )
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
        return self._finish(
            cid=cid, action="update_bid_strategy",
            target={"campaign_id": campaign_id, "kampanye": rows[0].get("campaign.name")},
            before={"strategi": rows[0].get("campaign.bidding_strategy_type")}, after=after,
            service="CampaignService", method="mutate_campaigns", operations=[op],
            confirm=confirm, notes=notes,
            verify=lambda: (self.ads.search(cid, "SELECT campaign.bidding_strategy_type FROM campaign "
                                                 f"WHERE campaign.id = {campaign_id}") or [{}])[0],
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

    # ---------- shared negative keyword list ----------

    def list_items(self, cid: str, shared_set_id: str) -> list[dict[str, Any]]:
        sid = _digits(shared_set_id, "shared_set_id")
        return self.ads.search(cid, (
            "SELECT shared_criterion.criterion_id, shared_criterion.keyword.text, "
            "shared_criterion.keyword.match_type FROM shared_criterion "
            f"WHERE shared_set.id = {sid} AND shared_criterion.type = 'KEYWORD'"
        ))

    def _list_info(self, cid: str, sid: str) -> dict[str, Any]:
        rows = self.ads.search(cid, "SELECT shared_set.id, shared_set.name, shared_set.type, shared_set.member_count, "
                                    f"shared_set.reference_count FROM shared_set WHERE shared_set.id = {sid}")
        if not rows:
            raise ValueError(f"Negative keyword list {sid} tidak ditemukan.")
        if rows[0].get("shared_set.type") not in (None, "NEGATIVE_KEYWORDS"):
            raise ValueError(f"Shared set {sid} bukan negative keyword list.")
        return rows[0]

    def create_negative_list(self, cid: str, name: str, confirm: bool) -> dict:
        name = " ".join(str(name).split())
        if not name:
            raise ValueError("Nama list tidak boleh kosong.")
        existing = self.ads.search(cid, "SELECT shared_set.id, shared_set.name FROM shared_set "
                                        "WHERE shared_set.type = 'NEGATIVE_KEYWORDS' AND shared_set.status = 'ENABLED'")
        if any(r.get("shared_set.name", "").lower() == name.lower() for r in existing):
            raise ValueError(f"List bernama '{name}' sudah ada.")
        op = self.gc.get_type("SharedSetOperation")
        op.create.name = name
        op.create.type_ = self.gc.enums.SharedSetTypeEnum.NEGATIVE_KEYWORDS
        result = self._finish(
            cid=cid, action="create_negative_list", target={"nama": name}, before=None, after={"nama": name},
            service="SharedSetService", method="mutate_shared_sets", operations=[op], confirm=confirm,
        )
        if result["status"] == "DIJALANKAN" and result.get("resource"):
            result["shared_set_id"] = result["resource"][0].split("/")[-1]
        return result

    def add_to_negative_list(self, cid: str, shared_set_id: str, keywords: list[dict], confirm: bool) -> dict:
        sid = _digits(shared_set_id, "shared_set_id")
        info = self._list_info(cid, sid)
        wanted = normalize_keywords(keywords)
        existing = {(r.get("shared_criterion.keyword.text", "").lower(), r.get("shared_criterion.keyword.match_type"))
                    for r in self.list_items(cid, sid)}
        new = [k for k in wanted if (k["text"], k["match_type"]) not in existing]
        skipped = [k for k in wanted if (k["text"], k["match_type"]) in existing]
        if not new:
            return {"status": "TIDAK_ADA_PERUBAHAN", "pesan": "Semua kata kunci sudah ada di list.", "dilewati": skipped}
        path = self.gc.get_service("SharedSetService").shared_set_path(cid, sid)
        ops = []
        for k in new:
            op = self.gc.get_type("SharedCriterionOperation")
            op.create.shared_set = path
            op.create.keyword.text = k["text"]
            op.create.keyword.match_type = getattr(self.gc.enums.KeywordMatchTypeEnum, k["match_type"])
            ops.append(op)
        notes = [f"List dipakai {info.get('shared_set.reference_count') or 0} kampanye — semuanya ikut terpengaruh."]
        if skipped:
            notes.append(f"{len(skipped)} kata kunci dilewati karena sudah ada.")
        return self._finish(
            cid=cid, action="add_to_negative_list", target={"shared_set_id": sid, "nama": info.get("shared_set.name")},
            before={"jumlah": len(existing)}, after={"ditambahkan": new},
            service="SharedCriterionService", method="mutate_shared_criteria", operations=ops,
            confirm=confirm, notes=notes, verify=lambda: {"jumlah": len(self.list_items(cid, sid))},
        )

    def remove_from_negative_list(self, cid: str, shared_set_id: str, texts: list[str], confirm: bool) -> dict:
        sid = _digits(shared_set_id, "shared_set_id")
        info = self._list_info(cid, sid)
        wanted = {" ".join(t.lower().split()) for t in texts}
        rows = [r for r in self.list_items(cid, sid) if r.get("shared_criterion.keyword.text", "").lower() in wanted]
        if not rows:
            return {"status": "TIDAK_ADA_PERUBAHAN", "pesan": "Tidak ada kata kunci yang cocok di list."}
        svc = self.gc.get_service("SharedCriterionService")
        ops = []
        for r in rows:
            op = self.gc.get_type("SharedCriterionOperation")
            op.remove = svc.shared_criterion_path(cid, sid, r["shared_criterion.criterion_id"])
            ops.append(op)
        return self._finish(
            cid=cid, action="remove_from_negative_list", target={"shared_set_id": sid, "nama": info.get("shared_set.name")},
            before={"dihapus": [r.get("shared_criterion.keyword.text") for r in rows]}, after=None,
            service="SharedCriterionService", method="mutate_shared_criteria", operations=ops, confirm=confirm,
            verify=lambda: {"jumlah": len(self.list_items(cid, sid))},
        )

    def _attached(self, cid: str, sid: str) -> list[str]:
        rows = self.ads.search(cid, "SELECT campaign.id FROM campaign_shared_set "
                                    f"WHERE shared_set.id = {sid} AND campaign_shared_set.status = 'ENABLED'")
        return [r.get("campaign.id") for r in rows]

    def attach_negative_list(self, cid: str, shared_set_id: str, campaign_ids: list[str], confirm: bool) -> dict:
        sid = _digits(shared_set_id, "shared_set_id")
        info = self._list_info(cid, sid)
        ids = [_digits(i, "campaign_id") for i in campaign_ids]
        if not ids:
            raise ValueError("campaign_ids tidak boleh kosong.")
        attached = set(self._attached(cid, sid))
        new = [i for i in ids if i not in attached]
        if not new:
            return {"status": "TIDAK_ADA_PERUBAHAN", "pesan": "List sudah terpasang di semua kampanye tersebut."}
        set_path = self.gc.get_service("SharedSetService").shared_set_path(cid, sid)
        camp_svc = self.gc.get_service("CampaignService")
        ops = []
        for i in new:
            op = self.gc.get_type("CampaignSharedSetOperation")
            op.create.campaign = camp_svc.campaign_path(cid, i)
            op.create.shared_set = set_path
            ops.append(op)
        return self._finish(
            cid=cid, action="attach_negative_list", target={"shared_set_id": sid, "nama": info.get("shared_set.name")},
            before={"kampanye": sorted(attached)}, after={"ditambahkan": new},
            service="CampaignSharedSetService", method="mutate_campaign_shared_sets", operations=ops,
            confirm=confirm, notes=[f"{info.get('shared_set.member_count') or 0} kata kunci negatif akan berlaku di "
                                    f"{len(new)} kampanye."],
            verify=lambda: {"kampanye": sorted(self._attached(cid, sid))},
        )

    def detach_negative_list(self, cid: str, shared_set_id: str, campaign_ids: list[str], confirm: bool) -> dict:
        sid = _digits(shared_set_id, "shared_set_id")
        info = self._list_info(cid, sid)
        ids = [_digits(i, "campaign_id") for i in campaign_ids]
        attached = set(self._attached(cid, sid))
        targets = [i for i in ids if i in attached]
        if not targets:
            return {"status": "TIDAK_ADA_PERUBAHAN", "pesan": "List tidak terpasang di kampanye tersebut."}
        svc = self.gc.get_service("CampaignSharedSetService")
        ops = []
        for i in targets:
            op = self.gc.get_type("CampaignSharedSetOperation")
            op.remove = svc.campaign_shared_set_path(cid, i, sid)
            ops.append(op)
        return self._finish(
            cid=cid, action="detach_negative_list", target={"shared_set_id": sid, "nama": info.get("shared_set.name")},
            before={"kampanye": sorted(attached)}, after={"dilepas": targets},
            service="CampaignSharedSetService", method="mutate_campaign_shared_sets", operations=ops,
            confirm=confirm, verify=lambda: {"kampanye": sorted(self._attached(cid, sid))},
        )

    # ---------- Responsive Search Ads ----------

    def _fill_assets(self, repeated, items: list[dict[str, Any]], pin_map: dict[int, str]) -> None:
        for it in items:
            asset = self.gc.get_type("AdTextAsset")
            asset.text = it["text"]
            if it.get("pin"):
                asset.pinned_field = getattr(self.gc.enums.ServedAssetFieldTypeEnum, pin_map[it["pin"]])
            repeated.append(asset)

    def _ad_row(self, cid: str, ad_group_id: str, ad_id: str) -> dict[str, Any]:
        rows = self.ads.search(cid, (
            "SELECT ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status, ad_group_ad.ad_strength, "
            "ad_group_ad.policy_summary.approval_status, ad_group_ad.ad.final_urls, "
            "ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
            "ad_group_ad.ad.responsive_search_ad.path1, ad_group_ad.ad.responsive_search_ad.path2 "
            f"FROM ad_group_ad WHERE ad_group.id = {ad_group_id} AND ad_group_ad.ad.id = {ad_id}"
        ))
        if not rows:
            raise ValueError(f"Iklan {ad_id} di grup iklan {ad_group_id} tidak ditemukan.")
        return rows[0]

    def create_rsa(self, cid: str, ad_group_id: str, headlines: list, descriptions: list, final_urls: list[str],
                   confirm: bool, path1: str | None = None, path2: str | None = None, paused: bool = True) -> dict:
        ag = _digits(ad_group_id, "ad_group_id")
        copy = rsa.validate(headlines, descriptions, path1, path2)
        errors = copy.errors + rsa.validate_urls(final_urls)
        if not final_urls:
            errors.append("final_urls wajib diisi (minimal satu URL landing page).")
        if errors:
            raise ValueError("Teks iklan belum valid:\n- " + "\n- ".join(errors))
        op = self.gc.get_type("AdGroupAdOperation")
        aga = op.create
        aga.ad_group = self.gc.get_service("AdGroupService").ad_group_path(cid, ag)
        aga.status = self.gc.enums.AdGroupAdStatusEnum.PAUSED if paused else self.gc.enums.AdGroupAdStatusEnum.ENABLED
        aga.ad.final_urls.extend(final_urls)
        r = aga.ad.responsive_search_ad
        self._fill_assets(r.headlines, copy.headlines, rsa.HEADLINE_PINS)
        self._fill_assets(r.descriptions, copy.descriptions, rsa.DESC_PINS)
        if copy.path1:
            r.path1 = copy.path1
        if copy.path2:
            r.path2 = copy.path2
        notes = list(copy.warnings) + ["Iklan baru ditinjau Google (biasanya < 1 hari, bisa sampai 48 jam)."]
        if paused:
            notes.append("Iklan dibuat DIJEDA. Aktifkan dengan set_status level='ad' setelah dicek.")
        return self._finish(
            cid=cid, action="create_rsa", target={"ad_group_id": ag}, before=None,
            after={"headlines": copy.headlines, "descriptions": copy.descriptions, "final_urls": final_urls,
                   "path1": copy.path1, "path2": copy.path2, "dijeda": paused},
            service="AdGroupAdService", method="mutate_ad_group_ads", operations=[op], confirm=confirm, notes=notes,
        )

    def update_rsa(self, cid: str, ad_group_id: str, ad_id: str, confirm: bool,
                   headlines: list | None = None, descriptions: list | None = None,
                   final_urls: list[str] | None = None, path1: str | None = None, path2: str | None = None) -> dict:
        ag, aid = _digits(ad_group_id, "ad_group_id"), _digits(ad_id, "ad_id")
        before = self._ad_row(cid, ag, aid)
        if before.get("ad_group_ad.ad.type") not in (None, "RESPONSIVE_SEARCH_AD"):
            raise ValueError(f"Iklan {aid} bukan Responsive Search Ad ({before.get('ad_group_ad.ad.type')}).")
        if headlines is None and descriptions is None and final_urls is None and path1 is None and path2 is None:
            raise ValueError("Tidak ada yang diubah.")
        copy = rsa.validate(headlines, descriptions, path1, path2, partial=True)
        errors = copy.errors + rsa.validate_urls(final_urls)
        if errors:
            raise ValueError("Teks iklan belum valid:\n- " + "\n- ".join(errors))
        op = self.gc.get_type("AdOperation")
        ad = op.update
        ad.resource_name = self.gc.get_service("AdService").ad_path(cid, aid)
        after: dict[str, Any] = {}
        if headlines is not None:
            self._fill_assets(ad.responsive_search_ad.headlines, copy.headlines, rsa.HEADLINE_PINS)
            op.update_mask.paths.append("responsive_search_ad.headlines")
            after["headlines"] = copy.headlines
        if descriptions is not None:
            self._fill_assets(ad.responsive_search_ad.descriptions, copy.descriptions, rsa.DESC_PINS)
            op.update_mask.paths.append("responsive_search_ad.descriptions")
            after["descriptions"] = copy.descriptions
        if path1 is not None:
            ad.responsive_search_ad.path1 = copy.path1 or ""
            op.update_mask.paths.append("responsive_search_ad.path1")
            after["path1"] = copy.path1
        if path2 is not None:
            ad.responsive_search_ad.path2 = copy.path2 or ""
            op.update_mask.paths.append("responsive_search_ad.path2")
            after["path2"] = copy.path2
        if final_urls is not None:
            ad.final_urls.extend(final_urls)
            op.update_mask.paths.append("final_urls")
            after["final_urls"] = final_urls
        notes = list(copy.warnings) + ["Iklan yang diubah ditinjau ulang Google (biasanya < 1 hari, bisa sampai 48 jam)."]
        return self._finish(
            cid=cid, action="update_rsa", target={"ad_group_id": ag, "ad_id": aid},
            before={"headlines": before.get("ad_group_ad.ad.responsive_search_ad.headlines"),
                    "descriptions": before.get("ad_group_ad.ad.responsive_search_ad.descriptions"),
                    "final_urls": before.get("ad_group_ad.ad.final_urls"),
                    "ad_strength": before.get("ad_group_ad.ad_strength")},
            after=after, service="AdService", method="mutate_ads", operations=[op], confirm=confirm, notes=notes,
            verify=lambda: {k.split(".")[-1]: v for k, v in self._ad_row(cid, ag, aid).items()
                            if k.endswith(("headlines", "descriptions", "final_urls", "approval_status", "ad_strength"))},
        )
