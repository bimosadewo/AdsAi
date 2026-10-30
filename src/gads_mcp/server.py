"""MCP server Google Ads untuk AdsAi."""

from __future__ import annotations

import functools
from datetime import timedelta
from typing import Any, Callable

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import (analysis, campaigns, dashboard, diagnostics, remote_auth, dossier, errors, extensions, ga4, gaql, gbp, goals, geo, meta, meta_launch, keyword_research, monitors, proposals, rsa, strategy,
               structure, targeting)
from .client import Ads, AdsError
from .clients import find_client, load_clients
from .collect import collect_snapshot, daily_rows, q_daily, today_local
from .config import load_settings
from .mutations import Mutator
from .safety import AuditLog, FailureLog
from .service import Context, alerts_message, build_dossier, build_weekly_report, send_message

INSTRUCTIONS = """\
Server ini membaca dan mengelola akun Google Ads klien agensi (uang sungguhan).
- Mulai dengan list_clients / client_health (atau list_accounts) untuk customer_id dan target klien.
- Semua nilai uang dalam mata uang akun (mis. 50000 = Rp50.000), bukan micros.
- Cek conversion_actions sebelum menilai CPA: konversi Utama > 3 atau aksi ringan sebagai Utama
  berarti angka konversi belum bisa dipercaya.
- Tools yang MENGUBAH akun selalu dua langkah: confirm=false (pratinjau tervalidasi Google, tidak
  mengubah apa pun) -> tunjukkan rencana ke pengguna -> confirm=true hanya setelah pengguna jelas
  setuju. Setelah itu laporkan isi 'terverifikasi' (kondisi akun yang dibaca ulang).
- send_to_client mengirim pesan ke klien: pratinjau dulu, kirim hanya setelah disetujui.
- Mulai sesi per klien dengan suggest_next_action dan baca client_strategy: arahan AVOID/PREFER/
  CONSTRAINT/REQUIRE wajib dihormati; simpan keputusan baru (add_strategy_directive) hanya bila pengguna
  memutuskannya secara eksplisit.
- "Tampilkan performa/hasil iklan" → performance_dashboard (kartu interaktif); lalu cukup kesimpulan singkat.
- Jangan mengarang angka; bila tool gagal, baca 'Saran' di pesannya atau panggil why_did_this_fail —
  jangan mengulang dengan argumen tebakan. Setelah timeout, baca kondisi terkini dulu.
- "Jeda/hentikan" = set_status PAUSED, bukan remove_*. Menghapus butuh confirm_delete=true.
- Untuk kebutuhan yang tidak tercakup tool lain, pakai run_query dengan GAQL.
- "Pindai akun" / laporan pagi → watch_scan. Alarm: create_monitor (multi-syarat, pratinjau dulu), test_monitor.
- Google Business Profile (profil Maps): gbp_status → gbp_locations (audit) / gbp_reviews; perubahan
  (gbp_post, gbp_reply_review, gbp_special_hours) juga pratinjau dulu. Draf balasan ulasan ditulis Claude.
- Website/Google Analytics 4 (baca saja): ga4_overview (pengunjung, saluran, halaman masuk, klik WA);
  ga4_report untuk pertanyaan lain. Bandingkan key event GA4 dengan konversi Google Ads bila relevan.
- Meta Ads / Facebook / Instagram: meta_status → meta_performance (kampanye, hasil WA/prospek, penempatan);
  meta_insights untuk rincian; meta_ads untuk iklan + teks + biaya per hasil (cari iklan terbukti).
  "Beriklan lagi pakai yang terbukti" → meta_create_campaign_from_proven (pratinjau, selalu DIJEDA), lalu
  meta_set_status ACTIVE hanya setelah pengguna setuju.
- Saat pengguna tidak di tempat (mis. tugas terjadwal), JANGAN mengubah akun: masukkan usulan lewat
  propose_change. Pengguna memutuskan dengan list_proposals → decide_proposal (setujui/tolak, confirm=true).
"""

# ---------------------------------------------------------------------------
# Kartu dashboard interaktif (MCP Apps). Harus terdaftar sebelum MCPServer dibuat.
# ---------------------------------------------------------------------------

apps = Apps()
apps.add_html_resource(dashboard.RESOURCE_URI, dashboard.html(), name="AdsAi dashboard",
                       title="Dashboard performa Google Ads", prefers_border=True)


@apps.tool(resource_uri=dashboard.RESOURCE_URI, visibility=["model", "app"],
           meta={"ui/resourceUri": dashboard.RESOURCE_URI},  # kunci lama, untuk host MCP Apps versi awal
           annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
def performance_dashboard(customer_id: str | None = None, client: str | None = None, days: int = 7,
                          start: str | None = None, end: str | None = None) -> dict:
    """Dashboard performa Google Ads yang tampil sebagai KARTU INTERAKTIF di Claude: kotak angka (biaya, klik,
    konversi, biaya/konversi, tayangan, CTR, CPC) dengan perbandingan periode setara, grafik harian biaya vs
    konversi, tabel per kampanye/grup iklan/kata kunci, dan temuan dengan tombol aksi. Tab rentang 7/14/30/60/90 hari
    bisa diklik di kartu. Default: 7 hari sampai kemarin (hari ini belum lengkap); start/end (YYYY-MM-DD) untuk
    rentang khusus, mis. kemarin saja: start=end=tanggal kemarin. Data langsung dari Google Ads, bukan cache.
    Pakai tool ini untuk "tampilkan performa iklan"; setelah kartu tampil cukup tulis 2–4 kalimat kesimpulan."""
    try:
        a = ads()
        cl = None
        if client:
            cl = find_client(load_clients(a.settings.clients_file), client)
            cid = cl.customer_id
        else:
            cid = a.customer_id(customer_id)
            try:
                cl = next((c for c in load_clients(a.settings.clients_file).values() if c.customer_id == cid), None)
            except Exception:  # noqa: BLE001 — clients.yaml opsional
                cl = None
        d = dashboard.fetch(a, cid, days, start, end, client=cl)
        return {**d, "ringkasan": dashboard.text_summary(d)}
    except (AdsError, ValueError, RuntimeError) as ex:
        _record_failure("performance_dashboard", {"customer_id": customer_id, "client": client}, ex)
        raise ToolError(str(ex)) from ex


_remote = remote_auth.from_env()   # None = mode lokal (stdio, Claude Desktop)
mcp = MCPServer("google-ads", instructions=INSTRUCTIONS, extensions=[apps],
                **({"auth_server_provider": _remote[0], "auth": _remote[1]} if _remote else {}))
if _remote:
    @mcp.custom_route(remote_auth.CALLBACK_PATH, methods=["GET"], include_in_schema=False)
    async def _google_callback(request):
        return await _remote[0].handle_callback(request)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def _health(request):
        from starlette.responses import PlainTextResponse
        return PlainTextResponse(f"ok {code_version()}")
def code_version() -> str:
    """Sidik kode yang sedang berjalan (hash semua berkas paket) — cocokkan dengan lokal untuk memastikan deploy."""
    import hashlib
    from pathlib import Path
    h = hashlib.sha256()
    root = Path(__file__).parent
    for f in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".txt")):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

_ads: Ads | None = None


def _register(fn: Callable, annotations: ToolAnnotations) -> Callable:
    """Daftarkan tool dan ubah error yang bisa dipahami menjadi pesan untuk Claude."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (AdsError, ValueError, RuntimeError) as ex:
            _record_failure(fn.__name__, kwargs, ex)
            raise ToolError(str(ex)) from ex

    return mcp.tool(annotations=annotations)(wrapper)


def _failures() -> FailureLog:
    return FailureLog(ads().settings.safety.audit_log_dir)


def _record_failure(name: str, kwargs: dict, ex: Exception) -> None:
    try:
        _failures().write(name, kwargs, str(ex), getattr(ex, "codes", None))
    except Exception:  # noqa: BLE001 — mis. .env belum ada; error asli tetap dilaporkan
        pass


def tool(fn: Callable) -> Callable:
    return _register(fn, READ_ONLY)


CONFIRM_DOC = """

confirm=false (default): pratinjau + validasi Google, TIDAK mengubah apa pun.
confirm=true: jalankan sungguhan dan catat di log audit. Hanya setelah pengguna setuju."""


WRITE_TOOLS: dict[str, Callable] = {}


def write_tool(fn: Callable) -> Callable:
    fn.__doc__ = (fn.__doc__ or "").rstrip() + CONFIRM_DOC
    WRITE_TOOLS[fn.__name__] = fn
    return _register(fn, WRITE)


def ads() -> Ads:
    global _ads
    if _ads is None:
        _ads = Ads(load_settings())
    return _ads


def mutator() -> Mutator:
    a = ads()
    return Mutator(a, AuditLog(a.settings.safety.audit_log_dir))


def _run(customer_id: str | None, query: str) -> tuple[str, list[dict[str, Any]]]:
    a = ads()
    cid = a.customer_id(customer_id)
    return cid, a.search(cid, query)


@tool
def list_accounts() -> dict:
    """Daftar akun klien di bawah akun Manager (MCC), atau akun yang bisa diakses bila tanpa MCC."""
    a = ads()
    login = a.settings.login_customer_id
    if login:
        rows = a.search(login, gaql.q_accounts())
        return {"manager_id": login, "akun": rows}
    return {"akun_yang_bisa_diakses": a.accessible_customers()}


@tool
def list_campaigns(customer_id: str | None = None, include_removed: bool = False) -> dict:
    """Daftar kampanye beserta status, alasan status (primary_status_reasons), jenis, bidding, dan budget harian."""
    cid, rows = _run(customer_id, gaql.q_campaigns(include_removed))
    return {"customer_id": cid, "jumlah": len(rows), "kampanye": rows}


@tool
def campaign_performance(
    customer_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    start: str | None = None,
    end: str | None = None,
    only_with_impressions: bool = True,
) -> dict:
    """Performa per kampanye: biaya, klik, CTR, CPC, konversi, biaya/konversi, dan pangsa tayangan.

    date_range: LAST_7_DAYS, LAST_30_DAYS, THIS_MONTH, LAST_MONTH, dll. Atau isi start & end (YYYY-MM-DD).
    """
    cid, rows = _run(customer_id, gaql.q_campaign_performance(gaql.date_clause(date_range, start, end), only_with_impressions))
    return {"customer_id": cid, "ringkasan": gaql.summarize(rows), "kampanye": rows}


@tool
def search_terms(
    customer_id: str | None = None,
    campaign_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    start: str | None = None,
    end: str | None = None,
    min_cost: float = 0,
    limit: int = 300,
) -> dict:
    """Istilah penelusuran yang memicu iklan, diurutkan dari biaya terbesar. min_cost dalam mata uang akun."""
    q = gaql.q_search_terms(gaql.date_clause(date_range, start, end), campaign_id, int(min_cost * 1_000_000))
    cid, rows = _run(customer_id, gaql.ensure_read_only(f"{q} LIMIT {min(limit, gaql.MAX_ROWS)}"))
    zero = [r for r in rows if not r.get("metrics.conversions")]
    return {
        "customer_id": cid,
        "ringkasan": gaql.summarize(rows),
        "biaya_tanpa_konversi": round(sum(float(r.get("metrics.cost") or 0) for r in zero), 2),
        "istilah": rows,
    }


@tool
def keywords(
    customer_id: str | None = None,
    campaign_id: str | None = None,
    date_range: str = "LAST_30_DAYS",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Kata kunci beserta match type, Quality Score (dan komponennya), CPC, dan konversi."""
    cid, rows = _run(customer_id, gaql.q_keywords(gaql.date_clause(date_range, start, end), campaign_id))
    return {"customer_id": cid, "ringkasan": gaql.summarize(rows), "kata_kunci": rows}


@tool
def conversion_actions(customer_id: str | None = None) -> dict:
    """Semua conversion action: Utama/Sekunder, kategori, cara hitung, jangka waktu, dan nilai default."""
    cid, rows = _run(customer_id, gaql.q_conversion_actions())
    primary = [r for r in rows if r.get("conversion_action.primary_for_goal")]
    return {
        "customer_id": cid,
        "jumlah": len(rows),
        "jumlah_utama": len(primary),
        "peringatan": (
            f"Ada {len(primary)} konversi Utama. Idealnya 1-3 aksi yang benar-benar lead/penjualan."
            if len(primary) > 3
            else None
        ),
        "konversi": rows,
    }


