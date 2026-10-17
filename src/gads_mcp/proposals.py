"""Antrean persetujuan: usulan perubahan akun yang menunggu keputusan pengguna (setara list_pending_actions /
manage_action Adspirer).

Alur: agen (mis. pemindaian pagi terjadwal) memanggil propose_change → AdsAi langsung menjalankan PRATINJAU tool
tersebut (validasi Google, tidak mengubah apa pun) dan menyimpannya di antrean. Pengguna meninjau dari chat mana
pun (juga HP) → decide_proposal setujui/tolak. Hanya "setujui" + confirm=true yang menjalankan perubahan, lewat
tool yang sama sehingga semua batas pengaman (read-only, batas budget, log audit, baca-ulang) tetap berlaku.

Usulan kedaluwarsa setelah 72 jam: data akun berubah, usulan lama tidak boleh diterapkan diam-diam.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

TTL = timedelta(hours=72)
PENDING, APPROVED, REJECTED, FAILED, EXPIRED = "MENUNGGU", "DISETUJUI", "DITOLAK", "GAGAL", "KEDALUWARSA"
# Bukan perubahan akun / punya alur persetujuan sendiri — tidak boleh lewat antrean.
EXCLUDED = {"send_to_client", "add_strategy_directive", "decide_proposal"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Proposal:
    tool: str
    argumen: dict[str, Any]
    alasan: str
    pratinjau: dict[str, Any]
    sumber: str = "chat"
    status: str = PENDING
    dibuat: str = field(default_factory=lambda: _now().isoformat(timespec="seconds"))
    diputuskan: str | None = None
    hasil: dict[str, Any] | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:6])

    def expired(self, now: datetime | None = None) -> bool:
        return self.status == PENDING and (now or _now()) - datetime.fromisoformat(self.dibuat) > TTL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def store_path() -> Path:
    return Path(os.getenv("ADSAI_PROPOSALS_FILE") or PROJECT_ROOT / "proposals.json")


def load(path: Path | None = None) -> list[Proposal]:
    p = path or store_path()
    if not p.exists():
        return []
    items = [Proposal(**d) for d in json.loads(p.read_text(encoding="utf-8")).get("usulan", [])]
    for it in items:
        if it.expired():
            it.status = EXPIRED
    return items


def save(items: list[Proposal], path: Path | None = None) -> None:
    p = path or store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = [i for i in items if i.status == PENDING] + [i for i in items if i.status != PENDING][-200:]
    data = json.dumps({"usulan": [i.to_dict() for i in keep]}, ensure_ascii=False, indent=2, default=str)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=p.parent, delete=False, suffix=".tmp") as t:
        t.write(data)
    os.replace(t.name, p)


def find(items: list[Proposal], pid: str) -> Proposal:
    for i in items:
        if i.id == pid:
            return i
    raise ValueError(f"Usulan {pid} tidak ditemukan. Lihat list_proposals.")


def clean_args(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """confirm ditentukan oleh antrean, bukan oleh pengusul."""
    return {k: v for k, v in (arguments or {}).items() if k != "confirm"}


def is_preview(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "PRATINJAU"


def brief(preview: dict[str, Any]) -> dict[str, Any]:
    """Ringkas pratinjau untuk disimpan/ditampilkan (tanpa detail panjang)."""
    keys = ("status", "aksi", "customer_id", "target", "sebelum", "sesudah", "catatan", "peringatan", "pesan")
    return {k: preview[k] for k in keys if k in preview}
