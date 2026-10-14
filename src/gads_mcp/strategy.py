"""Strategi per klien (strategi/<klien>.md) — keputusan tim yang harus diingat agen di setiap sesi.

Format mengikuti STRATEGY.md Adspirer agar bisa dibaca manusia dan agen:

    # PT Contoh — Strategy Playbook

    ## Active Directives

    ### Google Ads
    - AVOID: "gratis" — klien tidak melayani sampel gratis
    - PREFER: "geotextile" — margin tertinggi
    - CONSTRAINT: CPC maks Rp5.000 — keputusan klien
    - REQUIRE: semua iklan menyebut "garansi 1 tahun" — kebijakan merek

    ## Decision Log

    ### 2026-10-14 — gratis
    - AVOID: "gratis" — klien tidak melayani sampel gratis

    ## Archived Directives

Teks dalam tanda kutip dipakai mesin: istilah PREFER ikut dilindungi dari usulan negatif, istilah AVOID yang
masih memakan biaya dilaporkan sebagai *penyimpangan strategi*. Arahan hanya ditambahkan setelah pengguna
mengonfirmasi (tool `add_strategy_directive` dengan pratinjau → confirm=true).
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .config import PROJECT_ROOT

KINDS = ("AVOID", "PREFER", "CONSTRAINT", "REQUIRE")
SECTIONS = ("Lintas Platform", "Google Ads", "Alokasi Budget", "Materi Iklan")
_LINE = re.compile(r"^\s*(?:[-*]\s*)?(AVOID|PREFER|CONSTRAINT|REQUIRE):\s*(.+?)\s*$")
_REASON = re.compile(r"\s+(?:—|–|--)\s+")
_QUOTED = re.compile(r"[\"“”]([^\"“”]+)[\"“”]")


@dataclass(frozen=True)
class Directive:
    jenis: str
    teks: str
    alasan: str | None
    bagian: str

    @property
    def terms(self) -> list[str]:
        return [t.strip().lower() for t in _QUOTED.findall(self.teks) if t.strip()]

    def line(self) -> str:
        return f"- {self.jenis}: {self.teks}" + (f" — {self.alasan}" if self.alasan else "")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "istilah": self.terms}


def strategy_dir() -> Path:
    return Path(os.getenv("ADSAI_STRATEGY_DIR") or PROJECT_ROOT / "strategi")


def path_for(client_key: str, directory: Path | None = None) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", client_key.lower()).strip("-") or "klien"
    return (directory or strategy_dir()) / f"{slug}.md"


def parse(text: str) -> dict[str, Any]:
    directives: list[Directive] = []
    decisions: list[str] = []
    top = section = None
    for raw in text.splitlines():
        if raw.startswith("## "):
            top, section = raw[3:].strip().lower(), None
            continue
        if raw.startswith("### "):
            section = raw[4:].strip()
            if top == "decision log":
                decisions.append(section)
            continue
        if top != "active directives":
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        body = m.group(2)
        parts = _REASON.split(body, maxsplit=1)
        directives.append(Directive(m.group(1), parts[0].strip(), parts[1].strip() if len(parts) > 1 else None,
                                    section or "Lintas Platform"))
    return {"arahan": directives, "keputusan": decisions}


def load(client_key: str, directory: Path | None = None) -> dict[str, Any]:
    path = path_for(client_key, directory)
    if not path.exists():
        return {"berkas": str(path), "ada": False, "arahan": [], "keputusan": []}
    return {"berkas": str(path), "ada": True, **parse(path.read_text(encoding="utf-8"))}


def template(client_name: str) -> str:
    lines = [f"# {client_name} — Strategy Playbook", "", "## Active Directives", ""]
    for s in SECTIONS:
        lines += [f"### {s}", "[No active directives]", ""]
    lines += ["## Decision Log", "", "## Archived Directives", ""]
    return "\n".join(lines)


def build_directive(kind: str, text: str, reason: str | None, section: str) -> Directive:
    kind = kind.upper().strip()
    if kind not in KINDS:
        raise ValueError(f"jenis harus salah satu dari {KINDS}")
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("teks arahan tidak boleh kosong")
    if _REASON.search(text):
        raise ValueError("teks arahan tidak boleh memuat ' — '; tulis alasannya di parameter reason")
    if not reason or not reason.strip():
        raise ValueError("reason wajib diisi: arahan tanpa alasan sulit ditinjau ulang nanti")
    return Directive(kind, text, " ".join(reason.split()), section.strip() or "Google Ads")


def add_directive(text: str, directive: Directive, topic: str, today: date) -> str:
    """Sisipkan arahan ke bagiannya (dibuat bila belum ada) + catat di Decision Log. Fungsi murni."""
    lines = text.splitlines()

    def find(header: str, start: int = 0, stop: int | None = None) -> int | None:
        for i in range(start, len(lines) if stop is None else stop):
            if lines[i].strip().lower() == header.lower():
                return i
        return None

    def section_end(i: int) -> int:
        j = i + 1
        while j < len(lines) and not lines[j].startswith("#"):
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        return j

    active = find("## Active Directives")
    log = find("## Decision Log")
    if active is None or log is None or log < active:
        raise ValueError("Berkas strategi tidak memuat '## Active Directives' lalu '## Decision Log'.")
    sec = find(f"### {directive.bagian}", active, log)
    if sec is None:
        lines[log:log] = [f"### {directive.bagian}", directive.line(), ""]
    else:
        end = section_end(sec)
        body = [k for k in range(sec + 1, end) if lines[k].strip() == "[No active directives]"]
        if body:
            lines[body[0]] = directive.line()
        else:
            lines.insert(end, directive.line())
    log = find("## Decision Log")
    end = section_end(log)
    entry = [f"### {today.isoformat()} — {topic}", directive.line()]
    lines[end:end] = ["", *entry]
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).rstrip("\n") + "\n"


def protected_terms(directives: Iterable[Directive]) -> list[str]:
    return sorted({t for d in directives if d.jenis == "PREFER" for t in d.terms})


def drift(search_terms: Iterable[dict[str, Any]], directives: Iterable[Directive]) -> list[dict[str, Any]]:
    """Search term yang memuat istilah AVOID dan masih memakan biaya = penyimpangan strategi."""
    avoid = [(t, d) for d in directives if d.jenis == "AVOID" for t in d.terms]
    out = []
    for row in search_terms:
        term = str(row.get("search_term_view.search_term") or "").lower()
        cost = float(row.get("metrics.cost") or 0)
        if not term or cost <= 0:
            continue
        for t, d in avoid:
            if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", term):
                out.append({"search_term": term, "biaya": cost, "konversi": float(row.get("metrics.conversions") or 0),
                            "melanggar": d.line()[2:]})
                break
    return sorted(out, key=lambda x: -x["biaya"])