@tool
def account_alerts(customer_id: str | None = None) -> dict:
    """Cek kesehatan akun: kampanye aktif tanpa tayangan 7 hari, konversi Utama berlebihan, dan impor konversi offline yang gagal."""
    a = ads()
    cid = a.customer_id(customer_id)
    alerts: list[str] = []

    rows = a.search(cid, gaql.q_enabled_campaign_impressions())
    impressions: dict[str, int] = {}
    for r in rows:
        key = r["campaign.id"]
        impressions[key] = impressions.get(key, 0) + int(r.get("metrics.impressions") or 0)
    enabled = a.search(cid, "SELECT campaign.id, campaign.name, campaign.primary_status, campaign.primary_status_reasons "
                            "FROM campaign WHERE campaign.status = 'ENABLED'")
    for c in enabled:
        if impressions.get(c["campaign.id"], 0) == 0:
            alerts.append(
                f"Kampanye aktif '{c['campaign.name']}' tidak tayang 7 hari terakhir "
                f"(status: {c.get('campaign.primary_status')}, alasan: {c.get('campaign.primary_status_reasons')}). "
                "Cek saldo/pembayaran dan status iklan."
            )

    conv = a.search(cid, gaql.q_conversion_actions())
    n_primary = sum(1 for r in conv if r.get("conversion_action.primary_for_goal"))
    if n_primary > 3:
        alerts.append(f"{n_primary} konversi berstatus Utama — angka konversi kemungkinan menggelembung.")

    try:
        uploads = a.search(cid, gaql.q_offline_upload_summary())
    except AdsError:
        uploads = []
    for u in uploads:
        rate = u.get("offline_conversion_upload_client_summary.success_rate")
        if rate is not None and float(rate) < 0.9:
            alerts.append(
                f"Impor konversi offline ({u.get('offline_conversion_upload_client_summary.client')}) "
                f"tingkat sukses hanya {float(rate):.0%}."
            )

    return {"customer_id": cid, "jumlah_peringatan": len(alerts), "peringatan": alerts}


@tool
def run_query(query: str, customer_id: str | None = None) -> dict:
    """Jalankan query GAQL bebas (hanya SELECT, maks 1000 baris).

    Contoh: SELECT ad_group.name, metrics.clicks FROM ad_group WHERE segments.date DURING LAST_7_DAYS
    Referensi field: https://developers.google.com/google-ads/api/fields
    """
    cid, rows = _run(customer_id, gaql.ensure_read_only(query))
    return {"customer_id": cid, "jumlah": len(rows), "baris": rows}


# ---------------------------------------------------------------------------
# Fase 2 — tools yang mengubah akun (selalu pratinjau dulu)
# ---------------------------------------------------------------------------

@write_tool
def add_negative_keywords(campaign_id: str, keywords: list[dict], customer_id: str | None = None,
                          confirm: bool = False) -> dict:
    """Tambah kata kunci negatif di level kampanye. keywords: [{"text": "...", "match_type": "PHRASE|EXACT|BROAD"}].
    Kata kunci yang sudah terpasang otomatis dilewati."""
    a = ads()
    return mutator().add_negative_keywords(a.customer_id(customer_id), campaign_id, keywords, confirm)


@write_tool
def remove_negative_keywords(campaign_id: str, texts: list[str], customer_id: str | None = None,
                             confirm: bool = False) -> dict:
    """Hapus kata kunci negatif kampanye berdasarkan teksnya (semua match type yang cocok)."""
    a = ads()
    return mutator().remove_negative_keywords(a.customer_id(customer_id), campaign_id, texts, confirm)


@write_tool
def update_budget(campaign_id: str, daily_budget: float, customer_id: str | None = None,
                  confirm: bool = False, allow_shared: bool = False) -> dict:
    """Ubah budget harian kampanye (mata uang akun, mis. 50000 = Rp50.000).
    Dibatasi ADSAI_MAX_DAILY_BUDGET dan ADSAI_MAX_BUDGET_INCREASE_FACTOR."""
    a = ads()
    return mutator().update_budget(a.customer_id(customer_id), campaign_id, daily_budget, confirm, allow_shared)


@write_tool
def set_status(level: str, ids: list[str], status: str, customer_id: str | None = None,
               confirm: bool = False) -> dict:
    """Jeda/aktifkan. level: campaign | ad_group | keyword | ad. status: ENABLED | PAUSED.
    keyword: id 'ad_group_id~criterion_id'; ad: id 'ad_group_id~ad_id' (lihat campaign_structure).
    Untuk menghapus kata kunci permanen pakai remove_keywords."""
    a = ads()
    return mutator().set_status(a.customer_id(customer_id), level, ids, status, confirm)


@write_tool
def update_conversion_action(conversion_action_id: str, customer_id: str | None = None, confirm: bool = False,
                             primary_for_goal: bool | None = None, counting_type: str | None = None,
                             click_through_days: int | None = None, view_through_days: int | None = None,
                             status: str | None = None) -> dict:
    """Ubah conversion action: Utama/Sekunder (primary_for_goal), counting_type (ONE_PER_CLICK/MANY_PER_CLICK),
    jangka waktu klik (1-90) / tayang (1-30) hari, status (ENABLED/HIDDEN)."""
    a = ads()
    return mutator().update_conversion_action(
        a.customer_id(customer_id), conversion_action_id, confirm, primary_for_goal, counting_type,
        click_through_days, view_through_days, status)


@write_tool
def update_bid_strategy(campaign_id: str, strategy: str, customer_id: str | None = None, confirm: bool = False,
                        target_cpa: float | None = None, max_cpc: float | None = None) -> dict:
    """Ganti strategi bidding: MAXIMIZE_CONVERSIONS (opsional target_cpa), MAXIMIZE_CLICKS (opsional max_cpc),
    atau MANUAL_CPC."""
    a = ads()
    return mutator().update_bid_strategy(a.customer_id(customer_id), campaign_id, strategy, confirm, target_cpa, max_cpc)


@write_tool
def add_keywords(ad_group_id: str, keywords: list[dict], customer_id: str | None = None, confirm: bool = False,
                 cpc_bid: float | None = None, paused: bool = False) -> dict:
    """Tambah kata kunci ke grup iklan. keywords: [{"text": "...", "match_type": "EXACT|PHRASE|BROAD"}]."""
    a = ads()
    return mutator().add_keywords(a.customer_id(customer_id), ad_group_id, keywords, confirm, cpc_bid, paused)


@tool
def change_log(customer_id: str | None = None, limit: int = 50) -> dict:
    """Riwayat perubahan yang dilakukan lewat AdsAi untuk satu akun (terbaru dulu)."""
    a = ads()
    cid = a.customer_id(customer_id)
    return {"customer_id": cid, "perubahan": AuditLog(a.settings.safety.audit_log_dir).read(cid, limit)}


# ---------------------------------------------------------------------------
# Struktur kampanye & iklan
# ---------------------------------------------------------------------------

@tool
def campaign_structure(campaign_id: str, customer_id: str | None = None, page: int = 1, page_size: int = 10,
                       include: str = "all", ad_group_id: str | None = None) -> dict:
    """Struktur satu kampanye: grup iklan, kata kunci (id 'ad_group_id~criterion_id', match type, QS),
    iklan RSA (headline, deskripsi, ad strength, status persetujuan), ekstensi, negatif kampanye,
    shared negative list — plus 'temuan' audit struktur otomatis.
    Dibagi per halaman grup iklan: bila halaman.has_more=true, panggil lagi dengan page+1.
    include: all | summary | keywords | ads. ad_group_id: lihat satu grup iklan saja."""
    a = ads()
    cid = a.customer_id(customer_id)
    return {"customer_id": cid, **structure.fetch_structure(a, cid, campaign_id, page=page, page_size=page_size,
                                                            include=include, ad_group_id=ad_group_id)}


@tool
def ad_performance(customer_id: str | None = None, campaign_id: str | None = None, ad_group_id: str | None = None,
                   date_range: str = "LAST_30_DAYS", start: str | None = None, end: str | None = None,
                   limit: int = 100) -> dict:
    """Performa per iklan (biaya, klik, CTR, konversi, CPA, ad strength) — untuk menentukan iklan pemenang
    dan yang perlu dijeda dalam satu grup iklan."""
    a = ads()
    cid = a.customer_id(customer_id)
    q = structure.q_ad_performance(gaql.date_clause(date_range, start, end), campaign_id, ad_group_id)
    rows = a.search(cid, f"{q} LIMIT {max(1, min(limit, 500))}")
    return {"customer_id": cid, "ringkasan": gaql.summarize(rows), "iklan": rows}


@tool
def validate_ad_copy(headlines: list, descriptions: list, path1: str | None = None, path2: str | None = None,
                     final_urls: list[str] | None = None) -> dict:
    """Periksa teks RSA tanpa mengirim apa pun ke Google: jumlah & panjang (headline 3-15 ≤30, deskripsi 2-4 ≤90,
    path ≤15), tanda seru, huruf kapital berlebihan, duplikat, pin, klaim superlatif. Item boleh string atau
    {"text": "...", "pin": 1}. Pakai saat menyusun iklan sebelum create/update."""
    copy = rsa.validate(headlines, descriptions, path1, path2)
    out = copy.to_dict()
    url_errors = rsa.validate_urls(final_urls)
    out["error"] += url_errors
    out["valid"] = out["valid"] and not url_errors
    out["panjang"] = {"headlines": [len(h["text"]) for h in copy.headlines],
                      "descriptions": [len(d["text"]) for d in copy.descriptions]}
    return out


@write_tool
def create_responsive_search_ad(ad_group_id: str, headlines: list, descriptions: list, final_urls: list[str],
                                customer_id: str | None = None, confirm: bool = False, path1: str | None = None,
                                path2: str | None = None, paused: bool = True) -> dict:
    """Buat Responsive Search Ad di grup iklan. headlines 3-15 (≤30 karakter), descriptions 2-4 (≤90),
    item boleh {"text": "...", "pin": 1}. Default dibuat DIJEDA (paused=true); aktifkan lewat set_status level='ad'."""
    a = ads()
    return mutator().create_rsa(a.customer_id(customer_id), ad_group_id, headlines, descriptions, final_urls,
                                confirm, path1, path2, paused)


@write_tool
def update_responsive_search_ad(ad_group_id: str, ad_id: str, customer_id: str | None = None, confirm: bool = False,
                                headlines: list | None = None, descriptions: list | None = None,
                                final_urls: list[str] | None = None, path1: str | None = None,
                                path2: str | None = None) -> dict:
    """Ubah RSA yang ada (ID dari campaign_structure). Bagian yang dikirim MENGGANTI seluruh daftar lama
    (mis. headlines = daftar lengkap baru). Iklan akan ditinjau ulang Google."""
    a = ads()
    return mutator().update_rsa(a.customer_id(customer_id), ad_group_id, ad_id, confirm, headlines, descriptions,
                                final_urls, path1, path2)


# ---------------------------------------------------------------------------
# Pengaturan kampanye & targeting
# ---------------------------------------------------------------------------

def _tg() -> targeting.Targeting:
    return targeting.Targeting(mutator())


@tool
def campaign_settings(campaign_id: str, customer_id: str | None = None) -> dict:
    """Pengaturan & targeting kampanye: jaringan (Search Partners, perluasan Display), AI Max, mode lokasi,
    lokasi (termasuk pengecualian), radius, jadwal iklan, penyesuaian perangkat, bahasa — plus 'temuan' audit
    (mis. Search Partners/Display aktif, tanpa target lokasi, mode 'kehadiran atau minat', penyesuaian yang
    diabaikan Smart Bidding). criterion_id dipakai untuk remove_targeting."""
    a = ads()
    cid = a.customer_id(customer_id)
    return {"customer_id": cid, **targeting.fetch_settings(a, cid, campaign_id)}


@tool
def resolve_locations(queries: list[str], customer_id: str | None = None, country_code: str | None = "ID",
                      locale: str = "id") -> dict:
    """Cari ID lokasi Google (geo_id) dari nama: kota, provinsi, kabupaten, kode pos. Mis. ["Surabaya",
    "Jawa Timur", "Sidoarjo"]. country_code=null untuk mencari di seluruh dunia. Pilih geo_id yang tepat
    (perhatikan 'nama' lengkap & 'jenis') lalu pakai di add_location_targets."""
    return {"hasil": _tg().resolve_locations(queries, country_code, locale)}


@write_tool
def update_campaign_settings(campaign_id: str, customer_id: str | None = None, confirm: bool = False,
                             search_partners: bool | None = None, display_expansion: bool | None = None,
                             ai_max: bool | None = None, location_mode: str | None = None) -> dict:
    """Ubah pengaturan kampanye: search_partners (Google Search Partners), display_expansion (perluasan
    Display untuk Search), ai_max, location_mode (PRESENCE = orang di lokasi; PRESENCE_OR_INTEREST = juga yang
    tertarik; SEARCH_INTEREST). Hanya field yang dikirim yang berubah."""
    a = ads()
    return _tg().update_settings(a.customer_id(customer_id), campaign_id, confirm, search_partners,
                                 display_expansion, ai_max, location_mode)


