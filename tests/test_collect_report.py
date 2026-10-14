"""Uji alur ujung-ke-ujung: data palsu Google Ads -> snapshot -> alert -> laporan mingguan -> CLI."""

from datetime import date

from gads_mcp import cli
from gads_mcp.analysis import evaluate_alerts
from gads_mcp.clients import Client
from gads_mcp.collect import collect_snapshot, week_ranges
from gads_mcp.config import Safety, Settings
from gads_mcp.reports import change, number, rupiah, weekly_report
from gads_mcp.safety import AuditLog
from gads_mcp.service import Context

TODAY = date(2026, 10, 15)
CLIENT = Client("tigapilar", "PT Tiga Pilar", "5827572362", whatsapp=("6281200000000",),
                target_cpa=15_000, monthly_budget=3_000_000,
                protected_terms=("geotextile", "plastik cor", "woven", "nonwoven"))


class FakeSearch:
    def __init__(self):
        self.queries = []

    def search(self, cid, q):
        self.queries.append(q)
        if "FROM customer" in q:
            days = [f"2026-10-{d:02d}" for d in range(1, 15)] if "'2026-10-01'" in q else \
                   [f"2026-09-{d:02d}" for d in range(24, 31)] + [f"2026-10-{d:02d}" for d in range(1, 15)]
            return [{"segments.date": d, "metrics.cost": 100_000.0, "metrics.clicks": 15,
                     "metrics.impressions": 300, "metrics.conversions": 0.0 if d >= "2026-10-11" else 6.0}
                    for d in days]
        if "FROM campaign WHERE segments.date BETWEEN '2026-10-08'" in q:
            return [{"campaign.id": "1", "campaign.name": "Lead-Search", "metrics.cost": 500_000.0,
                     "metrics.clicks": 70, "metrics.impressions": 1000, "metrics.conversions": 18.0},
                    {"campaign.id": "2", "campaign.name": "Plastik Cor", "metrics.cost": 200_000.0,
                     "metrics.clicks": 40, "metrics.impressions": 900, "metrics.conversions": 12.0}]
        if "FROM campaign WHERE segments.date BETWEEN '2026-10-01'" in q:
            return [{"campaign.id": "1", "campaign.name": "Lead-Search", "metrics.cost": 450_000.0,
                     "metrics.clicks": 60, "metrics.impressions": 900, "metrics.conversions": 30.0}]
        if "campaign.status = 'ENABLED' AND segments.date" in q:
            return [{"campaign.id": "1", "metrics.impressions": 50}]
        if "serving_status" in q:
            return [{"campaign.id": "1", "campaign.name": "Lead-Search"},
                    {"campaign.id": "3", "campaign.name": "Geotextile Lama",
                     "campaign.primary_status_reasons": ["BUDGET_CONSTRAINED"]}]
        if "FROM conversion_action" in q:
            return [{"conversion_action.primary_for_goal": True}] * 5
        if "offline_conversion_upload_client_summary" in q:
            return [{"offline_conversion_upload_client_summary.client": "GOOGLE_ADS_API",
                     "offline_conversion_upload_client_summary.success_rate": 0.1}]
        if "FROM search_term_view" in q:
            return [{"search_term_view.search_term": "beda geotextile woven dan nonwoven", "metrics.cost": 30_000.0,
                     "metrics.conversions": 0, "metrics.clicks": 4},
                    {"search_term_view.search_term": "plastic product", "metrics.cost": 25_000.0,
                     "metrics.conversions": 0, "metrics.clicks": 3},
                    {"search_term_view.search_term": "jual plastik cor", "metrics.cost": 60_000.0,
                     "metrics.conversions": 9, "metrics.clicks": 12}]
        if "FROM campaign_criterion" in q:
            return [{"campaign_criterion.keyword.text": "pdf"}]
        return []


def test_week_ranges():
    assert week_ranges(TODAY) == (("2026-10-08", "2026-10-14"), ("2026-10-01", "2026-10-07"))


def test_snapshot_alerts_and_report(tmp_path):
    snap = collect_snapshot(FakeSearch(), CLIENT, today=TODAY)
    assert snap["minggu_ini"]["biaya"] == 700_000 and snap["minggu_ini"]["konversi"] == 30
    assert snap["kampanye_aktif_tidak_tayang"] == [{"id": "3", "nama": "Geotextile Lama", "alasan": "BUDGET_CONSTRAINED"}]
    assert snap["pacing"]["biaya_bulan_ini"] == 1_400_000
    assert snap["impor_offline_bermasalah"][0]["rate"] == 0.1

    alerts = evaluate_alerts(snap, CLIENT)
    codes = {a.kode for a in alerts}
    assert {"TIDAK_TAYANG", "KONVERSI_NOL", "CPA_NAIK", "KONVERSI_UTAMA_BERLEBIH", "IMPOR_GAGAL"} <= codes

    audit = AuditLog(tmp_path)
    audit.write(CLIENT.customer_id, "update_budget", {"kampanye": "Lead-Search"},
                {"budget_harian": 200_000}, {"budget_harian": 50_000}, "BERHASIL")
    text = weekly_report(snap, alerts, CLIENT, audit.read(CLIENT.customer_id))
    assert "*Laporan Mingguan Google Ads*" in text
    assert "8 Okt 2026 – 14 Okt 2026" in text
    assert "Rp700.000" in text
    assert "Mengubah budget — Lead-Search — Rp200.000 → Rp50.000/hari" in text
    assert '"beda geotextile woven dan nonwoven"' in text       # tampil sebagai pemborosan
    plan = text.split("Rencana minggu depan")[1]
    assert "Blokir 2 pencarian" in plan and '"plastic product"' in plan
    assert "potensi hemat ±Rp55.000" in plan                     # tanpa hitung ganda
    md = weekly_report(snap, alerts, CLIENT, [], fmt="markdown")
    assert "**Laporan Mingguan Google Ads**" in md


def test_formatting_helpers():
    assert rupiah(1234567.4) == "Rp1.234.567"
    assert number(1234.5, 1) == "1.234,5"
    assert change(120, 100) == " (▲20% ✅)"
    assert change(120, 100, neutral=True) == " (▲20%)"
    assert change(80, 100, lower_is_better=True) == " (▼20% ✅)"
    assert change(None, 100) == ""


def test_cli_alerts_and_report(tmp_path, capsys, monkeypatch):
    settings = Settings("t", "c", "s", "r", "", "", Safety(audit_log_dir=tmp_path), tmp_path / "clients.yaml")
    ctx = Context(settings, FakeSearch(), {"tigapilar": CLIENT}, AuditLog(tmp_path))
    monkeypatch.setattr(cli.Context, "load", classmethod(lambda cls: ctx))
    monkeypatch.setattr("gads_mcp.collect.today_local", lambda: TODAY)
    monkeypatch.setattr("gads_mcp.service.today_local", lambda: TODAY)

    assert cli.main(["alerts"]) == 2  # ada alert KRITIS
    out = capsys.readouterr().out
    assert "[tigapilar]" in out and "🔴" in out

    assert cli.main(["report", "--client", "tigapilar", "--send"]) == 0
    out = capsys.readouterr().out
    assert "WA console → 6281200000000" in out
    assert "✔ whatsapp:console" in out
