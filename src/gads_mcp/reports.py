"""Laporan mingguan & ringkasan alert dalam Bahasa Indonesia (format WhatsApp atau Markdown)."""

from __future__ import annotations

from typing import Any

from .analysis import Alert, estimated_savings, suggest_negatives
from .fmt import number, rupiah

ICON = {"KRITIS": "🔴", "PERINGATAN": "🟠", "INFO": "🔵"}


def change(now: float | None, before: float | None, lower_is_better: bool = False, neutral: bool = False) -> str:
    if not now or not before:
        return ""
    pct = (now - before) / before * 100
    if abs(pct) < 1:
        return " (=)"
    good = not neutral and ((pct < 0) if lower_is_better else (pct > 0))
    arrow = "▲" if pct > 0 else "▼"
    return f" ({arrow}{abs(pct):.0f}%{' ✅' if good else ''})"


def _bold(text: str, fmt: str) -> str:
    return f"*{text}*" if fmt == "whatsapp" else f"**{text}**"


def _date_id(iso: str) -> str:
    bulan = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    y, m, d = iso.split("-")
    return f"{int(d)} {bulan[int(m) - 1]} {y}"


def format_alerts(client_name: str, alerts: list[Alert], fmt: str = "whatsapp") -> str:
    if not alerts:
        return f"✅ {_bold(client_name, fmt)}: tidak ada masalah terdeteksi."
    lines = [f"⚠️ {_bold('Alert Google Ads — ' + client_name, fmt)}"]
    for a in alerts:
        lines.append(f"{ICON.get(a.level, '•')} {a.pesan}")
    return "\n".join(lines)


def weekly_report(snapshot: dict[str, Any], alerts: list[Alert], client: Any,
                  changes: list[dict[str, Any]] | None = None, fmt: str = "whatsapp") -> str:
    w, p = snapshot["minggu_ini"], snapshot["minggu_lalu"]
    start, end = snapshot["periode"]["minggu_ini"]
    b = lambda t: _bold(t, fmt)  # noqa: E731
    L: list[str] = []

    L.append(f"📊 {b('Laporan Mingguan Google Ads')}")
    L.append(f"{client.name} · {_date_id(start)} – {_date_id(end)}")
    L.append("")
    L.append(b("Ringkasan"))
    L.append(f"• Biaya: {rupiah(w['biaya'])}{change(w['biaya'], p['biaya'], neutral=True)}")
    L.append(f"• Klik: {number(w['klik'])}{change(w['klik'], p['klik'])}")
    L.append(f"• Konversi: {number(w['konversi'], 1)}{change(w['konversi'], p['konversi'])}")
    L.append(f"• Biaya/konversi: {rupiah(w['biaya_per_konversi'])}"
             f"{change(w['biaya_per_konversi'], p['biaya_per_konversi'], lower_is_better=True)}")
    if client.target_cpa:
        ok = w["biaya_per_konversi"] is not None and w["biaya_per_konversi"] <= client.target_cpa
        L.append(f"• Target biaya/konversi: {rupiah(client.target_cpa)} {'✅ tercapai' if ok else '⏳ belum tercapai'}")
    pacing = snapshot.get("pacing")
    if pacing:
        L.append(f"• Budget bulan ini: terpakai {rupiah(pacing['biaya_bulan_ini'])} dari "
                 f"{rupiah(pacing['budget_bulanan'])} (proyeksi {number(pacing['persen_proyeksi'], 1)}%, {pacing['status'].lower()})")

    camps = [c for c in snapshot.get("kampanye_minggu_ini", []) if float(c.get("metrics.cost") or 0) > 0][:5]
    if camps:
        L.append("")
        L.append(b("Per kampanye"))
        for c in camps:
            conv = float(c.get("metrics.conversions") or 0)
            cpa = float(c["metrics.cost"]) / conv if conv else None
            L.append(f"• {c.get('campaign.name')}: {rupiah(float(c['metrics.cost']))}, "
                     f"{number(conv, 1)} konv., {rupiah(cpa)}/konv.")

    if alerts:
        L.append("")
        L.append(b("Perlu perhatian"))
        for a in alerts:
            L.append(f"{ICON.get(a.level, '•')} {a.pesan}")

    terms = snapshot.get("istilah_minggu_ini") or []
    wasted = sorted(
        (t for t in terms if not float(t.get("metrics.conversions") or 0) and float(t.get("metrics.cost") or 0) > 0),
        key=lambda t: -float(t.get("metrics.cost") or 0),
    )[:5]
    if wasted:
        total = sum(float(t.get("metrics.cost") or 0) for t in terms if not float(t.get("metrics.conversions") or 0))
        L.append("")
        L.append(b(f"Pencarian tanpa konversi ({rupiah(total)})"))
        for t in wasted:
            L.append(f"• \"{t.get('search_term_view.search_term')}\" — {rupiah(float(t['metrics.cost']))}")

    if changes:
        L.append("")
        L.append(b("Yang dikerjakan tim minggu ini"))
        for c in changes[:8]:
            L.append(f"• {describe_change(c)}")

    recs = recommendations(snapshot, client)
    if recs:
        L.append("")
        L.append(b("Rencana minggu depan"))
        for r in recs:
            L.append(f"• {r}")

    return "\n".join(L)