@write_tool
def add_location_targets(campaign_id: str, geo_ids: list[str], customer_id: str | None = None, confirm: bool = False,
                         exclude: bool = False, bid_pct: float | None = None) -> dict:
    """Targetkan (atau exclude=true: kecualikan) lokasi berdasarkan geo_id dari resolve_locations.
    bid_pct: penyesuaian bid lokasi, mis. 20 = +20% (diabaikan Smart Bidding)."""
    a = ads()
    return _tg().add_locations(a.customer_id(customer_id), campaign_id, geo_ids, confirm, exclude, bid_pct)


@write_tool
def add_radius_target(campaign_id: str, radius_km: float, customer_id: str | None = None, confirm: bool = False,
                      latitude: float | None = None, longitude: float | None = None,
                      street_address: str | None = None, city_name: str | None = None,
                      province_name: str | None = None, postal_code: str | None = None,
                      bid_pct: float | None = None) -> dict:
    """Targetkan radius (1-800 km) di sekitar titik (latitude/longitude) atau alamat (mis. gudang/toko klien)."""
    a = ads()
    return _tg().add_proximity(a.customer_id(customer_id), campaign_id, radius_km, confirm, latitude, longitude,
                               street_address, city_name, province_name, postal_code, "ID", bid_pct)


@write_tool
def remove_targeting(campaign_id: str, criterion_ids: list[str], customer_id: str | None = None,
                     confirm: bool = False, allow_no_location: bool = False) -> dict:
    """Hapus target lokasi, radius, atau jendela jadwal (criterion_id dari campaign_settings). Menghapus semua
    target lokasi (iklan jadi tayang ke seluruh dunia) ditolak kecuali allow_no_location=true."""
    a = ads()
    return _tg().remove_criteria(a.customer_id(customer_id), campaign_id, criterion_ids, confirm, allow_no_location)


@write_tool
def set_ad_schedule(campaign_id: str, windows: list[dict], customer_id: str | None = None,
                    confirm: bool = False) -> dict:
    """GANTI seluruh jadwal iklan (atomik). windows: [{"day": "hari_kerja", "start": "08:00", "end": "17:00",
    "bid_pct": 0}]. day: Senin..Minggu, hari_kerja, akhir_pekan, setiap_hari. Menit 00/15/30/45; tidak bisa
    melewati tengah malam. Iklan HANYA tayang di jendela ini (zona waktu akun)."""
    a = ads()
    return _tg().set_schedule(a.customer_id(customer_id), campaign_id, windows, confirm)


@write_tool
def set_device_bid_adjustments(adjustments: dict, customer_id: str | None = None, confirm: bool = False,
                               campaign_id: str | None = None, ad_group_id: str | None = None) -> dict:
    """Penyesuaian bid per perangkat dalam persen: {"mobile": 20, "desktop": -30, "tablet": -100}
    (-100 = matikan). Isi campaign_id ATAU ad_group_id. Smart Bidding mengabaikan nilai selain -100."""
    a = ads()
    return _tg().set_devices(a.customer_id(customer_id), confirm, adjustments, campaign_id, ad_group_id)


# ---------------------------------------------------------------------------
# Ekstensi (sitelink, callout, structured snippet, telepon)
# ---------------------------------------------------------------------------

def _ext() -> extensions.Extensions:
    return extensions.Extensions(mutator())


def _raise_if(errors: list[str]) -> None:
    if errors:
        raise ValueError("Ekstensi belum valid:\n- " + "\n- ".join(errors))


@tool
def list_extensions(campaign_id: str, customer_id: str | None = None) -> dict:
    """Ekstensi yang tertaut ke kampanye (level kampanye & grup iklan): sitelink, callout, structured snippet,
    telepon — dengan asset_id dan status. Pakai sebelum menambah (hindari duplikat), mengubah, atau melepas."""
    a = ads()
    cid = a.customer_id(customer_id)
    items = _ext().list(cid, campaign_id)
    count = {k: sum(1 for i in items if i["jenis"] == k and i["status"] == "ENABLED") for k in extensions.TYPES}
    return {"customer_id": cid, "jumlah_aktif": count, "ekstensi": items}


@write_tool
def add_sitelinks(campaign_id: str, sitelinks: list[dict], customer_id: str | None = None, confirm: bool = False,
                  ad_group_id: str | None = None) -> dict:
    """Tambah sitelink. sitelinks: [{"link_text" (≤25), "final_url", "description1" (≤35), "description2" (≤35)}];
    deskripsi opsional tapi harus berpasangan. Arahkan tiap sitelink ke halaman berbeda (Harga, Penawaran,
    Produk, Kontak). ad_group_id: pasang khusus di satu grup iklan."""
    a = ads()
    specs, errors, warnings = extensions.validate_sitelinks(sitelinks)
    _raise_if(errors)
    return _ext().add(a.customer_id(customer_id), campaign_id, "SITELINK", specs, confirm, ad_group_id, warnings)


@write_tool
def add_callouts(campaign_id: str, callouts: list[str], customer_id: str | None = None, confirm: bool = False,
                 ad_group_id: str | None = None) -> dict:
    """Tambah callout (≤25 karakter, 4-8 per kampanye): fakta/keunggulan seperti 'Stok Ready', 'Harga Pabrik',
    'Kirim Seluruh Indonesia'. Bukan ajakan, tanpa titik di akhir, tanpa tanda seru."""
    a = ads()
    texts, errors, warnings = extensions.validate_callouts(callouts)
    _raise_if(errors)
    return _ext().add(a.customer_id(customer_id), campaign_id, "CALLOUT", [{"text": t} for t in texts], confirm,
                      ad_group_id, warnings)


@write_tool
def add_structured_snippets(campaign_id: str, snippets: list[dict], customer_id: str | None = None,
                            confirm: bool = False, ad_group_id: str | None = None) -> dict:
    """Tambah structured snippet: [{"header": "Jenis", "values": ["Woven", "Non Woven", "HDPE"]}].
    Header resmi Google (boleh alias Indonesia): Jenis/Types, Merek/Brands, Layanan/Service catalog, Model/Models,
    Gaya/Styles, Kursus/Courses, Destinasi/Destinations, Fasilitas/Amenities, dll. 3-10 nilai, masing-masing ≤25."""
    a = ads()
    specs, errors, warnings = extensions.validate_snippets(snippets)
    _raise_if(errors)
    return _ext().add(a.customer_id(customer_id), campaign_id, "STRUCTURED_SNIPPET", specs, confirm,
                      ad_group_id, warnings)


@write_tool
def add_call_extension(campaign_id: str, phone_number: str, customer_id: str | None = None, confirm: bool = False,
                       country_code: str = "ID", conversion_action_id: str | None = None) -> dict:
    """Tambah ekstensi telepon (tombol telepon di HP). Nomor Indonesia boleh '0812...', '+62 812...', '021 ...'.
    Nomor yang sudah terpasang tidak ditambah lagi. conversion_action_id: hitung panggilan sebagai konversi."""
    a = ads()
    spec, warnings = extensions.build_spec("CALL", {"phone_number": phone_number, "country_code": country_code,
                                                    "conversion_action_id": conversion_action_id})
    return _ext().add(a.customer_id(customer_id), campaign_id, "CALL", [spec], confirm, None, warnings)


@write_tool
def update_extension(campaign_id: str, asset_id: str, customer_id: str | None = None, confirm: bool = False,
                     link_text: str | None = None, final_url: str | None = None, description1: str | None = None,
                     description2: str | None = None, text: str | None = None, header: str | None = None,
                     values: list[str] | None = None, phone_number: str | None = None, country_code: str = "ID",
                     conversion_action_id: str | None = None) -> dict:
    """Ubah satu ekstensi (asset_id dari list_extensions). Jenis diambil dari ekstensi lama; kirim field LENGKAP
    versi baru: sitelink → link_text, final_url, description1/2; callout → text; snippet → header, values;
    telepon → phone_number. Dijalankan atomik: aset baru dibuat & ditautkan, tautan lama dilepas."""
    a = ads()
    cid = a.customer_id(customer_id)
    e = _ext()
    (old,) = e._find(cid, extensions._digits(campaign_id, "campaign_id"), [asset_id])
    spec, warnings = extensions.build_spec(old["jenis"], {
        "link_text": link_text, "final_url": final_url, "description1": description1, "description2": description2,
        "text": text, "header": header, "values": values, "phone_number": phone_number, "country_code": country_code,
        "conversion_action_id": conversion_action_id})
    return e.replace(cid, campaign_id, asset_id, spec, confirm, warnings)


@write_tool
def set_extension_status(campaign_id: str, asset_ids: list[str], status: str, customer_id: str | None = None,
                         confirm: bool = False) -> dict:
    """Jeda (PAUSED) atau aktifkan (ENABLED) ekstensi tanpa melepasnya — mis. nomor telepon saat kantor libur."""
    a = ads()
    return _ext().set_status(a.customer_id(customer_id), campaign_id, asset_ids, status, confirm)


@write_tool
def remove_extensions(campaign_id: str, asset_ids: list[str], customer_id: str | None = None,
                      confirm: bool = False) -> dict:
    """Lepas ekstensi dari kampanye/grup iklan (asset_ids wajib, dari list_extensions). Aset tetap ada di
    library akun. Melepas semua sekaligus tanpa daftar ID sengaja tidak didukung."""
    a = ads()
    return _ext().remove(a.customer_id(customer_id), campaign_id, asset_ids, confirm)


# ---------------------------------------------------------------------------
# Shared negative keyword list
# ---------------------------------------------------------------------------

@tool
def list_negative_keyword_lists(customer_id: str | None = None) -> dict:
    """Daftar shared negative keyword list akun: jumlah kata kunci dan jumlah kampanye yang memakainya."""
    a = ads()
    cid = a.customer_id(customer_id)
    rows = a.search(cid, "SELECT shared_set.id, shared_set.name, shared_set.member_count, shared_set.reference_count, "
                         "shared_set.status FROM shared_set WHERE shared_set.type = 'NEGATIVE_KEYWORDS' "
                         "AND shared_set.status = 'ENABLED'")
    return {"customer_id": cid, "list": [
        {"id": r.get("shared_set.id"), "nama": r.get("shared_set.name"), "jumlah_kata_kunci": r.get("shared_set.member_count"),
         "dipakai_kampanye": r.get("shared_set.reference_count")} for r in rows]}


@tool
def get_negative_keyword_list(shared_set_id: str, customer_id: str | None = None, query: str | None = None) -> dict:
    """Isi satu shared negative keyword list (teks + match type) dan kampanye yang memakainya.
    query: saring teks kata kunci."""
    a = ads()
    cid = a.customer_id(customer_id)
    m = mutator()
    items = [{"text": r.get("shared_criterion.keyword.text"), "match_type": r.get("shared_criterion.keyword.match_type")}
             for r in m.list_items(cid, shared_set_id)]
    if query:
        items = [i for i in items if query.lower() in (i["text"] or "").lower()]
    return {"customer_id": cid, "shared_set_id": shared_set_id, "jumlah": len(items),
            "kata_kunci": sorted(items, key=lambda i: i["text"] or ""),
            "kampanye": m._attached(cid, str(int(shared_set_id)))}


@write_tool
def create_negative_keyword_list(name: str, customer_id: str | None = None, confirm: bool = False) -> dict:
    """Buat shared negative keyword list kosong (mis. 'Kompetitor', 'Informasional'). Isi dengan
    add_negative_keywords_to_list, pasang dengan attach_negative_keyword_list."""
    a = ads()
    return mutator().create_negative_list(a.customer_id(customer_id), name, confirm)


@write_tool
def add_negative_keywords_to_list(shared_set_id: str, keywords: list[dict], customer_id: str | None = None,
                                  confirm: bool = False) -> dict:
    """Tambah kata kunci negatif ke shared list — berlaku di semua kampanye yang memakai list itu.
    keywords: [{"text": "...", "match_type": "PHRASE|EXACT|BROAD"}]. Duplikat dilewati."""
    a = ads()
    return mutator().add_to_negative_list(a.customer_id(customer_id), shared_set_id, keywords, confirm)


@write_tool
def remove_negative_keywords_from_list(shared_set_id: str, texts: list[str], customer_id: str | None = None,
                                       confirm: bool = False) -> dict:
    """Hapus kata kunci dari shared negative list berdasarkan teksnya."""
    a = ads()
    return mutator().remove_from_negative_list(a.customer_id(customer_id), shared_set_id, texts, confirm)


