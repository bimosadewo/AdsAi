"""Alur kerja tingkat klien yang dipakai bersama oleh server MCP dan CLI terjadwal."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import dossier
from .analysis import Alert, evaluate_alerts
from .client import Ads
from .clients import Client, find_client, load_clients
from .collect import collect_snapshot, today_local
from .config import PROJECT_ROOT, Settings, load_settings
from .notify import deliver
from .reports import format_alerts, weekly_report
from .safety import AuditLog


@dataclass
class Context:
    settings: Settings
    ads: Any
    clients: dict[str, Client]
    audit: AuditLog

    @classmethod
    def load(cls) -> "Context":
        settings = load_settings()
        return cls(settings, Ads(settings), load_clients(settings.clients_file),
                   AuditLog(settings.safety.audit_log_dir))

    def client(self, ref: str) -> Client:
        return find_client(self.clients, ref)

    def active_clients(self, ref: str | None = None) -> list[Client]:
        if ref:
            return [self.client(ref)]
        return [c for c in self.clients.values() if c.active]


def client_alerts(ctx: Context, client: Client) -> tuple[list[Alert], dict[str, Any]]:
    snapshot = collect_snapshot(ctx.ads, client, with_search_terms=False)
    return evaluate_alerts(snapshot, client), snapshot


def build_weekly_report(ctx: Context, client: Client, fmt: str = "whatsapp") -> dict[str, Any]:
    snapshot = collect_snapshot(ctx.ads, client)
    alerts = evaluate_alerts(snapshot, client)
    since = datetime.combine(today_local() - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)
    changes = ctx.audit.read(client.customer_id, limit=50, since=since)
    text = weekly_report(snapshot, alerts, client, changes, fmt)
    return {"klien": client.name, "teks": text, "alert": [a.to_dict() for a in alerts], "snapshot": snapshot}


def build_dossier(ctx: Context, client: Client, write: bool = True, out_dir: str | None = None) -> dict[str, Any]:
    kit = dossier.build_kit(client, dossier.fetch_data(ctx.ads, client))
    result: dict[str, Any] = {"klien": client.name, "berkas": kit}
    if write:
        base = Path(out_dir or os.getenv("ADSAI_DOSSIER_DIR") or PROJECT_ROOT / "dossiers")
        result["disimpan_di"] = dossier.write_kit(kit, base / dossier.slug(client.key))
    return result


def send_message(client: Client, subject: str, text: str) -> list[dict[str, Any]]:
    return [r.to_dict() for r in deliver(client, subject, text)]


def alerts_message(client: Client, alerts: list[Alert]) -> str:
    return format_alerts(client.name, alerts)
