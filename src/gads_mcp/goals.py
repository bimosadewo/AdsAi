"""Sasaran konversi tingkat kampanye: baca, nilai, dan kembalikan ke sasaran default akun.

Latar: kampanye bisa memakai sasaran default akun (CUSTOMER) atau sasaran khusus kampanye (CAMPAIGN). Bila
sasaran yang dipakai kampanye tidak berisi satu pun konversi Utama yang aktif, Google menandai kampanye
"Terbatas: sasaran yang Anda targetkan tidak memiliki tindakan konversi utama" — Smart Bidding lalu
membelanjakan budget tanpa sinyal. API v25 melaporkan alasan ini sebagai primary_status_reasons UNKNOWN,
jadi AdsAi menghitungnya sendiri dari sasaran + daftar konversi. Adspirer tidak punya tool untuk ini.
"""

from __future__ import annotations

from typing import Any, Iterable

from .mutations import Mutator, _digits


def _campaign_resource(cid: str, camp: str) -> str:
    return f"customers/{cid}/campaigns/{camp}"


def queries(cid: str, camp: str) -> dict[str, str]:
    res = _campaign_resource(cid, camp)
    return {
        "kampanye": f"SELECT campaign.id, campaign.name, campaign.bidding_strategy_type FROM campaign WHERE campaign.id = {camp}",
        "level": ("SELECT conversion_goal_campaign_config.goal_config_level FROM conversion_goal_campaign_config "
                  f"WHERE conversion_goal_campaign_config.campaign = '{res}'"),
        "sasaran": ("SELECT campaign_conversion_goal.category, campaign_conversion_goal.origin, "
                    "campaign_conversion_goal.biddable FROM campaign_conversion_goal "
                    f"WHERE campaign_conversion_goal.campaign = '{res}'"),
        "sasaran_akun": ("SELECT customer_conversion_goal.category, customer_conversion_goal.origin, "
                         "customer_conversion_goal.biddable FROM customer_conversion_goal"),
        "konversi": ("SELECT conversion_action.id, conversion_action.name, conversion_action.category, "
                     "conversion_action.origin, conversion_action.primary_for_goal FROM conversion_action "
                     "WHERE conversion_action.status = 'ENABLED'"),
    }


def _goals(rows: Iterable[dict[str, Any]], prefix: str) -> list[tuple[str, str]]:
    return sorted({(r.get(f"{prefix}.category"), r.get(f"{prefix}.origin")) for r in rows if r.get(f"{prefix}.biddable")})


def _primary_by_goal(actions: Iterable[dict[str, Any]]) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for a in actions:
        if a.get("conversion_action.primary_for_goal"):
            key = (a.get("conversion_action.category"), a.get("conversion_action.origin"))
            out.setdefault(key, []).append(a.get("conversion_action.name"))
    return out


def _describe(goals: list[tuple[str, str]], primary: dict) -> list[dict[str, Any]]:
    return [{"kategori": cat, "sumber": origin, "konversi_utama": primary.get((cat, origin), [])}
            for cat, origin in goals]


def assess(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Murni: nilai hasil queries(). Status OK / TANPA_KONVERSI_UTAMA / TIDAK_LENGKAP."""
    camp = (data.get("kampanye") or [{}])[0]
    level = (data.get("level") or [{}])[0].get("conversion_goal_campaign_config.goal_config_level") or "TIDAK_TERBACA"
    primary = _primary_by_goal(data.get("konversi") or [])
    used = _describe(_goals(data.get("sasaran") or [], "campaign_conversion_goal"), primary)
    account = _describe(_goals(data.get("sasaran_akun") or [], "customer_conversion_goal"), primary)
    out: dict[str, Any] = {"kampanye": camp.get("campaign.name"), "id": camp.get("campaign.id"),
                           "bidding": camp.get("campaign.bidding_strategy_type"),
                           "sumber_sasaran": {"CUSTOMER": "default akun", "CAMPAIGN": "khusus kampanye"}.get(level, level),
                           "sasaran_dipakai": used, "sasaran_default_akun": account}
    if not used:
        out["status"] = "TIDAK_LENGKAP"
        out["pesan"] = "Sasaran kampanye tidak terbaca — jangan simpulkan apa pun; cek manual di Setelan → Sasaran."
        return out
    covered = [g for g in used if g["konversi_utama"]]
    empty = [f"{g['kategori']} ({g['sumber']})" for g in used if not g["konversi_utama"]]
    if not covered:
        out["status"] = "TANPA_KONVERSI_UTAMA"
        msg = ("Tidak ada satu pun konversi Utama aktif di sasaran yang dipakai kampanye ini — bidding tidak punya "
               "sinyal dan konversi tidak tercatat di kolom Konversi.")
        if level == "CAMPAIGN" and any(g["konversi_utama"] for g in account):
            msg += " Sasaran default akun SUDAH berisi konversi Utama → pakai use_account_conversion_goals."
        out["pesan"] = msg
    else:
        out["status"] = "OK"
        out["pesan"] = f"{len(covered)} sasaran berisi konversi Utama."
    if empty:
        out["sasaran_kosong"] = empty
    return out


class Goals:
    def __init__(self, mutator: Mutator):
        self.m = mutator

    def read(self, cid: str, campaign_id: str) -> dict[str, Any]:
        camp = _digits(campaign_id, "campaign_id")
        data = {k: self.m.ads.search(cid, q) for k, q in queries(cid, camp).items()}
        return assess(data)

    def use_account_goals(self, cid: str, campaign_id: str, confirm: bool) -> dict[str, Any]:
        camp = _digits(campaign_id, "campaign_id")
        before = self.read(cid, camp)
        if not before.get("kampanye"):
            raise ValueError(f"Kampanye {camp} tidak ditemukan. Ambil ID dari list_campaigns.")
        if before["sumber_sasaran"] == "default akun":
            return {**before, "status_sasaran": before["status"], "status": "TIDAK_BERUBAH",
                    "pesan_ubah": "Kampanye sudah memakai sasaran default akun."}
        if not any(g["konversi_utama"] for g in before["sasaran_default_akun"]):
            raise ValueError("Sasaran default akun juga tidak berisi konversi Utama aktif — memindahkan kampanye tidak "
                             "akan menolong. Jadikan konversi prospek (WA/form/telepon) Utama dulu "
                             "(update_conversion_action), lalu ulangi.")
        gc = self.m.gc
        svc = gc.get_service("ConversionGoalCampaignConfigService")
        op = gc.get_type("ConversionGoalCampaignConfigOperation")
        op.update.resource_name = svc.conversion_goal_campaign_config_path(cid, camp)
        op.update.goal_config_level = gc.enums.GoalConfigLevelEnum.CUSTOMER
        op.update_mask.paths.append("goal_config_level")
        notes = ["Kampanye akan mengoptimalkan ke sasaran default akun: "
                 + "; ".join(f"{g['kategori']} ({', '.join(g['konversi_utama']) or 'tanpa konversi Utama'})"
                             for g in before["sasaran_default_akun"]),
                 "Strategi bidding mungkin masuk fase belajar lagi ±1–2 minggu."]
        return self.m._finish(
            cid=cid, action="use_account_conversion_goals", target={"campaign_id": camp},
            before={"sumber_sasaran": before["sumber_sasaran"], "status": before["status"]},
            after={"sumber_sasaran": "default akun"},
            service="ConversionGoalCampaignConfigService", method="mutate_conversion_goal_campaign_configs",
            operations=[op], confirm=confirm, notes=notes,
            verify=lambda: {k: v for k, v in self.read(cid, camp).items() if k in ("sumber_sasaran", "status", "pesan")},
        )