@write_tool
def attach_negative_keyword_list(shared_set_id: str, campaign_ids: list[str], customer_id: str | None = None,
                                 confirm: bool = False) -> dict:
    """Pasang shared negative list ke satu atau beberapa kampanye (yang sudah terpasang dilewati)."""
    a = ads()
    return mutator().attach_negative_list(a.customer_id(customer_id), shared_set_id, campaign_ids, confirm)


@write_tool
def detach_negative_keyword_list(shared_set_id: str, campaign_ids: list[str], customer_id: str | None = None,
                                 confirm: bool = False) -> dict:
    """Lepas shared negative list dari kampanye (list dan isinya tetap ada)."""
    a = ads()
    return mutator().detach_negative_list(a.customer_id(customer_id), shared_set_id, campaign_ids, confirm)


# ---------------------------------------------------------------------------
# Fase 3 — klien, kesehatan akun, laporan, pengiriman
# ---------------------------------------------------------------------------

def ctx() -> Context:
    a = ads()
    return Context(a.settings, a, load_clients(a.settings.clients_file), AuditLog(a.settings.safety.audit_log_dir))


@tool
def list_clients() -> dict:
    """Daftar klien agensi dari clients.yaml (nama, customer ID, kontak, target CPA, budget bulanan)."""
    a = ads()
    return {"klien": [c.to_dict() for c in load_clients(a.settings.clients_file).values()]}


@tool
def client_health(client: str | None = None) -> dict:
    """Cek kesehatan akun satu klien (key/nama/customer ID) atau semua klien aktif: tidak tayang, konversi nol,
    anomali harian, CPA naik, pacing budget, konversi Utama berlebih, impor offline gagal."""
    c = ctx()
    out = []
    for cl in c.active_clients(client):
        try:
            snap = collect_snapshot(c.ads, cl, with_search_terms=False)
            alerts = analysis.evaluate_alerts(snap, cl)
            out.append({"klien": cl.name, "customer_id": cl.customer_id,
                        "alert": [x.to_dict() for x in alerts],
                        "minggu_ini": snap["minggu_ini"], "minggu_lalu": snap["minggu_lalu"],
                        "pacing": snap["pacing"], "pesan_wa": alerts_message(cl, alerts)})
        except (AdsError, ValueError) as ex:
            out.append({"klien": cl.name, "error": str(ex)})
    return {"hasil": out}


@tool
def weekly_report(client: str, fmt: str = "whatsapp") -> dict:
    """Buat laporan mingguan (7 hari terakhir vs minggu sebelumnya) untuk satu klien. Hanya pratinjau,
    tidak mengirim apa pun. fmt: whatsapp | markdown."""
    c = ctx()
    report = build_weekly_report(c, c.client(client), fmt)
    return {"klien": report["klien"], "teks": report["teks"], "alert": report["alert"]}


@tool
def client_dossier(client: str, write: bool = True) -> dict:
    """Buat/perbarui kit dossier klien untuk agen AI (format Agent Skills): SKILL.md (doktrin: jenis akun,
    prioritas, arahan strategi, aturan main), AGENTS.md (instruksi agen; juga untuk kolom instruksi Project),
    PROMPTBOOK.md (menu prompt), README.md (cara pasang), references/account-profile.md (angka, kampanye,
    konversi, search terms, temuan) dan references/working-with-adsai.md (kontrak tool). Data 90 hari terakhir.
    Tidak mengubah akun; write=true menyimpan ke folder dossiers/<klien>/. Kontak klien tidak dimasukkan."""
    c = ctx()
    return build_dossier(c, c.client(client), write=write)


@write_tool
def send_to_client(client: str, message: str, subject: str = "Laporan Google Ads", confirm: bool = False) -> dict:
    """Kirim pesan (mis. teks dari weekly_report) ke WhatsApp/email klien sesuai clients.yaml.
    confirm=false: tampilkan siapa penerimanya tanpa mengirim. confirm=true: kirim sungguhan.
    Selalu tunjukkan isi pesan dan penerima ke pengguna sebelum confirm=true."""
    c = ctx()
    cl = c.client(client)
    if not confirm:
        return {"status": "PRATINJAU", "klien": cl.name, "whatsapp": list(cl.whatsapp), "email": list(cl.email),
                "pesan": message, "catatan": "Belum dikirim. Panggil lagi dengan confirm=true setelah pengguna setuju."}
    return {"status": "DIKIRIM", "hasil": send_message(cl, subject, message)}


# ---------------------------------------------------------------------------
# Fase 4 — analisa lanjutan
# ---------------------------------------------------------------------------

def _resolve(client: str | None, customer_id: str | None):
    """Kembalikan (customer_id, objek klien atau None)."""
    a = ads()
    if client:
        cl = find_client(load_clients(a.settings.clients_file), client)
        return cl.customer_id, cl
    return a.customer_id(customer_id), None


@tool
def ngram_analysis(client: str | None = None, customer_id: str | None = None, campaign_id: str | None = None,
                   n: int = 1, date_range: str = "LAST_30_DAYS", start: str | None = None, end: str | None = None,
                   min_cost: float = 0, limit: int = 50) -> dict:
    """Kelompokkan istilah penelusuran per kata (n=1), dua kata (n=2), dst. dengan biaya, klik, konversi —
    untuk menemukan pola pemborosan yang tidak terlihat per istilah."""
    cid, _ = _resolve(client, customer_id)
    q = gaql.q_search_terms(gaql.date_clause(date_range, start, end), campaign_id)
    rows = ads().search(cid, f"{q} LIMIT {gaql.MAX_ROWS}")
    return {"customer_id": cid, "n": n, "jumlah_istilah": len(rows),
            "ngram": analysis.ngram_table(rows, max(1, min(n, 4)), min_cost=min_cost)[:limit]}


@tool
def suggest_negative_keywords(client: str | None = None, customer_id: str | None = None,
                              campaign_id: str | None = None, date_range: str = "LAST_30_DAYS",
                              start: str | None = None, end: str | None = None,
                              min_cost: float | None = None) -> dict:
    """Usulan kata kunci negatif dari istilah/n-gram yang memakan biaya tanpa konversi. Kata produk klien
    (protected_terms di clients.yaml) dan kata niat beli (harga, jual, distributor, ...) tidak pernah diusulkan.
    Hasilnya bisa langsung dipakai di add_negative_keywords (tetap lewat pratinjau)."""
    cid, cl = _resolve(client, customer_id)
    q = gaql.q_search_terms(gaql.date_clause(date_range, start, end), campaign_id)
    a = ads()
    rows = a.search(cid, f"{q} LIMIT {gaql.MAX_ROWS}")
    existing = [r.get("campaign_criterion.keyword.text", "") for r in a.search(cid, (
        "SELECT campaign_criterion.keyword.text FROM campaign_criterion WHERE campaign_criterion.negative = TRUE "
        "AND campaign_criterion.type = 'KEYWORD'" + (f" AND campaign.id = {int(campaign_id)}" if campaign_id else "")))]
    threshold = min_cost if min_cost is not None else (cl.thresholds.min_waste_cost if cl else 20_000)
    directives = strategy.load(cl.key)["arahan"] if cl else []
    protected = tuple(cl.protected_terms if cl else ()) + tuple(strategy.protected_terms(directives))
    sugg = analysis.suggest_negatives(rows, threshold, protected, existing)
    return {"customer_id": cid, "ambang_biaya": threshold, "jumlah_usulan": len(sugg),
            "potensi_hemat": round(sum(s["biaya"] for s in sugg), 2), "usulan": sugg,
            "penyimpangan_strategi": strategy.drift(rows, directives) or None,
            "catatan": None if cl else "Tanpa clients.yaml, kata produk klien tidak terlindungi — periksa usulan dengan teliti."}


@tool
def detect_anomalies(client: str | None = None, customer_id: str | None = None, days: int = 21,
                     z: float = 2.5) -> dict:
    """Deteksi lonjakan/penurunan tajam kemarin (biaya, klik, konversi, CPC) dibanding hari-hari sebelumnya,
    plus jumlah hari berturut-turut tanpa konversi."""
    cid, _ = _resolve(client, customer_id)
    t = today_local()
    daily = daily_rows(ads().search(cid, q_daily((t - timedelta(days=days)).isoformat(), (t - timedelta(days=1)).isoformat())))
    return {"customer_id": cid, "hari": len(daily), "anomali": analysis.detect_anomalies(daily, z),
            "hari_tanpa_konversi_berturut": analysis.zero_conversion_streak(daily), "harian": daily}


@tool
def budget_pacing(client: str | None = None, customer_id: str | None = None,
                  monthly_budget: float | None = None) -> dict:
    """Proyeksi biaya akhir bulan vs budget bulanan (dari clients.yaml atau parameter monthly_budget),
    plus saran budget harian agar pas."""
    cid, cl = _resolve(client, customer_id)
    budget = monthly_budget or (cl.monthly_budget if cl else None)
    if not budget:
        raise ValueError("monthly_budget belum diketahui. Isi di clients.yaml atau kirim sebagai parameter.")
    t = today_local()
    if t.day == 1:
        return {"customer_id": cid, "catatan": "Hari pertama bulan ini; belum ada data untuk proyeksi."}
    mtd = sum(r["cost"] for r in daily_rows(ads().search(cid, q_daily(t.replace(day=1).isoformat(),
                                                                (t - timedelta(days=1)).isoformat()))))
    return {"customer_id": cid, **analysis.budget_pacing(mtd, budget, t)}



# ---------------------------------------------------------------------------
# Riset kata kunci
# ---------------------------------------------------------------------------

@tool
def research_keywords(seed_keywords: list[str], url: str | None = None, geo_ids: list[str] | None = None,
                      language: str = "id", include_terms: list[str] | None = None,
                      exclude_terms: list[str] | None = None, limit: int = 50,
                      customer_id: str | None = None) -> dict:
    """Riset kata kunci Keyword Planner untuk Indonesia: volume bulanan, persaingan, kisaran bid atas halaman
    (Rupiah), niat (BELI / TINGGI / SEDANG / RENDAH / INFORMASI), kandidat kata kunci iklan & kandidat negatif,
    serta budget harian untuk 10/20/40 klik. Default lokasi Indonesia (geo 2360) dan bahasa Indonesia (1025);
    geo_ids bisa diisi ID kota/provinsi dari resolve_locations. Seed selalu ditampilkan walau volumenya kecil.
    include_terms: kata produk wajib (mis. ["pipa", "geopipe"]) untuk membuang ide yang melenceng.
    Hanya membaca — tidak membuat kampanye dan tidak memakan biaya iklan."""
    a = ads()
    cid = a.customer_id(customer_id)
    try:
        return keyword_research.KeywordResearch(a).run(cid, seed_keywords, url, geo_ids, language,
                                                       include_terms or (), exclude_terms or (), max(1, min(limit, 200)))
    except AdsError as ex:
        if any(k in str(ex) for k in ("PERMISSION", "NOT_APPROVED", "NOT_ALLOWED", "AUTHORIZATION")):
            raise AdsError(f"{ex} || Kemungkinan Keyword Planner lewat API belum diizinkan untuk level akses project "
                           "ini (Explorer). Ajukan Basic: Cloud Console → Google Ads API → Overview → Start Brand "
                           "Verification → Apply for access. Sementara itu, pakai Keyword Planner di UI Google Ads.",
                           ex.codes) from ex
        raise


# ---------------------------------------------------------------------------
# Sasaran konversi kampanye
# ---------------------------------------------------------------------------

@tool
def campaign_conversion_goals(campaign_id: str, customer_id: str | None = None) -> dict:
    """Sasaran konversi yang dipakai kampanye (default akun vs khusus kampanye), konversi Utama di tiap sasaran,
    dan statusnya: OK / TANPA_KONVERSI_UTAMA (bidding tanpa sinyal) / TIDAK_LENGKAP. Pakai bila status kampanye
    LIMITED dengan alasan UNKNOWN, atau UI berkata "sasaran tidak memiliki tindakan konversi utama"."""
    a = ads()
    return {"customer_id": a.customer_id(customer_id),
            **goals.Goals(mutator()).read(a.customer_id(customer_id), campaign_id)}


@write_tool
def use_account_conversion_goals(campaign_id: str, customer_id: str | None = None, confirm: bool = False) -> dict:
    """Kembalikan kampanye ke sasaran konversi default akun (setara "Gunakan sasaran default akun" di UI).
    Ditolak bila sasaran default akun pun tidak berisi konversi Utama aktif."""
    a = ads()
    return goals.Goals(mutator()).use_account_goals(a.customer_id(customer_id), campaign_id, confirm)


# ---------------------------------------------------------------------------
# Kata kunci: bid & hapus
# ---------------------------------------------------------------------------

