"""Alur kerja tingkat klien yang dipakai bersama oleh server MCP dan CLI terjadwal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .analysis import Alert, evaluate_alerts
from .client import Ads
from .clients import Client, find_client, load_clients
from .collect import collect_snapshot, today_local
from .config import Settings, load_settings
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


def send_message(client: Client, subject: str, text: str) -> list[dict[str, Any]]:
    return [r.to_dict() for r in deliver(client, subject, text)]


def alerts_message(client: Client, alerts: list[Alert]) -> str:
    return format_alerts(client.name, alerts)
