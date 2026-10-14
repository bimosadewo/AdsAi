"""Pengaman untuk perubahan akun: aturan budget, mode baca-saja, dan log audit (JSON Lines)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Safety


class SafetyError(ValueError):
    """Perubahan ditolak oleh aturan pengaman AdsAi (bukan oleh Google)."""


def ensure_writable(safety: Safety) -> None:
    if safety.read_only:
        raise SafetyError("AdsAi sedang mode baca-saja (ADSAI_READ_ONLY=true). Perubahan tidak dijalankan.")


def check_budget(safety: Safety, current: float | None, new: float) -> list[str]:
    """Kembalikan catatan; lempar SafetyError bila melanggar batas."""
    notes: list[str] = []
    if new <= 0:
        raise SafetyError("Budget harian harus lebih dari 0.")
    if safety.max_daily_budget is not None and new > safety.max_daily_budget:
        raise SafetyError(
            f"Budget {new:,.0f} melebihi batas ADSAI_MAX_DAILY_BUDGET ({safety.max_daily_budget:,.0f})."
        )
    if current:
        factor = new / current
        if factor > safety.max_budget_increase_factor:
            raise SafetyError(
                f"Kenaikan {factor:.1f}x (dari {current:,.0f} ke {new:,.0f}) melebihi batas "
                f"{safety.max_budget_increase_factor:g}x per perubahan. Naikkan bertahap."
            )
        if factor < 0.5:
            notes.append(f"Budget turun {1 - factor:.0%}; kampanye bisa kehilangan banyak tayangan.")
    return notes


def to_micros(amount: float) -> int:
    """Uang -> micros, dibulatkan ke 10.000 micros (unit terkecil yang diterima Google Ads)."""
    micros = int(round(amount * 1_000_000))
    return int(round(micros / 10_000) * 10_000)


class AuditLog:
    """Satu file JSONL per customer: logs/<customer_id>.jsonl."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _path(self, customer_id: str) -> Path:
        return self.directory / f"{customer_id}.jsonl"

    def write(
        self,
        customer_id: str,
        action: str,
        target: dict[str, Any],
        before: Any,
        after: Any,
        result: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = {
            "waktu": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operator": os.getenv("ADSAI_OPERATOR") or os.getenv("USER") or os.getenv("USERNAME") or "unknown",
            "customer_id": customer_id,
            "aksi": action,
            "target": target,
            "sebelum": before,
            "sesudah": after,
            "hasil": result,
        }
        if note:
            entry["catatan"] = note
        with self._path(customer_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def read(self, customer_id: str, limit: int = 50, since: datetime | None = None) -> list[dict[str, Any]]:
        path = self._path(customer_id)
        if not path.exists():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if since and datetime.fromisoformat(entry["waktu"]) < since:
                continue
            entries.append(entry)
        return entries[-limit:][::-1]


class FailureLog:
    """Panggilan tool yang gagal (logs/_gagal.jsonl) — bahan `why_did_this_fail`.

    Agen sering hanya melihat ringkasan error atau sesi sudah berganti; log ini menyimpan error asli Google
    beserta kodenya agar penyebab bisa dibaca ulang, bukan ditebak. Tidak menyimpan kredensial.
    """

    MAX_LINES = 500

    def __init__(self, directory: Path):
        self.path = Path(directory) / "_gagal.jsonl"

    def write(self, tool: str, arguments: dict[str, Any], message: str, codes: list[str] | None = None) -> None:
        args = {k: v for k, v in arguments.items() if k != "confirm"}
        entry = {
            "waktu": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool,
            "customer_id": arguments.get("customer_id"),
            "klien": arguments.get("client"),
            "confirm": bool(arguments.get("confirm")),
            "argumen": json.dumps(args, ensure_ascii=False, default=str)[:600],
            "kode": codes or [],
            "pesan": message[:1500],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
            lines = (lines + [json.dumps(entry, ensure_ascii=False)])[-self.MAX_LINES:]
            self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass  # pencatatan kegagalan tidak boleh menutupi error aslinya

    def read(self, limit: int = 10, tool: str | None = None, customer_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in reversed(self.path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            e = json.loads(line)
            if tool and e.get("tool") != tool:
                continue
            if customer_id and e.get("customer_id") not in (customer_id, None):
                continue
            out.append(e)
            if len(out) >= limit:
                break
        return out