@write_tool
def update_keyword_bids(bids: list[dict], customer_id: str | None = None, confirm: bool = False) -> dict:
    """Ubah bid CPC maksimum kata kunci (rupiah). bids: [{"id": "ad_group_id~criterion_id", "cpc_bid": 5000}].
    Kenaikan maks 2× per perubahan. Hanya berpengaruh di kampanye Manual CPC — di Smart Bidding Google
    mengabaikannya (pratinjau memberi catatan)."""
    a = ads()
    return mutator().update_keyword_bids(a.customer_id(customer_id), bids, confirm)


@write_tool
def remove_keywords(ids: list[str], customer_id: str | None = None, confirm: bool = False,
                    confirm_delete: bool = False) -> dict:
    """HAPUS PERMANEN kata kunci (id 'ad_group_id~criterion_id'). Bila maksud pengguna 'hentikan/jeda',
    pakai set_status level keyword PAUSED — jangan tool ini. Eksekusi butuh confirm=true DAN confirm_delete=true."""
    a = ads()
    return mutator().remove_keywords(a.customer_id(customer_id), ids, confirm, confirm_delete)


# ---------------------------------------------------------------------------
# Diagnosis (hanya baca)
# ---------------------------------------------------------------------------

@tool
def suggest_next_action(client: str) -> dict:
    """Mulai di sini untuk satu klien: urutan langkah paling bernilai sekarang (alert KRITIS → penyimpangan
    strategi → peringatan → rutin sesuai jenis akun), masing-masing dengan tool yang dipakai. Tidak mengubah apa pun."""
    c = ctx()
    cl = c.client(client)
    snap = collect_snapshot(c.ads, cl)
    alerts = analysis.evaluate_alerts(snap, cl)
    total = gaql.summarize(c.ads.search(cl.customer_id, gaql.q_campaign_performance(
        dossier.period(today_local())[2])))
    kind = dossier.classify_account(snap, total)
    directives = strategy.load(cl.key)["arahan"]
    drift = strategy.drift(snap.get("istilah_minggu_ini") or [], directives)
    return {"klien": cl.name, "customer_id": cl.customer_id, "jenis_akun": kind["jenis"], "alasan": kind["alasan"],
            "langkah": diagnostics.next_actions(alerts, kind["jenis"], drift),
            "arahan_strategi": len(directives)}


@tool
def verify_campaign_is_live(campaign_id: str, customer_id: str | None = None) -> dict:
    """Cek langsung ke Google apakah kampanye benar-benar bisa tayang: status, budget > 0, status utama Google
    (+alasannya), grup iklan aktif, iklan aktif yang tidak ditolak, kata kunci aktif (Search), tayangan kemarin,
    dan apakah sasaran konversinya berisi konversi Utama.
    Pakai setelah mengaktifkan kampanye atau saat klien bilang iklannya tidak muncul."""
    a = ads()
    cid = a.customer_id(customer_id)
    camp = str(int(str(campaign_id).strip()))
    data = {k: a.search(cid, q) for k, q in diagnostics.live_queries(camp).items()}
    out = {"customer_id": cid, **diagnostics.assess_live(data)}
    if out.get("vonis") != "TIDAK_DITEMUKAN":
        try:
            g = goals.Goals(mutator()).read(cid, camp)
            out["sasaran_konversi"] = {"status": g["status"], "sumber": g["sumber_sasaran"], "pesan": g["pesan"]}
            if g["status"] == "TANPA_KONVERSI_UTAMA":
                out.setdefault("saran", []).append(g["pesan"])
        except (AdsError, ValueError) as ex:
            out["sasaran_konversi"] = {"status": "TIDAK_LENGKAP", "pesan": str(ex)}
    return out


@tool
def why_did_this_fail(limit: int = 5, tool_name: str | None = None, customer_id: str | None = None) -> dict:
    """Kegagalan tool terakhir dengan error asli Google, kodenya, dan saran perbaikan. Pakai sebelum mencoba ulang
    — jangan mengulang dengan argumen tebakan. Membedakan masalah login Google Ads dari error permintaan."""
    entries = _failures().read(max(1, min(limit, 50)), tool_name, customer_id)
    return diagnostics.why_failed(entries)


@tool
def explain_error(message: str) -> dict:
    """Terjemahkan pesan error Google Ads mentah (dari tool, UI, atau log) menjadi sebab & langkah perbaikan."""
    return errors.explain(message)


# ---------------------------------------------------------------------------
# Strategi klien (strategi/<klien>.md)
# ---------------------------------------------------------------------------

@tool
def client_strategy(client: str) -> dict:
    """Arahan strategi klien yang sudah disepakati (AVOID / PREFER / CONSTRAINT / REQUIRE + alasan) dan log
    keputusan. Baca ini sebelum mengusulkan perubahan; jangan melanggar arahan tanpa membahasnya dengan pengguna.
    Bila data baru bertentangan dengan arahan, sampaikan dan tanyakan — jangan diam-diam diabaikan."""
    cl = ctx().client(client)
    data = strategy.load(cl.key)
    return {"klien": cl.name, "berkas": data["berkas"], "ada": data["ada"],
            "arahan": [d.to_dict() for d in data["arahan"]], "keputusan": data["keputusan"][:20]}


@write_tool
def add_strategy_directive(client: str, kind: str, text: str, reason: str, section: str = "Google Ads",
                           topic: str | None = None, confirm: bool = False) -> dict:
    """Simpan keputusan tim/klien sebagai arahan strategi. kind: AVOID | PREFER | CONSTRAINT | REQUIRE.
    Tulis istilah pencarian dalam tanda kutip (mis. text='"gratis"') agar dipakai mesin: istilah PREFER dilindungi
    dari usulan negatif, istilah AVOID yang masih memakan biaya dilaporkan sebagai penyimpangan.
    HANYA setelah pengguna eksplisit memutuskan — jangan simpan hasil eksplorasi atau dugaan.
    Tidak mengubah akun Google Ads; menulis ke strategi/<klien>.md beserta Decision Log bertanggal."""
    cl = ctx().client(client)
    d = strategy.build_directive(kind, text, reason, section)
    path = strategy.path_for(cl.key)
    current = path.read_text(encoding="utf-8") if path.exists() else strategy.template(cl.name)
    if any(x.line() == d.line() for x in strategy.parse(current)["arahan"]):
        return {"status": "TIDAK_BERUBAH", "klien": cl.name, "arahan": d.line()[2:], "pesan": "Arahan ini sudah ada."}
    if not confirm:
        return {"status": "PRATINJAU", "klien": cl.name, "berkas": str(path), "arahan": d.line()[2:],
                "bagian": d.bagian, "pesan": "Belum disimpan. Konfirmasi ke pengguna, lalu panggil lagi dengan confirm=true."}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(strategy.add_directive(current, d, topic or d.teks[:40], today_local()), encoding="utf-8")
    return {"status": "DISIMPAN", "klien": cl.name, "berkas": str(path), "arahan": d.line()[2:],
            "terverifikasi": [x.to_dict() for x in strategy.load(cl.key)["arahan"]]}


# ---------------------------------------------------------------------------
# Monitor & pemindaian akun (pengganti Watch Agent / monitor Adspirer)
# ---------------------------------------------------------------------------

LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def _monitor_target(client: str | None, customer_id: str | None) -> str:
    a = ads()
    if client:
        return find_client(load_clients(a.settings.clients_file), client).customer_id
    return a.customer_id(customer_id)


@functools.partial(_register, annotations=LOCAL_WRITE)
def create_monitor(name: str, conditions: list[str], consecutive_days: int = 1, campaign: str | None = None,
                   client: str | None = None, customer_id: str | None = None, confirm: bool = False) -> dict:
    """Buat alarm multi-syarat. conditions: daftar syarat yang SEMUA harus terpenuhi pada hari yang sama, mis.
    ["cpa > 150000", "biaya > 100000"] atau ["konversi = 0", "biaya >= 50000"]. Metrik: biaya, klik, tayangan,
    konversi, cpa, ctr, cpc, tingkat_konversi (uang dalam Rupiah, CTR dalam %). consecutive_days: alarm berbunyi
    bila syarat terpenuhi N hari berturut-turut sampai kemarin. campaign: nama/ID kampanye (sebagian nama boleh),
    "AKUN" untuk total akun, kosong = tiap kampanye aktif dinilai sendiri.
    confirm=false: tampilkan aturan + hasil uji coba pada data sungguhan, belum disimpan. confirm=true: simpan.
    Tidak mengubah akun Google Ads."""
    if not 1 <= int(consecutive_days) <= monitors.MAX_LOOKBACK - 1:
        raise ValueError(f"consecutive_days harus 1–{monitors.MAX_LOOKBACK - 1}.")
    m = monitors.Monitor(nama=name.strip()[:80], kondisi=monitors.parse_conditions(conditions),
                         customer_id=_monitor_target(client, customer_id), berturut_hari=int(consecutive_days),
                         kampanye=(campaign or None))
    trial = monitors.run(ads(), [m], today_local())[0]
    if not confirm:
        return {"status": "PRATINJAU", "monitor": m.to_dict(), "uji_coba_sekarang": trial,
                "pesan": "Belum disimpan. Tunjukkan aturan & hasil uji coba ke pengguna, lalu confirm=true."}
    all_m = monitors.load()
    all_m.append(m)
    monitors.save(all_m)
    return {"status": "DISIMPAN", "monitor": m.to_dict(), "uji_coba_sekarang": trial,
            "terverifikasi": [x.to_dict() for x in monitors.load() if x.id == m.id]}


@tool
def list_monitors() -> dict:
    """Daftar alarm yang tersimpan beserta aturannya."""
    return {"berkas": str(monitors.store_path()), "monitor": [m.to_dict() for m in monitors.load()]}


@functools.partial(_register, annotations=LOCAL_WRITE)
def delete_monitor(monitor_id: str, confirm: bool = False) -> dict:
    """Hapus alarm berdasarkan id (lihat list_monitors). confirm=false: pratinjau. Tidak mengubah akun Google Ads."""
    all_m = monitors.load()
    hit = [m for m in all_m if m.id == monitor_id]
    if not hit:
        raise ValueError(f"Monitor {monitor_id} tidak ditemukan. Lihat list_monitors.")
    if not confirm:
        return {"status": "PRATINJAU", "akan_dihapus": hit[0].to_dict()}
    monitors.save([m for m in all_m if m.id != monitor_id])
    return {"status": "DIHAPUS", "monitor": hit[0].to_dict(), "sisa": len(monitors.load())}


@tool
def test_monitor(monitor_id: str) -> dict:
    """Uji satu alarm tersimpan pada data sungguhan sampai kemarin — memakai logika yang SAMA dengan alarm
    sungguhan, termasuk hitungan hari berturut-turut. Tidak mengirim notifikasi."""
    hit = [m for m in monitors.load() if m.id == monitor_id]
    if not hit:
        raise ValueError(f"Monitor {monitor_id} tidak ditemukan. Lihat list_monitors.")
    return monitors.run(ads(), hit, today_local())[0]


@tool
def watch_scan(customer_id: str | None = None, days: int = 7) -> dict:
    """Pindai akun sekarang (setara Watch Agent): semua alarm tersimpan + temuan dashboard (kata kunci QS rendah,
    landing page, biaya AI Max di luar kata kunci, CPA, budget) + cek kesehatan akun. Hasil diurutkan dari yang
    paling mendesak, masing-masing dengan 'aksi' berupa pertanyaan siap pakai. Tidak mengubah apa pun.
    Cocok dijadwalkan tiap pagi; laporkan ringkas: yang terpicu dulu, lalu 1–3 langkah berikutnya."""
    a = ads()
    cid = a.customer_id(customer_id)
    today = today_local()
    alarm = monitors.run(a, [m for m in monitors.load() if m.customer_id == cid], today)
    d = dashboard.fetch(a, cid, days)
    try:
        health = account_alerts(cid)["peringatan"]
    except ToolError as ex:
        health = [f"Cek kesehatan akun gagal: {ex}"]
    items = []
    for r in alarm:
        if r["terpicu"]:
            ent = ", ".join(f"{p['entitas']} ({p['hari_berturut']} hari)" for p in r["pemicu"][:3])
            items.append({"level": "KRITIS", "sumber": "alarm", "judul": f"Alarm: {r['nama']}",
                          "pesan": f"{r['aturan']} → {ent}",
                          "aksi": f"Alarm '{r['nama']}' terpicu untuk {ent}. Cari penyebabnya dan usulkan langkah, "
                                  "pratinjau dulu sebelum mengubah apa pun."})
    for i in d["insight"]:
        if i["level"] != "INFO" or i.get("aksi"):
            items.append({"level": i["level"], "sumber": "dashboard", "judul": i.get("judul") or i["pesan"][:60],
                          "pesan": i["pesan"], "aksi": (i.get("aksi") or {}).get("prompt")})
    for text in health:
        items.append({"level": "PERINGATAN", "sumber": "kesehatan akun", "judul": text[:60], "pesan": text,
                      "aksi": None})
    items.sort(key=lambda x: dashboard.LEVELS.get(x["level"], 9))
    k = d["kpi"]
    return {"customer_id": cid, "dipindai": today.isoformat(), "periode": d["periode"],
            "ringkas": f"{days} hari: biaya {k['biaya']['nilai']:,.0f}, klik {k['klik']['nilai']:,.0f}, "
                       f"konversi {k['konversi']['nilai']:,.1f}".replace(",", "."),
            "jumlah_temuan": len(items), "alarm_dinilai": len(alarm),
            "alarm_terpicu": sum(r["terpicu"] for r in alarm), "temuan": items}


