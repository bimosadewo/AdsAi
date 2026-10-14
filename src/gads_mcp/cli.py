"""Perintah terjadwal AdsAi (untuk cron / Windows Task Scheduler).

Contoh:
    adsai clients
    adsai alerts                     # cek semua klien aktif, tampilkan hasil
    adsai alerts --send              # kirim alert ke klien yang punya masalah
    adsai report --client tigapilar  # pratinjau laporan mingguan
    adsai report --send              # kirim laporan mingguan ke semua klien aktif
    adsai dossier --client tigapilar # perbarui kit dossier klien untuk agen AI

Kode keluar: 0 = aman, 2 = ada alert KRITIS, 1 = error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .clients import load_clients
from .config import PROJECT_ROOT, find_env_file
from .service import Context, alerts_message, build_dossier, build_weekly_report, client_alerts, send_message


def _print_results(results: list[dict]) -> None:
    for r in results:
        mark = "✔" if r["berhasil"] else "✘"
        print(f"  {mark} {r['kanal']} → {r['ke']}: {r['detail']}")


def cmd_clients(args) -> int:
    """Tidak butuh kredensial Google: berguna untuk memeriksa clients.yaml lebih awal."""
    load_dotenv(find_env_file())
    clients = load_clients(Path(os.getenv("ADSAI_CLIENTS_FILE") or PROJECT_ROOT / "clients.yaml"))
    for c in clients.values():
        status = "aktif" if c.active else "nonaktif"
        print(f"{c.key:20s} {c.customer_id}  {c.name}  ({status}, WA: {len(c.whatsapp)}, email: {len(c.email)})")
    return 0


def cmd_alerts(ctx: Context, args) -> int:
    worst = 0
    for client in ctx.active_clients(args.client):
        try:
            alerts, _ = client_alerts(ctx, client)
        except Exception as ex:  # satu klien gagal tidak menghentikan yang lain
            print(f"[{client.key}] GAGAL: {ex}", file=sys.stderr)
            worst = max(worst, 1)
            continue
        text = alerts_message(client, alerts)
        print(f"\n[{client.key}]\n{text}")
        if any(a.level == "KRITIS" for a in alerts):
            worst = 2
        if args.send and alerts and (not args.critical_only or any(a.level == "KRITIS" for a in alerts)):
            _print_results(send_message(client, f"Alert Google Ads — {client.name}", text))
    return worst


def cmd_report(ctx: Context, args) -> int:
    code = 0
    for client in ctx.active_clients(args.client):
        try:
            report = build_weekly_report(ctx, client, args.format)
        except Exception as ex:
            print(f"[{client.key}] GAGAL: {ex}", file=sys.stderr)
            code = 1
            continue
        if args.json:
            print(json.dumps({k: v for k, v in report.items() if k != "snapshot"}, ensure_ascii=False, indent=2))
        else:
            print(f"\n[{client.key}]\n{report['teks']}")
        if args.send:
            _print_results(send_message(client, f"Laporan Mingguan Google Ads — {client.name}", report["teks"]))
    return code


def cmd_dossier(ctx: Context, args) -> int:
    code = 0
    for client in ctx.active_clients(args.client):
        try:
            result = build_dossier(ctx, client, out_dir=args.out)
        except Exception as ex:
            print(f"[{client.key}] GAGAL: {ex}", file=sys.stderr)
            code = 1
            continue
        print(f"[{client.key}] dossier diperbarui:")
        for path in result["disimpan_di"]:
            print(f"  {path}")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adsai", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("clients", help="Daftar klien di clients.yaml")
    p_alert = sub.add_parser("alerts", help="Cek kesehatan akun klien")
    p_alert.add_argument("--client", help="key / customer ID / nama klien (default: semua klien aktif)")
    p_alert.add_argument("--send", action="store_true", help="Kirim alert ke kontak klien bila ada masalah")
    p_alert.add_argument("--critical-only", action="store_true", help="Hanya kirim bila ada alert KRITIS")
    p_report = sub.add_parser("report", help="Laporan mingguan")
    p_report.add_argument("--client")
    p_report.add_argument("--send", action="store_true", help="Kirim ke kontak klien")
    p_report.add_argument("--format", choices=["whatsapp", "markdown"], default="whatsapp")
    p_report.add_argument("--json", action="store_true", help="Cetak JSON (teks + alert)")
    p_dossier = sub.add_parser("dossier", help="Buat/perbarui kit dossier klien untuk agen AI")
    p_dossier.add_argument("--client")
    p_dossier.add_argument("--out", help="Folder tujuan (default: ./dossiers)")
    args = parser.parse_args(argv)

    if args.cmd == "clients":
        try:
            return cmd_clients(args)
        except Exception as ex:
            print(f"Error: {ex}", file=sys.stderr)
            return 1
    try:
        ctx = Context.load()
    except Exception as ex:
        print(f"Error: {ex}", file=sys.stderr)
        return 1
    return {"alerts": cmd_alerts, "report": cmd_report, "dossier": cmd_dossier}[args.cmd](ctx, args)


if __name__ == "__main__":
    sys.exit(main())
