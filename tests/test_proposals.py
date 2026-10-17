from datetime import datetime, timedelta, timezone

import pytest

from gads_mcp import proposals as pr
from gads_mcp import server


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setenv("ADSAI_PROPOSALS_FILE", str(tmp_path / "p.json"))
    calls = []

    def fake_set_status(level, ids, status, customer_id=None, confirm=False):
        calls.append(confirm)
        if confirm:
            return {"status": "BERHASIL", "terverifikasi": {"status": status}}
        return {"status": "PRATINJAU", "aksi": "set_status_campaign", "target": {"ids": ids},
                "sebelum": [{"status": "ENABLED"}], "sesudah": {"status": status}, "detail_panjang": "x" * 100}

    monkeypatch.setitem(server.WRITE_TOOLS, "set_status", fake_set_status)
    return calls


ARGS = {"level": "campaign", "ids": ["123"], "status": "PAUSED", "confirm": True}


def test_propose_runs_preview_only_and_queues(queue):
    out = server.propose_change("set_status", ARGS, "0 konversi 3 hari, biaya Rp200rb", "pemindaian pagi")
    assert out["status"] == "DIANTREKAN" and queue == [False]           # confirm dari pengusul diabaikan
    u = out["usulan"]
    assert u["argumen"] == {"level": "campaign", "ids": ["123"], "status": "PAUSED"}
    assert "detail_panjang" not in u["pratinjau"] and u["pratinjau"]["sesudah"] == {"status": "PAUSED"}
    assert server.propose_change("set_status", ARGS, "lagi")["status"] == "SUDAH_ADA"
    assert server.list_proposals()["menunggu"] == 1


def test_excluded_and_unknown_tools_rejected(queue):
    from mcp.server.mcpserver.exceptions import ToolError
    for name in ("send_to_client", "decide_proposal", "run_query", "tidak_ada"):
        with pytest.raises(ToolError):
            server.propose_change(name, {}, "x")


def test_approve_two_step_then_executes_once(queue):
    pid = server.propose_change("set_status", ARGS, "alasan")["usulan"]["id"]
    pre = server.decide_proposal(pid, "setujui")
    assert pre["status"] == "PRATINJAU" and pre["pratinjau_sekarang"]["status"] == "PRATINJAU"
    assert queue == [False, False]
    done = server.decide_proposal(pid, "setujui", confirm=True)
    assert done["status"] == "DISETUJUI" and done["usulan"]["hasil"]["status"] == "BERHASIL"
    assert queue == [False, False, True]
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError):                                       # tidak bisa diterapkan dua kali
        server.decide_proposal(pid, "setujui", confirm=True)


def test_reject_never_executes(queue):
    pid = server.propose_change("set_status", ARGS, "alasan")["usulan"]["id"]
    assert server.decide_proposal(pid, "tolak", confirm=True)["status"] == "DITOLAK"
    assert queue == [False]
    assert server.list_proposals("DITOLAK")["jumlah"] == 1


def test_proposals_expire_after_72h(queue):
    p = pr.Proposal("set_status", {"ids": ["1"]}, "x", {"status": "PRATINJAU"},
                    dibuat=(datetime.now(timezone.utc) - timedelta(hours=73)).isoformat(timespec="seconds"))
    pr.save([p])
    assert pr.load()[0].status == pr.EXPIRED
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError):
        server.decide_proposal(p.id, "setujui", confirm=True)
    assert queue == []