# ---------------------------------------------------------------------------
# Antrean persetujuan (pengganti list_pending_actions / manage_action Adspirer)
# ---------------------------------------------------------------------------

@functools.partial(_register, annotations=LOCAL_WRITE)
def propose_change(tool_name: str, arguments: dict, reason: str, source: str = "chat") -> dict:
    """Masukkan usulan perubahan akun ke antrean persetujuan TANPA menjalankannya. tool_name: nama tool pengubah
    (mis. add_negative_keywords, set_status, update_budget, update_keyword_bids); arguments: argumennya (tanpa
    confirm). AdsAi langsung menjalankan pratinjau (validasi Google, tidak mengubah apa pun) dan menyimpannya.
    reason: alasan berbasis data (angka, periode). Pakai ini dari pemindaian terjadwal / saat pengguna tidak di
    tempat; pengguna memutuskan lewat decide_proposal. Usulan kedaluwarsa 72 jam."""
    if tool_name in proposals.EXCLUDED or tool_name not in WRITE_TOOLS:
        raise ValueError(f"'{tool_name}' bukan tool pengubah akun yang bisa diantrekan. Pilihan: "
                         + ", ".join(sorted(set(WRITE_TOOLS) - proposals.EXCLUDED)))
    args = proposals.clean_args(arguments)
    preview = WRITE_TOOLS[tool_name](**args, confirm=False)
    if not proposals.is_preview(preview):
        return {"status": "TIDAK_DIANTREKAN", "alasan": "Pratinjau tidak menghasilkan perubahan.", "pratinjau": preview}
    items = proposals.load()
    dup = [i for i in items if i.status == proposals.PENDING and i.tool == tool_name and i.argumen == args]
    if dup:
        return {"status": "SUDAH_ADA", "usulan": dup[0].to_dict()}
    p = proposals.Proposal(tool=tool_name, argumen=args, alasan=reason.strip()[:500],
                           pratinjau=proposals.brief(preview), sumber=source[:40])
    items.append(p)
    proposals.save(items)
    return {"status": "DIANTREKAN", "usulan": p.to_dict(),
            "pesan": f"Menunggu persetujuan. Pengguna bisa bilang 'setujui usulan {p.id}' atau 'tolak usulan {p.id}'."}


@tool
def list_proposals(status: str = "MENUNGGU") -> dict:
    """Daftar usulan perubahan di antrean persetujuan. status: MENUNGGU (default) | SEMUA | DISETUJUI | DITOLAK |
    GAGAL | KEDALUWARSA. Tampilkan ke pengguna: id, apa yang berubah (sebelum → sesudah), alasan, umur usulan."""
    items = proposals.load()
    st = status.upper()
    shown = items if st == "SEMUA" else [i for i in items if i.status == st]
    return {"jumlah": len(shown), "menunggu": sum(i.status == proposals.PENDING for i in items),
            "usulan": [i.to_dict() for i in reversed(shown)][:50]}


@write_tool
def decide_proposal(proposal_id: str, decision: str, confirm: bool = False) -> dict:
    """Putuskan usulan di antrean. decision: setujui | tolak.
    setujui + confirm=false: pratinjau ULANG pada kondisi akun sekarang (bisa berbeda dari saat diusulkan).
    setujui + confirm=true: jalankan perubahan lewat tool aslinya (semua batas pengaman & log audit berlaku).
    tolak: tandai ditolak (confirm=true untuk menyimpan). Hanya setelah pengguna jelas memutuskan."""
    items = proposals.load()
    p = proposals.find(items, proposal_id)
    if p.status != proposals.PENDING:
        raise ValueError(f"Usulan {p.id} berstatus {p.status}; hanya usulan MENUNGGU yang bisa diputuskan.")
    d = decision.strip().lower()
    if d not in ("setujui", "tolak", "approve", "reject"):
        raise ValueError("decision harus 'setujui' atau 'tolak'.")
    approve = d in ("setujui", "approve")
    if not confirm:
        fresh = WRITE_TOOLS[p.tool](**p.argumen, confirm=False) if approve else None
        return {"status": "PRATINJAU", "keputusan": "setujui" if approve else "tolak", "usulan": p.to_dict(),
                **({"pratinjau_sekarang": fresh} if fresh is not None else {}),
                "pesan": "Belum diputuskan. Konfirmasi ke pengguna, lalu panggil lagi dengan confirm=true."}
    p.diputuskan = proposals._now().isoformat(timespec="seconds")
    if approve:
        try:
            p.hasil = WRITE_TOOLS[p.tool](**p.argumen, confirm=True)
            p.status = proposals.APPROVED
        except Exception as ex:  # noqa: BLE001 — catat kegagalan di antrean, lalu laporkan
            p.status, p.hasil = proposals.FAILED, {"error": str(ex)}
            proposals.save(items)
            raise
    else:
        p.status = proposals.REJECTED
    proposals.save(items)
    return {"status": p.status, "usulan": p.to_dict()}


@tool
def location_performance(customer_id: str | None = None, days: int = 30, level: str = "provinsi") -> dict:
    """Performa iklan per daerah (level: provinsi | kota) sampai kemarin: tayangan, klik, biaya, konversi,
    biaya/konversi, porsi biaya, dan temuan (daerah boros tanpa konversi, daerah paling efisien, CTR tidak wajar).
    Tersedia di semua level akses API — pengganti sementara volume Keyword Planner per daerah (butuh Basic)."""
    if level not in geo.LEVELS:
        raise ValueError("level harus 'provinsi' atau 'kota'.")
    if not 1 <= int(days) <= 365:
        raise ValueError("days harus 1–365.")
    a = ads()
    cid = a.customer_id(customer_id)
    s, e, _, _ = dashboard.period(days)
    agg = geo.aggregate(a.search(cid, geo.query(level, s, e)), level)
    names = {}
    ids = [i for i in agg if i.isdigit()]
    for k in range(0, len(ids), 200):
        for r in a.search(cid, geo.names_query(ids[k:k + 200])):
            names[str(r.get("geo_target_constant.id"))] = r.get("geo_target_constant.name")
    return {"customer_id": cid, "periode": {"mulai": s.isoformat(), "selesai": e.isoformat()}, "level": level,
            **geo.summarize(agg, names)}


@write_tool
def create_search_campaign(name: str, daily_budget: float, geo_ids: list[str], ad_groups: list[dict],
                           negative_keywords: list | None = None, schedule: list[dict] | None = None,
                           bidding: str = "MAXIMIZE_CLICKS", max_cpc: float | None = None,
                           languages: list[str] | None = None, final_urls: list[str] | None = None,
                           headlines: list | None = None, descriptions: list | None = None,
                           path1: str | None = None, path2: str | None = None,
                           customer_id: str | None = None, confirm: bool = False) -> dict:
    """Buat kampanye Penelusuran baru lengkap dalam SATU langkah atomik (gagal satu bagian = tidak ada yang
    dibuat), selalu DIJEDA. Otomatis: hanya Google Search (Search Partners & Display OFF → tidak tayang di
    YouTube/aplikasi/game), AI Max OFF, mode lokasi Kehadiran. Aset lokasi akun (Google Business Profile) ikut
    otomatis sehingga iklan bisa tampil di Google Maps.
    geo_ids: dari resolve_locations. ad_groups: [{"name", "keywords": ["teks" | {"text","match_type"}],
    "headlines"?, "descriptions"?, "final_urls"?, "path1"?, "path2"?}] — headlines/descriptions/final_urls/path
    di level atas dipakai untuk grup yang tidak mengisinya. Kata kunci string = PHRASE.
    schedule: [{"day": "hari_kerja"|"Senin".., "start": "08:00", "end": "17:00"}] (zona waktu akun).
    bidding: MAXIMIZE_CLICKS (pakai max_cpc sebagai batas) | MAXIMIZE_CONVERSIONS. languages default id+en."""
    a = ads()
    spec = {"name": name, "daily_budget": daily_budget, "geo_ids": geo_ids, "ad_groups": ad_groups,
            "negative_keywords": negative_keywords, "schedule": schedule, "bidding": bidding, "max_cpc": max_cpc,
            "languages": languages, "final_urls": final_urls, "headlines": headlines, "descriptions": descriptions,
            "path1": path1, "path2": path2}
    return campaigns.CampaignBuilder(mutator()).create(a.customer_id(customer_id), spec, confirm)

# ---------------------------------------------------------------------------
# Google Business Profile (profil Maps): lokasi, ulasan, postingan, jam khusus
# ---------------------------------------------------------------------------

_gbp: gbp.Gbp | None = None

REPLY_GUIDE = (
    "Draf balasan: sapa nama pengulas, terima kasih, sebut hal spesifik dari ulasannya, 2–4 kalimat, bahasa sopan "
    "sesuai bahasa ulasan. Ulasan negatif: minta maaf tanpa menyalahkan, jangan berdebat, ajak lanjut lewat "
    "kontak resmi. Jangan menjanjikan diskon/kompensasi, jangan menyebut data pribadi (untuk layanan kesehatan: "
    "jangan membenarkan bahwa pengulas adalah pasien). Tunjukkan draf ke pengguna sebelum gbp_reply_review confirm=true.")


def gbp_api() -> gbp.Gbp:
    global _gbp
    if _gbp is None:
        _gbp = gbp.Gbp.from_settings(ads().settings)
    return _gbp


def _gbp_account(account: str | None) -> dict:
    return gbp.pick_account(gbp_api().accounts(), account)


def _gbp_audit(account_id: str, action: str, target: dict, before: Any, after: Any, result: str) -> None:
    AuditLog(ads().settings.safety.audit_log_dir).write(f"gbp-{account_id}", action, target, before, after, result)


def _gbp_writable() -> None:
    from .safety import ensure_writable
    ensure_writable(ads().settings.safety)


@tool
def gbp_status() -> dict:
    """Cek apakah akses Google Business Profile (profil Google Maps) sudah siap, dan akun/grup lokasi apa saja
    yang bisa dikelola. Bila belum siap, menjelaskan langkah yang kurang (token, persetujuan Google, API)."""
    try:
        accounts = gbp_api().accounts()
    except gbp.GbpError as ex:
        return {"status": "BELUM_SIAP", "pesan": str(ex), "panduan": "docs/FASE-1-GBP.md"}
    return {"status": "SIAP", "akun": [{"akun": a.get("name"), "nama": a.get("accountName"), "jenis": a.get("type"),
                                       "peran": a.get("role")} for a in accounts]}


@tool
def gbp_locations(account: str | None = None, locations: list[str] | None = None) -> dict:
    """Daftar lokasi/cabang Google Business Profile + AUDIT KELENGKAPAN profil (skor 0–100 per cabang dan apa yang
    perlu dilengkapi: verifikasi, kategori, telepon, website, jam, deskripsi, alamat). account: nama/ID akun atau
    grup lokasi (lihat gbp_status). locations: filter ID/potongan nama/kode toko; kosong = semua."""
    acc = _gbp_account(account)
    locs = gbp.filter_locations(gbp_api().locations(acc["name"]), locations)
    return {"akun": acc.get("name"), "nama_akun": acc.get("accountName"), **gbp.audit(locs)}


