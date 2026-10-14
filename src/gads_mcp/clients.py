"""Daftar klien agensi (clients.yaml) — dasar untuk alert, laporan, dan analisa per klien."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import normalize_customer_id


@dataclass(frozen=True)
class Thresholds:
    zero_conversion_days: int = 3      # alert bila konversi 0 selama N hari berturut-turut (dengan biaya)
    cpa_increase_pct: float = 50.0     # alert bila CPA minggu ini naik > X% dari minggu lalu
    anomaly_z: float = 2.5             # sensitivitas deteksi anomali harian
    min_waste_cost: float = 20_000     # biaya minimum istilah tanpa konversi untuk diusulkan jadi negatif


@dataclass(frozen=True)
class Client:
    key: str
    name: str
    customer_id: str
    whatsapp: tuple[str, ...] = ()
    email: tuple[str, ...] = ()
    target_cpa: float | None = None
    monthly_budget: float | None = None
    protected_terms: tuple[str, ...] = ()
    thresholds: Thresholds = field(default_factory=Thresholds)
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "nama": self.name,
            "customer_id": self.customer_id,
            "whatsapp": list(self.whatsapp),
            "email": list(self.email),
            "target_cpa": self.target_cpa,
            "budget_bulanan": self.monthly_budget,
            "kata_dilindungi": list(self.protected_terms),
            "aktif": self.active,
        }


def normalize_phone(raw: str) -> str:
    """'0812-3456-7890' / '+62 812...' -> '6281234567890'."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    if not digits.startswith("62") or not 10 <= len(digits) <= 15:
        raise ValueError(f"Nomor WhatsApp tidak valid: {raw!r}")
    return digits


def _as_tuple(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def parse_clients(data: dict[str, Any]) -> dict[str, Client]:
    defaults = data.get("defaults") or {}
    clients: dict[str, Client] = {}
    for key, raw in (data.get("clients") or {}).items():
        raw = raw or {}
        th = {**(defaults.get("thresholds") or {}), **(raw.get("thresholds") or {})}
        unknown = set(th) - set(Thresholds.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Klien '{key}': threshold tidak dikenal {sorted(unknown)}")
        cid = normalize_customer_id(raw.get("customer_id"))
        if not cid:
            raise ValueError(f"Klien '{key}': customer_id wajib diisi.")
        clients[key] = Client(
            key=key,
            name=raw.get("name") or key,
            customer_id=cid,
            whatsapp=tuple(normalize_phone(p) for p in _as_tuple(raw.get("whatsapp"))),
            email=_as_tuple(raw.get("email")),
            target_cpa=raw.get("target_cpa"),
            monthly_budget=raw.get("monthly_budget"),
            protected_terms=tuple(t.lower() for t in _as_tuple(raw.get("protected_terms"))),
            thresholds=Thresholds(**th),
            active=raw.get("active", True),
        )
    return clients


def load_clients(path: Path) -> dict[str, Client]:
    path = Path(path)
    if not path.exists():
        raise RuntimeError(
            f"File klien {path} belum ada. Salin clients.example.yaml menjadi clients.yaml lalu isi."
        )
    return parse_clients(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def find_client(clients: dict[str, Client], ref: str) -> Client:
    """Cari klien berdasarkan key, customer ID, atau sebagian nama."""
    if ref in clients:
        return clients[ref]
    try:
        cid = normalize_customer_id(ref)
    except ValueError:
        cid = ""
    for c in clients.values():
        if cid and c.customer_id == cid:
            return c
    matches = [c for c in clients.values() if ref.lower() in c.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"Klien '{ref}' tidak ditemukan di clients.yaml.")
    raise ValueError(f"'{ref}' cocok dengan beberapa klien: {[c.key for c in matches]}")
