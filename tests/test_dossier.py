from datetime import date

from gads_mcp import cli, dossier
from gads_mcp.config import Safety, Settings
from gads_mcp.safety import AuditLog
from gads_mcp.service import Context

from test_collect_report import CLIENT, TODAY, FakeSearch


class DossierSearch(FakeSearch):
    def search(self, cid, q):
        if "ORDER BY campaign.name" in q and "campaign.status = 'ENABLED'" in q:
            return [{"campaign.id": "1", "campaign.name": "Lead-Search", "campaign.advertising_channel_type": "SEARCH",
                     "campaign.bidding_strategy_type": "MAXIMIZE_CONVERSIONS", "campaign_budget.amount": 50000.0,
                     "campaign.network_settings.target_search_network": True, "campaign.ai_max_setting.enable_ai_max": True}]
        if "FROM campaign WHERE segments.date BETWEEN '2026-07-17'" in q:
            return [{"campaign.id": "1", "campaign.name": "Lead-Search", "metrics.cost": 9_000_000.0,
                     "metrics.clicks": 1300, "metrics.impressions": 15000, "metrics.conversions": 600.0}]
        if "FROM conversion_action" in q:
            return [{"conversion_action.name": n, "conversion_action.primary_for_goal": True,
                     "conversion_action.category": "CONTACT", "conversion_action.counting_type": "ONE_PER_CLICK"}
                    for n in ("Form-Kontak", "KlikWhatsapp", "Scroll Depth 75", "TanyaDuluDeh")]
        return super().search(cid, q)


def test_build_kit_contents():
    data = dossier.fetch_data(DossierSearch(), CLIENT, today=TODAY)
    assert data["periode"] == ("2026-07-17", "2026-10-14")
    kit = dossier.build_kit(CLIENT, data)
    assert set(kit) == set(dossier.FILES)
    skill, profile = kit["SKILL.md"], kit["references/account-profile.md"]
    assert skill.startswith("---\nname: adsai-tigapilar\ndescription: ")
    assert 'customer_id="5827572362"' in skill
    assert "## Jenis akun: MACET" in skill and "references/account-profile.md" in skill
    assert "geotextile, plastik cor" in skill                        # kata dilindungi
    skill = profile
    assert "| Lead-Search | 1 | SEARCH | MAXIMIZE_CONVERSIONS | Rp50.000 | Rp9.000.000 | 600,0 | Rp15.000 | " \
           "Search Partners ON, AI Max ON |" in skill
    assert "4 konversi berstatus **Utama**" in skill and "menggelembung" in skill
    assert '"jual plastik cor" — 9,0 konv.' in skill               # pencarian pemenang
    assert "[KRITIS]" in skill                                       # temuan terkini
    cands = skill.split("Kandidat negatif")[1].split("## Temuan")[0]
    assert 'EXACT "plastic product"' in cands
    assert '"dan"' not in cands and '"woven"' not in cands and 'PHRASE "geotextile"' not in cands
    assert "6281200000000" not in "".join(kit.values())              # kontak klien tidak bocor
    assert "Prompt book — PT Tiga Pilar" in kit["PROMPTBOOK.md"] and "'Lead-Search'" in kit["PROMPTBOOK.md"]
    assert "Menu, bukan naskah" in kit["PROMPTBOOK.md"]
    assert "Rp15.000" in kit["AGENTS.md"] and "confirm=true" in kit["AGENTS.md"]
    assert "Project instructions" in kit["README.md"]
    assert "Baca sebelum menulis" in kit["references/working-with-adsai.md"]


def test_cli_writes_dossier(tmp_path, capsys, monkeypatch):
    settings = Settings("t", "c", "s", "r", "", "", Safety(audit_log_dir=tmp_path), tmp_path / "clients.yaml")
    ctx = Context(settings, DossierSearch(), {"tigapilar": CLIENT}, AuditLog(tmp_path))
    monkeypatch.setattr(cli.Context, "load", classmethod(lambda cls: ctx))
    monkeypatch.setattr("gads_mcp.collect.today_local", lambda: TODAY)
    monkeypatch.setattr("gads_mcp.dossier.today_local", lambda: TODAY)
    out = tmp_path / "d" / "tigapilar"
    out.mkdir(parents=True)
    (out / "AGENT_INSTRUCTIONS.md").write_text("lama")
    assert cli.main(["dossier", "--client", "tigapilar", "--out", str(tmp_path / "d")]) == 0
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*.md"))
    assert files == sorted(dossier.FILES)
    assert "dossier diperbarui" in capsys.readouterr().out


def _snap(week_conv, prev_conv, week_cost=1_000_000, prev_cost=1_000_000, not_serving=()):
    def summ(cost, conv):
        return {"biaya": cost, "konversi": conv, "biaya_per_konversi": cost / conv if conv else None}
    return {"minggu_ini": summ(week_cost, week_conv), "minggu_lalu": summ(prev_cost, prev_conv),
            "kampanye_aktif_tidak_tayang": list(not_serving)}


def test_classify_account():
    many = {"konversi": 200}
    assert dossier.classify_account(_snap(10, 10, not_serving=[{"id": 1}]), many)["jenis"] == "MACET"
    assert dossier.classify_account(_snap(5, 5), {"konversi": 12})["jenis"] == "BARU"
    assert dossier.classify_account(_snap(6, 10), many)["jenis"] == "MACET"
    assert dossier.classify_account(_snap(13, 10, week_cost=1_100_000), many)["jenis"] == "BERTUMBUH"
    assert dossier.classify_account(_snap(10, 10), many)["jenis"] == "STABIL"


def test_kit_uses_strategy():
    from gads_mcp import strategy

    data = dossier.fetch_data(DossierSearch(), CLIENT, today=TODAY)
    data["strategi"] = strategy.parse('## Active Directives\n### Google Ads\n'
                                      '- AVOID: "plastic product" — bukan pasar kami\n'
                                      '- PREFER: "plastik cor" — margin tertinggi\n')["arahan"]
    kit = dossier.build_kit(CLIENT, data)
    assert '[Google Ads] AVOID: "plastic product" — bukan pasar kami' in kit["SKILL.md"]
    profile = kit["references/account-profile.md"]
    assert "Penyimpangan strategi" in profile and '"plastic product"' in profile.split("Penyimpangan strategi")[1]