@tool
def gbp_reviews(account: str | None = None, locations: list[str] | None = None, only_unreplied: bool = True,
                limit: int = 20) -> dict:
    """Ulasan Google per cabang: rata-rata bintang, jumlah, dan ulasan terbaru (default hanya yang BELUM dibalas,
    maksimal `limit` per cabang). Untuk membalas: tulis draf, tunjukkan ke pengguna, lalu gbp_reply_review."""
    acc = _gbp_account(account)
    api = gbp_api()
    out, all_rows = [], []
    for loc in gbp.filter_locations(api.locations(acc["name"]), locations):
        data = api.reviews(acc["name"], loc["name"], limit=max(limit, 50) if only_unreplied else limit)
        rows = [gbp.review_row(r) for r in data["reviews"]]
        all_rows += rows
        shown = [r for r in rows if not r["balasan"]] if only_unreplied else rows
        out.append({"lokasi": loc["name"], "nama": loc.get("title"), "rata_rata": data["averageRating"],
                    "jumlah_ulasan": data["totalReviewCount"], "ulasan": shown[:limit]})
    return {"akun": acc.get("name"), "temuan": gbp.review_findings(all_rows), "cabang": out,
            "panduan_balasan": REPLY_GUIDE}


@write_tool
def gbp_reply_review(review: str, comment: str, confirm: bool = False) -> dict:
    """Balas (atau ganti balasan) satu ulasan Google. review: ID lengkap dari gbp_reviews
    ('accounts/../locations/../reviews/..'). comment: teks balasan (maks 4096 karakter)."""
    if not review.startswith("accounts/") or "/reviews/" not in review:
        raise gbp.GbpError("review harus ID lengkap dari gbp_reviews: accounts/{a}/locations/{l}/reviews/{r}.")
    notes = gbp.check_reply(comment)
    api = gbp_api()
    before = gbp.review_row(api.review(review))
    if before["balasan"]:
        notes.append("Ulasan ini sudah punya balasan; balasan lama akan DIGANTI.")
    plan = {"aksi": "gbp_reply_review", "target": {"ulasan": review}, "ulasan": before,
            "sesudah": {"balasan": comment.strip()}, "catatan": notes}
    if not confirm:
        return {"status": "PRATINJAU", **plan,
                "pesan": "Belum dikirim. Google tidak menyediakan validasi balasan; tinjau teksnya lalu confirm=true."}
    _gbp_writable()
    api.reply(review, comment.strip())
    after = gbp.review_row(api.review(review))
    _gbp_audit(review.split("/")[1], "gbp_reply_review", {"ulasan": review}, before["balasan"], after["balasan"],
               "OK")
    return {"status": "DIJALANKAN", **plan, "terverifikasi": after}


@write_tool
def gbp_post(summary: str, account: str | None = None, locations: list[str] | None = None,
             topic: str = "STANDARD", cta_type: str | None = None, cta_url: str | None = None,
             image_url: str | None = None, title: str | None = None, start: str | None = None,
             end: str | None = None, coupon: str | None = None, terms: str | None = None,
             confirm: bool = False) -> dict:
    """Posting berita/promo/acara SERENTAK ke banyak cabang Google Business Profile, teks otomatis disesuaikan per
    cabang lewat placeholder {nama} {kota} {alamat} {telepon} {website} {kode}. locations kosong = semua cabang.
    topic: STANDARD (berita) | OFFER (promo, wajib title/start/end, opsional coupon/terms) | EVENT (acara).
    cta_type: BOOK | ORDER | SHOP | LEARN_MORE | SIGN_UP | CALL (selain CALL wajib cta_url).
    image_url: link https publik ke gambar. Tanggal YYYY-MM-DD. Maks 1500 karakter."""
    acc = _gbp_account(account)
    api = gbp_api()
    locs = gbp.filter_locations(api.locations(acc["name"]), locations)
    if not locs:
        raise gbp.GbpError("Tidak ada lokasi yang cocok.")
    bodies, notes = {}, []
    for loc in locs:
        body, n = gbp.build_post(gbp.render(summary, loc), topic, cta_type, cta_url, image_url, title, start, end,
                                 coupon, terms)
        bodies[loc["name"]] = body
        notes += [x for x in n if x not in notes]
    per_branch = [{"nama": l.get("title"), "kota": gbp.city(l), "teks": bodies[l["name"]]["summary"]} for l in locs]
    plan = {"aksi": "gbp_post", "target": {"akun": acc["name"], "jumlah_cabang": len(locs)},
            "jenis": topic.upper(), "tombol": cta_type, "gambar": image_url, "per_cabang": per_branch[:10],
            "catatan": notes + ([f"(+{len(locs) - 10} cabang lain, teks mengikuti pola yang sama)"]
                                if len(locs) > 10 else [])}
    if not confirm:
        return {"status": "PRATINJAU", **plan,
                "pesan": "Belum diposting. Google meninjau postingan setelah dikirim (bisa ditolak bila melanggar "
                         "kebijakan); API postingan tidak punya mode validasi."}
    _gbp_writable()
    ok, failed = gbp.fan_out(locs, lambda l: api.create_post(acc["name"], l["name"], bodies[l["name"]]))
    created = [{"nama": r["nama"], "postingan": r["hasil"].get("name"), "status_google": r["hasil"].get("state"),
                "lihat": r["hasil"].get("searchUrl")} for r in ok]
    _gbp_audit(acc["name"].split("/")[-1], "gbp_post", plan["target"], None,
               {"ringkas": summary[:200], "berhasil": len(ok), "gagal": len(failed)}, "OK" if not failed else "SEBAGIAN")
    return {"status": "DIJALANKAN" if not failed else "SEBAGIAN", **plan, "berhasil": created, "gagal": failed}


@write_tool
def gbp_special_hours(dates: list[str], account: str | None = None, locations: list[str] | None = None,
                      closed: bool = True, open_time: str | None = None, close_time: str | None = None,
                      confirm: bool = False) -> dict:
    """Pasang jam khusus (libur nasional, cuti bersama, jam pendek) SERENTAK di banyak cabang. dates: daftar
    tanggal YYYY-MM-DD. closed=true = tutup seharian; closed=false + open_time/close_time (HH:MM) = jam khusus.
    Jam khusus lama di tanggal lain TETAP dipertahankan. locations kosong = semua cabang.
    Pratinjau divalidasi Google (validateOnly) per cabang."""
    periods = gbp.special_periods(dates, closed, open_time, close_time)
    acc = _gbp_account(account)
    api = gbp_api()
    locs = gbp.filter_locations(api.locations(acc["name"]), locations)
    merged = {l["name"]: gbp.merge_special_hours(l.get("specialHours"), periods) for l in locs}
    if confirm:
        _gbp_writable()
    ok, failed = gbp.fan_out(locs, lambda l: api.patch_special_hours(l["name"], merged[l["name"]],
                                                                     validate_only=not confirm))
    rows = [{"nama": l.get("title"),
             "sebelum": [gbp.describe_period(p) for p in (l.get("specialHours") or {}).get("specialHourPeriods", [])],
             "sesudah": [gbp.describe_period(p) for p in merged[l["name"]]["specialHourPeriods"]]} for l in locs]
    plan = {"aksi": "gbp_special_hours", "target": {"akun": acc["name"], "jumlah_cabang": len(locs)},
            "jam_baru": [gbp.describe_period(p) for p in periods], "per_cabang": rows[:10], "gagal": failed}
    if not confirm:
        return {"status": "PRATINJAU", **plan,
                "pesan": f"Lolos validasi Google di {len(ok)} dari {len(locs)} cabang. Belum ada yang diubah."}
    _gbp_audit(acc["name"].split("/")[-1], "gbp_special_hours", plan["target"], None,
               {"jam_baru": plan["jam_baru"], "berhasil": len(ok), "gagal": len(failed)},
               "OK" if not failed else "SEBAGIAN")
    verified = [{"nama": r["nama"], "jam_khusus": [gbp.describe_period(p) for p in
                                                   (r["hasil"].get("specialHours") or {}).get("specialHourPeriods", [])]}
                for r in ok]
    return {"status": "DIJALANKAN" if not failed else "SEBAGIAN", **plan, "terverifikasi": verified[:10]}

# ---------------------------------------------------------------------------
# Google Analytics 4 (website): baca saja
# ---------------------------------------------------------------------------

_ga4: ga4.Ga4 | None = None


def ga4_api() -> ga4.Ga4:
    global _ga4
    if _ga4 is None:
        _ga4 = ga4.Ga4.from_settings(ads().settings)
    return _ga4


def _ga4_range(days: int, start: str | None, end: str | None) -> tuple[str, str]:
    if start or end:
        if not (start and end):
            raise ga4.Ga4Error("Isi start dan end sekaligus (YYYY-MM-DD).")
        return start, end
    return ga4.date_range(days, today_local())


@tool
def ga4_properties() -> dict:
    """Properti Google Analytics 4 (website) yang bisa dibaca AdsAi — milik sendiri dan klien yang sudah
    menambahkan email agensi sebagai Viewer. Bila belum siap, menjelaskan langkah yang kurang."""
    try:
        props = ga4_api().properties()
    except ga4.Ga4Error as ex:
        return {"status": "BELUM_SIAP", "pesan": str(ex), "panduan": "docs/FASE-3-GA4.md"}
    return {"status": "SIAP", "properti": props}


@tool
def ga4_overview(property: str | None = None, days: int = 30, start: str | None = None,
                 end: str | None = None) -> dict:
    """Ringkasan website dari GA4: pengunjung & kunjungan per saluran (iklan, organik, langsung, sosial),
    halaman masuk teratas + tingkat keterlibatan, event teratas (mis. klik WhatsApp/telepon), dan temuan.
    property: nama/ID properti (lihat ga4_properties); kosong bila hanya satu. Default 30 hari sampai kemarin."""
    api = ga4_api()
    prop = ga4.pick_property(api.properties(), property)
    s, e = _ga4_range(days, start, end)
    pid = prop["properti"]
    channels = api.run_report(pid, s, e, ["sessionDefaultChannelGroup"],
                              ["sessions", "totalUsers", "engagedSessions", "keyEvents"], 20, "sessions")
    pages = api.run_report(pid, s, e, ["landingPagePlusQueryString"],
                           ["sessions", "engagementRate", "keyEvents"], 15, "sessions")
    events = api.run_report(pid, s, e, ["eventName"], ["eventCount", "keyEvents"], 25, "eventCount")
    total = {k: sum(c.get(k, 0) for c in channels) for k in ("sessions", "totalUsers", "engagedSessions", "keyEvents")}
    return {"properti": pid, "nama": prop["nama"], "periode": {"mulai": s, "selesai": e},
            "total": {"kunjungan": total["sessions"], "pengguna_per_saluran_dijumlah": total["totalUsers"],
                      "kunjungan_terlibat": total["engagedSessions"], "key_event": total["keyEvents"]},
            "temuan": ga4.overview_findings(channels, pages, events),
            "saluran": channels, "halaman_masuk": pages, "event": events,
            "catatan": "Pengguna per saluran dijumlah bisa lebih besar dari pengguna unik (satu orang bisa datang "
                       "dari beberapa saluran)."}


@tool
def ga4_report(dimensions: list[str], metrics: list[str], property: str | None = None, days: int = 30,
               start: str | None = None, end: str | None = None, limit: int = 50,
               contains: dict[str, str] | None = None) -> dict:
    """Laporan GA4 bebas (seperti run_query untuk Google Ads). Contoh dimensi: date, sessionSourceMedium,
    sessionDefaultChannelGroup, landingPagePlusQueryString, pagePath, eventName, city, region, deviceCategory,
    sessionGoogleAdsCampaignName. Contoh metrik: sessions, totalUsers, newUsers, engagedSessions,
    engagementRate, averageSessionDuration, screenPageViews, eventCount, keyEvents.
    contains: filter teks, mis. {"eventName": "whatsapp"} atau {"pagePath": "/plastik-cor"}."""
    if not dimensions or not metrics:
        raise ga4.Ga4Error("Isi minimal satu dimensi dan satu metrik.")
    api = ga4_api()
    prop = ga4.pick_property(api.properties(), property)
    s, e = _ga4_range(days, start, end)
    filt = None
    if contains:
        exprs = [{"filter": {"fieldName": k, "stringFilter": {"matchType": "CONTAINS", "value": v,
                                                               "caseSensitive": False}}} for k, v in contains.items()]
        filt = exprs[0] if len(exprs) == 1 else {"andGroup": {"expressions": exprs}}
    data = api.run_report(prop["properti"], s, e, dimensions, metrics, min(max(limit, 1), 1000), metrics[0], filt)
    return {"properti": prop["properti"], "nama": prop["nama"], "periode": {"mulai": s, "selesai": e},
            "jumlah_baris": len(data), "baris": data}

# ---------------------------------------------------------------------------
# Meta Ads (Facebook & Instagram): baca saja
# ---------------------------------------------------------------------------

_meta: meta.Meta | None = None


def meta_api() -> meta.Meta:
    global _meta
    if _meta is None:
        _meta = meta.Meta.from_settings(ads().settings)
    return _meta


def _meta_account(account: str | None) -> dict:
    return meta.pick_account(meta_api().ad_accounts(), account or ads().settings.meta_default_account or None)


