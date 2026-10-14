"""Validasi teks Responsive Search Ad (RSA) — tanpa API, dipakai sebelum membuat/mengubah iklan.

Aturan keras (ditolak Google): jumlah & panjang headline/deskripsi, path, tanda seru di headline,
posisi pin. Aturan lunak (peringatan): klaim superlatif yang perlu bukti, duplikat, huruf kapital
berlebihan, jumlah aset di bawah praktik terbaik.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

HEADLINE_MIN, HEADLINE_MAX, HEADLINE_LEN = 3, 15, 30
DESC_MIN, DESC_MAX, DESC_LEN = 2, 4, 90
PATH_LEN = 15
HEADLINE_PINS = {1: "HEADLINE_1", 2: "HEADLINE_2", 3: "HEADLINE_3"}
DESC_PINS = {1: "DESCRIPTION_1", 2: "DESCRIPTION_2"}

# Klaim yang biasanya butuh bukti pihak ketiga menurut kebijakan Google Ads.
SUPERLATIVES = re.compile(
    r"\b(terbaik|paling (murah|bagus|baik|lengkap|terpercaya)|termurah|terlengkap|terpercaya no\.? ?1|"
    r"nomor (1|satu)|no\.? ?1|#1|best|cheapest|number one|100% (aman|garansi|terjamin))\b",
    re.IGNORECASE,
)


@dataclass
class AdCopy:
    headlines: list[dict[str, Any]]      # [{"text": str, "pin": int|None}]
    descriptions: list[dict[str, Any]]
    path1: str | None = None
    path2: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.ok,
            "error": self.errors,
            "peringatan": self.warnings,
            "headlines": self.headlines,
            "descriptions": self.descriptions,
            "path1": self.path1,
            "path2": self.path2,
        }


def _items(raw: list[Any] | None) -> list[dict[str, Any]]:
    """Terima ["teks", ...] atau [{"text": "...", "pin": 1}, ...]."""
    out = []
    for item in raw or []:
        if isinstance(item, str):
            out.append({"text": " ".join(item.split()), "pin": None})
        elif isinstance(item, dict):
            pin = item.get("pin")
            out.append({"text": " ".join(str(item.get("text", "")).split()), "pin": int(pin) if pin else None})
        else:
            raise ValueError(f"Format aset tidak dikenal: {item!r}")
    return out


def _check_group(name: str, items: list[dict], lo: int, hi: int, max_len: int, pins: dict[int, str],
                 errors: list[str], warnings: list[str]) -> None:
    if not lo <= len(items) <= hi:
        errors.append(f"Jumlah {name} harus {lo}-{hi}, didapat {len(items)}.")
    seen: dict[str, int] = {}
    for i, it in enumerate(items, 1):
        text = it["text"]
        if not text:
            errors.append(f"{name} #{i} kosong.")
            continue
        if len(text) > max_len:
            errors.append(f"{name} #{i} terlalu panjang ({len(text)}/{max_len}): {text!r}")
        if it["pin"] is not None and it["pin"] not in pins:
            errors.append(f"{name} #{i}: pin harus salah satu dari {sorted(pins)}.")
        key = text.lower()
        if key in seen:
            errors.append(f"{name} #{i} duplikat dengan #{seen[key]}: {text!r}")
        seen.setdefault(key, i)
        if SUPERLATIVES.search(text):
            warnings.append(f"{name} #{i} berisi klaim superlatif ({text!r}) — Google bisa menolak tanpa bukti "
                            "pihak ketiga di landing page.")
        letters = [c for c in text if c.isalpha()]
        if len(letters) >= 6 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            errors.append(f"{name} #{i} memakai huruf kapital berlebihan (ditolak Google): {text!r}")
    pinned = [it for it in items if it["pin"]]
    if pinned and len(pinned) == len(items):
        warnings.append(f"Semua {name} di-pin — RSA kehilangan fleksibilitas kombinasi.")


def validate(headlines: list[Any] | None, descriptions: list[Any] | None,
             path1: str | None = None, path2: str | None = None,
             partial: bool = False) -> AdCopy:
    """partial=True dipakai saat update: bagian yang None tidak diperiksa jumlahnya."""
    h, d = _items(headlines), _items(descriptions)
    copy = AdCopy(h, d, (path1 or "").strip() or None, (path2 or "").strip() or None)
    if headlines is not None or not partial:
        _check_group("Headline", h, HEADLINE_MIN, HEADLINE_MAX, HEADLINE_LEN, HEADLINE_PINS, copy.errors, copy.warnings)
        for i, it in enumerate(h, 1):
            if "!" in it["text"]:
                copy.errors.append(f"Headline #{i} tidak boleh memakai tanda seru: {it['text']!r}")
        if len(h) < 10:
            copy.warnings.append(f"Baru {len(h)} headline; 10-15 headline yang beragam membantu ad strength "
                                 "'Baik/Sangat baik'.")
    if descriptions is not None or not partial:
        _check_group("Deskripsi", d, DESC_MIN, DESC_MAX, DESC_LEN, DESC_PINS, copy.errors, copy.warnings)
        if len(d) < 4:
            copy.warnings.append(f"Baru {len(d)} deskripsi; pakai 4 untuk kombinasi maksimal.")
    for name, value in (("path1", copy.path1), ("path2", copy.path2)):
        if value and len(value) > PATH_LEN:
            copy.errors.append(f"{name} terlalu panjang ({len(value)}/{PATH_LEN}): {value!r}")
        if value and " " in value:
            copy.errors.append(f"{name} tidak boleh berisi spasi (pakai tanda -): {value!r}")
    if copy.path2 and not copy.path1:
        copy.errors.append("path2 tidak boleh diisi tanpa path1.")
    return copy


def validate_urls(urls: list[str] | None) -> list[str]:
    errors = []
    for u in urls or []:
        if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", u):
            errors.append(f"URL tidak valid: {u!r}")
    return errors