ACTION_LABEL = {
    "add_negative_keywords": "Menambah kata kunci negatif",
    "remove_negative_keywords": "Menghapus kata kunci negatif",
    "update_budget": "Mengubah budget",
    "set_status_campaign": "Mengubah status kampanye",
    "set_status_ad_group": "Mengubah status grup iklan",
    "set_status_keyword": "Mengubah status kata kunci",
    "update_conversion_action": "Merapikan pengaturan konversi",
    "update_bid_strategy": "Mengubah strategi bidding",
    "add_keywords": "Menambah kata kunci",
    "set_status_ad": "Mengubah status iklan",
    "create_negative_list": "Membuat daftar kata kunci negatif",
    "add_to_negative_list": "Menambah kata ke daftar negatif bersama",
    "remove_from_negative_list": "Menghapus kata dari daftar negatif bersama",
    "attach_negative_list": "Memasang daftar negatif ke kampanye",
    "detach_negative_list": "Melepas daftar negatif dari kampanye",
    "create_rsa": "Membuat iklan baru",
    "update_rsa": "Memperbarui teks iklan",
    "add_sitelink": "Menambah sitelink",
    "add_callout": "Menambah callout",
    "add_structured_snippet": "Menambah structured snippet",
    "add_call": "Menambah ekstensi telepon",
    "update_sitelink": "Memperbarui sitelink",
    "update_callout": "Memperbarui callout",
    "update_structured_snippet": "Memperbarui structured snippet",
    "update_call": "Memperbarui nomor telepon iklan",
    "set_extension_status": "Mengubah status ekstensi",
    "remove_extensions": "Melepas ekstensi",
    "update_campaign_settings": "Mengubah pengaturan kampanye",
    "add_locations": "Menambah target lokasi",
    "exclude_locations": "Mengecualikan lokasi",
    "add_proximity": "Menambah target radius",
    "remove_targeting": "Menghapus target",
    "set_ad_schedule": "Mengatur jadwal iklan",
    "set_device_bid_adjustments": "Mengatur bid per perangkat",
}


def describe_change(entry: dict[str, Any]) -> str:
    label = ACTION_LABEL.get(entry.get("aksi"), entry.get("aksi", "Perubahan"))
    target = entry.get("target") or {}
    name = target.get("kampanye") or target.get("nama") or target.get("campaign_id") or ""
    after = entry.get("sesudah") or {}
    detail = ""
    if entry.get("aksi") in ("add_negative_keywords", "add_to_negative_list"):
        detail = f"{len(after.get('ditambahkan', []))} kata"
    elif str(entry.get("aksi")).startswith("add_") and isinstance(after.get("ditambahkan"), list):
        detail = f"{len(after['ditambahkan'])} item"
    elif entry.get("aksi") == "attach_negative_list":
        detail = f"{len(after.get('ditambahkan', []))} kampanye"
    elif entry.get("aksi") == "update_budget":
        before = (entry.get("sebelum") or {}).get("budget_harian")
        detail = f"{rupiah(before)} → {rupiah(after.get('budget_harian'))}/hari"
    elif "status" in str(entry.get("aksi")):
        detail = str(after.get("status", "")).replace("PAUSED", "dijeda").replace("ENABLED", "diaktifkan")
    parts = [label, str(name), detail]
    return " — ".join(p for p in parts if p)


def recommendations(snapshot: dict[str, Any], client: Any) -> list[str]:
    recs: list[str] = []
    terms = snapshot.get("istilah_minggu_ini") or []
    sugg = suggest_negatives(terms, client.thresholds.min_waste_cost, client.protected_terms,
                             snapshot.get("negatif_terpasang", []))
    if sugg:
        saving = estimated_savings(terms, sugg)
        sample = ", ".join(f'"{s["text"]}"' for s in sugg[:4])
        recs.append(f"Blokir {len(sugg)} pencarian tidak relevan (mis. {sample}) — potensi hemat ±{rupiah(saving)}/minggu.")
    pacing = snapshot.get("pacing")
    if pacing and pacing["status"] != "SESUAI" and pacing.get("saran_budget_harian"):
        recs.append(f"Sesuaikan budget harian ke ±{rupiah(pacing['saran_budget_harian'])} agar pas dengan budget bulanan.")
    if snapshot.get("jumlah_konversi_utama", 0) > 3:
        recs.append("Rapikan konversi Utama menjadi 1–3 aksi lead asli agar data dan bidding akurat.")
    w = snapshot["minggu_ini"]
    if client.target_cpa and w.get("biaya_per_konversi") and w["biaya_per_konversi"] > client.target_cpa * 1.3:
        recs.append("Evaluasi kata kunci & iklan dengan biaya/konversi tertinggi untuk menekan CPA ke target.")
    return recs