@tool
def meta_status() -> dict:
    """Cek koneksi Meta Ads (Facebook/Instagram) dan daftar akun iklan yang bisa dibaca beserta statusnya
    (aktif, tunggakan, dll.). Bila belum siap, menjelaskan langkah yang kurang."""
    try:
        accounts = meta_api().ad_accounts()
    except meta.MetaError as ex:
        return {"status": "BELUM_SIAP", "pesan": str(ex), "panduan": "docs/META-ADS.md"}
    return {"status": "SIAP", "akun": accounts}


@tool
def meta_performance(account: str | None = None, days: int = 30, start: str | None = None,
                     end: str | None = None) -> dict:
    """Performa Meta Ads (Facebook & Instagram): total biaya, tayangan, jangkauan, frekuensi, klik, hasil
    (percakapan WA, prospek, pembelian, kontak) + biaya per hasil, perbandingan periode sebelumnya, tabel per
    kampanye (status, tujuan, budget), porsi biaya per platform (Facebook/Instagram/Audience Network/Messenger),
    dan temuan. account: nama/ID akun (lihat meta_status). Default 30 hari sampai kemarin; maks 90."""
    api = meta_api()
    acc = _meta_account(account)
    if start or end:
        if not (start and end):
            raise meta.MetaError("Isi start dan end sekaligus (YYYY-MM-DD).")
        cur, prev = (start, end), None
    else:
        cur, prev = meta.ranges(days, today_local())
    total = [meta.clean(r) for r in api.insights(acc["akun"], *cur)]
    before = [meta.clean(r) for r in api.insights(acc["akun"], *prev)] if prev else []
    rows = [meta.clean(r) for r in api.insights(acc["akun"], *cur, level="campaign")]
    places = [meta.clean(r) for r in api.insights(acc["akun"], *cur, breakdowns=["publisher_platform"])]
    info = {c["id"]: c for c in api.campaigns(acc["akun"])}
    for r in rows:
        c = info.get(r.get("campaign_id"), {})
        r.update(status=c.get("effective_status"), tujuan=c.get("objective"), budget=meta.budget(c, acc["mata_uang"]))
    rows.sort(key=lambda r: -r["biaya"])
    t = total[0] if total else meta.clean({})
    b = before[0] if before else None
    compare = None
    if b:
        compare = {"periode": prev, "biaya": meta.change(t["biaya"], b["biaya"]),
                   "klik_link": meta.change(t["klik_link"], b["klik_link"]),
                   **{f"hasil_{k}": meta.change(v, b["hasil"].get(k, 0)) for k, v in t["hasil"].items()}}
    share = {p.get("publisher_platform"): (f"{p['biaya'] / t['biaya']:.0%}" if t["biaya"] else "0%") for p in places}
    return {"akun": acc["akun"], "nama": acc["nama"], "mata_uang": acc["mata_uang"],
            "periode": {"mulai": cur[0], "selesai": cur[1]}, "total": t, "dibanding_sebelumnya": compare,
            "porsi_biaya_per_platform": share, "temuan": meta.findings(rows, places), "kampanye": rows}


@tool
def meta_insights(account: str | None = None, level: str = "campaign", breakdowns: list[str] | None = None,
                  days: int = 30, start: str | None = None, end: str | None = None, daily: bool = False,
                  limit: int = 100) -> dict:
    """Rincian Meta Ads. level: account | campaign | adset | ad. breakdowns (opsional): age, gender,
    publisher_platform, platform_position, device_platform, region, country. daily=true = per hari.
    Contoh: umur & gender yang paling murah per WA → level=account, breakdowns=["age","gender"]."""
    api = meta_api()
    acc = _meta_account(account)
    rng = (start, end) if (start and end) else meta.ranges(days, today_local())[0]
    rows = [meta.clean(r) for r in api.insights(acc["akun"], *rng, level=level, breakdowns=breakdowns, daily=daily)]
    if not daily:
        rows.sort(key=lambda r: -r["biaya"])
    return {"akun": acc["akun"], "nama": acc["nama"], "mata_uang": acc["mata_uang"],
            "periode": {"mulai": rng[0], "selesai": rng[1]}, "jumlah_baris": len(rows), "baris": rows[:limit]}


@tool
def meta_ads(account: str | None = None, campaign_id: str | None = None, start: str | None = None,
             end: str | None = None, days: int = 30, limit: int = 50) -> dict:
    """Daftar iklan Meta beserta TEKS iklannya, set/kampanye induk, status, dan performa (biaya, hasil WA/prospek,
    biaya per hasil) — untuk mencari iklan yang SUDAH TERBUKTI. Untuk iklan lama isi start/end (YYYY-MM-DD),
    mis. start=2025-05-01 end=2025-05-31. campaign_id: batasi ke satu kampanye."""
    api = meta_api()
    acc = _meta_account(account)
    rng = (start, end) if (start and end) else meta.ranges(days, today_local())[0]
    perf = {r.get("ad_id"): meta.clean(r) for r in api.insights(acc["akun"], *rng, level="ad")}
    out = []
    for ad in api.ads(acc["akun"], campaign_id):
        p = perf.get(ad["id"])
        if campaign_id is None and p is None:
            continue   # tanpa filter kampanye: tampilkan hanya iklan yang tayang di periode ini
        cr = ad.get("creative") or {}
        out.append({"iklan": ad["id"], "nama": ad.get("name"), "status": ad.get("effective_status"),
                    "set_iklan": (ad.get("adset") or {}).get("name"), "set_iklan_id": ad.get("adset_id"),
                    "kampanye": (ad.get("campaign") or {}).get("name"), "kampanye_id": ad.get("campaign_id"),
                    "teks": (cr.get("body") or "")[:300], "judul": cr.get("title"),
                    "postingan": cr.get("effective_object_story_id") or cr.get("object_story_id"),
                    "biaya": (p or {}).get("biaya", 0), "hasil": (p or {}).get("hasil", {}),
                    "biaya_per_hasil": (p or {}).get("biaya_per_hasil", {})})
    out.sort(key=lambda r: -r["biaya"])
    return {"akun": acc["akun"], "nama": acc["nama"], "mata_uang": acc["mata_uang"],
            "periode": {"mulai": rng[0], "selesai": rng[1]}, "jumlah": len(out), "iklan": out[:limit]}


@write_tool
def meta_create_campaign_from_proven(name: str, ad_sets: list[dict], account: str | None = None,
                                     source_adset: str | None = None, confirm: bool = False) -> dict:
    """Buat kampanye Meta BARU dari iklan yang sudah terbukti, selalu DIJEDA (tidak membelanjakan uang sampai
    diaktifkan dengan meta_set_status). Materi iklan dipakai ulang apa adanya (postingan lama: like & komentar
    ikut); pengaturan tujuan/WhatsApp/halaman disalin dari set iklan sumber (default: set iklan dari iklan pertama).
    ad_sets: [{"name", "daily_budget" (mata uang akun, mis. 70000), "ads": [ID iklan sumber dari meta_ads],
    "age_min"?, "age_max"?, "gender"? (pria|wanita|semua), "placements"? (feed_reels|feed|otomatis)}].
    Kampanye memakai budget per set iklan (ABO). Pratinjau divalidasi Meta (validate_only)."""
    from .safety import SafetyError, check_budget, ensure_writable
    a = ads()
    acc = _meta_account(account)
    launcher = meta_launch.Launcher(meta_api(), acc["akun"], acc["mata_uang"] or "IDR")
    plan = launcher.plan(name, ad_sets, source_adset)
    notes = list(plan["notes"])
    for s in ad_sets:
        notes += check_budget(a.settings.safety, None, float(s["daily_budget"]))
    total = sum(float(s["daily_budget"]) for s in ad_sets)
    if a.settings.safety.max_daily_budget is not None and total > a.settings.safety.max_daily_budget:
        raise SafetyError(f"Total budget harian {total:,.0f} melebihi ADSAI_MAX_DAILY_BUDGET "
                          f"({a.settings.safety.max_daily_budget:,.0f}).")
    summary = launcher.summary(plan)
    existing = [c for c in meta_api().campaigns(acc["akun"]) if (c.get("name") or "").strip().lower()
                == name.strip().lower() and c.get("effective_status") != "DELETED"]
    if existing:
        raise meta.MetaError(f"Sudah ada kampanye bernama '{name}' (ID {existing[0]['id']}). Pakai nama lain.")
    if not confirm:
        checked = launcher.validate(plan)
        return {"status": "PRATINJAU", "akun": acc["akun"], "nama_akun": acc["nama"], "aksi": "meta_create_campaign",
                **summary, "lolos_validasi_meta": checked, "catatan": notes,
                "pesan": "Belum dibuat. Setelah dibuat, kampanye DIJEDA — tidak ada biaya sampai diaktifkan."}
    ensure_writable(a.settings.safety)
    created = launcher.execute(plan)
    verified = launcher.verify(created["kampanye"])
    AuditLog(a.settings.safety.audit_log_dir).write(
        f"meta-{acc['akun']}", "meta_create_campaign", {"kampanye": created["kampanye"]}, None,
        {"nama": name, "set_iklan": created["set_iklan"], "total_harian": total}, "OK")
    return {"status": "DIJALANKAN", "akun": acc["akun"], **summary, "dibuat": created, "terverifikasi": verified,
            "catatan": notes + ["Kampanye DIJEDA. Aktifkan dengan meta_set_status level=campaign status=ACTIVE "
                                "setelah dicek (metode pembayaran, teks iklan)."]}


@write_tool
def meta_set_status(level: str, ids: list[str], status: str, account: str | None = None,
                    confirm: bool = False) -> dict:
    """Aktifkan/jeda kampanye, set iklan, atau iklan Meta. level: campaign | adset | ad. status: ACTIVE | PAUSED.
    Mengaktifkan kampanye baru juga butuh set iklan & iklannya ACTIVE (dibuat AdsAi dalam keadaan dijeda)."""
    from .safety import ensure_writable
    level, status = level.lower(), status.upper()
    if level not in ("campaign", "adset", "ad") or status not in ("ACTIVE", "PAUSED"):
        raise meta.MetaError("level: campaign | adset | ad; status: ACTIVE | PAUSED.")
    if not ids:
        raise meta.MetaError("Isi minimal satu ID.")
    a = ads()
    acc = _meta_account(account)
    api = meta_api()
    targets = [api.get(i, "name,status,effective_status,account_id") for i in ids]
    wrong = [t["id"] for t in targets if t.get("account_id") and f"act_{t['account_id']}" != acc["akun"]]
    if wrong:
        raise meta.MetaError(f"ID {', '.join(wrong)} bukan milik akun {acc['nama']} ({acc['akun']}).")
    before = [{"id": t["id"], "nama": t.get("name"), "status": t.get("effective_status")} for t in targets]
    plan = {"aksi": "meta_set_status", "akun": acc["akun"], "level": level, "status_baru": status, "sebelum": before}
    if status == "ACTIVE":
        plan["catatan"] = ["Setelah aktif, iklan mulai tayang dan membelanjakan uang (setelah lolos tinjauan Meta)."]
    if not confirm:
        return {"status": "PRATINJAU", **plan, "pesan": "Belum diubah."}
    ensure_writable(a.settings.safety)
    for t in targets:
        api.update(t["id"], {"status": status})
    after = [{"id": t["id"], "nama": t.get("name"), "status": api.get(t["id"], "effective_status")
              .get("effective_status")} for t in targets]
    AuditLog(a.settings.safety.audit_log_dir).write(f"meta-{acc['akun']}", "meta_set_status",
                                                    {"level": level, "ids": ids}, before, after, "OK")
    return {"status": "DIJALANKAN", **plan, "terverifikasi": after}


def main() -> None:
    mcp.run()


def main_http() -> None:
    """Mode online (Cloud Run): Streamable HTTP di 0.0.0.0:$PORT, wajib login (lihat remote_auth)."""
    import os

    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    if not _remote:
        raise SystemExit("ADSAI_PUBLIC_URL belum diisi — mode online butuh login. Lihat docs/SERVER-ONLINE.md.")
    app = mcp.streamable_http_app(
        host="0.0.0.0",
        # Stateless: Cloud Run me-restart/menidurkan instance (deploy, idle) dan sesi di memori hilang; klien
        # yang membawa mcp-session-id lama lalu ditolak 404 "Session not found" — kartu gagal dimuat di
        # claude.ai/HP. AdsAi tidak memakai permintaan server→klien, jadi tiap request bisa berdiri sendiri.
        stateless_http=True,
        # Proteksi DNS-rebinding untuk server lokal; di server publik setiap permintaan sudah wajib token.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")), proxy_headers=True,
                forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
