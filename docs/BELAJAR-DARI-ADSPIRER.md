# Belajar dari Adspirer

Catatan hidup: diperbarui setiap kali kita memakai Adspirer dan menemukan pola baru.

**Sumber yang dipelajari**
1. Plugin publik Adspirer untuk Claude (`adspirer-ads-agent`, repo `amekala/ads-mcp`) — **lisensi MIT**.
   Isinya skill, perintah, dan agent (teks instruksi untuk Claude), bukan kode server.
2. Pemakaian langsung di akun PT Tiga Pilar (Okt 2026): perilaku tool, error, kuota.

Kode server Adspirer (`mcp.adspirer.com`) tertutup dan **tidak** kita tiru. Yang kita pelajari adalah
arsitektur yang terlihat dari luar dan isi plugin MIT-nya (diadaptasi dengan atribusi, lihat
[NOTICE](../NOTICE)).

---

## 1. Arsitektur Adspirer (yang terlihat dari luar)

```
Claude ──► plugin (skills + commands + agent)      ← "otak": cara berpikir & aturan main
   │
   └──► MCP server remote (HTTP + OAuth)            ← "tangan": 400+ tools
          ├─ ±20 tool langsung (start_here, search_tools, get_tool_schema,
          │     get_connections_status, get_usage_status, get_campaign_performance, ...)
          └─ router per platform (google_ads, meta_ads, tiktok_ads, ... monitoring_and_reporting)
                 action="list_tools"  → daftar tool + skema (gratis)
                 action="execute"     → jalankan tool (memakai kuota)
Web app adspirer.ai ── sambungkan akun iklan, dashboard, monitor server-side
```

| Lapisan | Fungsi | Pelajaran untuk AdsAi |
|---|---|---|
| **Plugin (skills)** | Kontrak keamanan, alur kerja (optimasi, review, launch), jebakan per platform, katalog error | **Nilai terbesar ada di sini.** AdsAi belum punya lapisan ini → sudah ditambahkan (`plugin/`) |
| **Router + discovery** | Ratusan tool tanpa membanjiri konteks Claude; `search_tools` + `get_tool_schema` | Perlu bila tool AdsAi > ±40. Saat ini 24 tool → belum perlu |
| **Tool "kartu"** | `get_campaign_performance` merender dashboard; opsi `raw_data` | Pertimbangkan `raw=true` di tool performa |
| **Kuota per call** | Free 15 · Plus 150 · Pro 600 · Max 3.000/bulan; discovery gratis; call gagal tidak dihitung | Model bisnis bila nanti SaaS (Fase 5) |
| **Monitor server-side** | `monitoring_and_reporting`: monitor ambang, brief terjadwal via email | Setara `adsai alerts/report` (cron) milik kita |

## 2. Prinsip kerja yang layak diadopsi

1. **Uang butuh "ya".** Tidak ada pembuatan kampanye, kenaikan budget, ganti bidding, atau resume
   tanpa persetujuan eksplisit untuk aksi itu. → AdsAi: sudah (`confirm=true`).
2. **Semua dibuat dalam keadaan dijeda.** → AdsAi: berlaku saat nanti ada tool buat kampanye.
3. **Baca dulu sebelum menulis.** → AdsAi: sudah (Mutator mengambil kondisi "sebelum").
4. **Verifikasi setelah menulis** — baca ulang objek dan laporkan yang benar-benar terjadi.
   → **Ditambahkan** ke AdsAi (lihat §5).
5. **Jangan mengarang angka.** Tool gagal = bilang gagal.
6. **Cek tracking sebelum memotong apa pun.** Kampanye "tanpa konversi" sering sebenarnya
   berkonversi tapi tidak tercatat. → Masuk SOP & skill AdsAi.
7. **Cek volume sebelum menyebut pecundang:** minimal ±3× target CPA atau ±100 klik.
8. **Urutan perbaikan:** negatif → eksklusi → jeda (sesempit mungkin) → realokasi.
9. **Realokasi bertahap 20–30%, lalu tunggu seminggu** (fase belajar).
10. **Jangan pasang Target CPA/ROAS tanpa histori konversi.** → **Ditambahkan** sebagai peringatan.
11. **Google boleh belanja sampai 2× budget harian di satu hari**, rata di akhir bulan —
    jangan panik. → **Ditambahkan** ke catatan pacing.
12. **Jangan janji "saya pantau" — jadwalkan.** → AdsAi: cron/Task Scheduler + `adsai alerts`.
13. **Bandingkan periode setara** (7 hari vs 7 hari sebelumnya; hari ini selalu parsial).
    → AdsAi: sudah (`week_ranges` tidak memasukkan hari ini).
14. **Mulai jawaban dengan kesimpulan**, baru tabel. Satu rekomendasi, dengan angka.

## 3. Temuan dari pemakaian langsung (akun Tiga Pilar, 14 Okt 2026)

| Temuan | Arti untuk AdsAi |
|---|---|
| `audit_conversion_tracking` memberi nilai **B (80/100)** padahal ada **17 konversi Utama** termasuk *Scroll Depth 75* | Audit mereka melewatkan masalah terbesar. `conversion_actions` & `account_alerts` AdsAi sudah menandai >3 Utama — **keunggulan kita** |
| Konversi buatan Google (*Clicks to call*, *Add to basket (page load)*, *Local actions*) → `Mutates are not allowed` | Batasan Google Ads API, bukan Adspirer. AdsAi sudah memberi catatan untuk tipe `GOOGLE_HOSTED` |
| Mengubah *view-through window* + *counting type* "Calls from ads" → `The field cannot be set` | Untuk tipe `AD_CALL`, sebagian field hanya bisa diubah di UI. Perlu catatan tambahan |
| Call yang gagal **tidak** memotong kuota | Wajar ditiru bila SaaS |
| Parameter `budget_amount` "in dollars" = **mata uang akun** (Rp50.000 diterima sebagai 50000) | AdsAi memakai mata uang akun secara eksplisit — lebih jelas |
| `list_conversion_actions` dengan filter `primary` memberi daftar lengkap + ID | Pola filter berguna; `conversion_actions` AdsAi menampilkan semua + hitungan Utama |
| Tidak ada pratinjau `validate_only` — langsung eksekusi setelah Claude bertanya | **Keunggulan AdsAi**: pratinjau tervalidasi Google sebelum konfirmasi |

## 4. Perbandingan cakupan Google Ads

| Area | Adspirer | AdsAi | Prioritas |
|---|---|---|---|
| Performa, search terms, kata kunci, konversi | ✅ | ✅ | — |
| Negatif kampanye, budget, status, bidding, konversi | ✅ | ✅ (+ pratinjau, batas budget, log audit) | — |
| **Shared negative keyword list** | ✅ | ❌ | **Tinggi** — hemat kerja lintas kampanye |
| **Struktur kampanye** (`get_campaign_structure`: grup iklan, iklan, ekstensi) | ✅ | Sebagian (`run_query`) | **Tinggi** |
| **Iklan RSA**: baca/buat/ubah headline & deskripsi | ✅ | ❌ | Tinggi |
| **Ekstensi**: sitelink, callout, snippet, call | ✅ | ❌ | Sedang — Adspirer: wajib ≥4 sitelink, 4–6 callout |
| Keyword research (Keyword Planner) | ✅ | ❌ | Sedang |
| Jadwal iklan, lokasi, perangkat | ✅ | ❌ | Sedang |
| Buat kampanye Search/PMax/Demand Gen/Video/Display | ✅ | ❌ | Rendah untuk Model A (tim buat di UI) |
| Analisa anomali & wasted spend | ✅ | ✅ (+ n-gram, perlindungan kata produk) | — |
| Laporan & alert terjadwal | Email/server-side | ✅ WhatsApp + email, Bahasa Indonesia | — |
| Multi-platform (Meta, TikTok, LinkedIn, Amazon, ChatGPT) | ✅ | ❌ | Meta = berikutnya (klien FB Ads banyak) |

## 5. Yang sudah diterapkan ke AdsAi dari pelajaran ini

- **Lapisan plugin/skill AdsAi** (`plugin/`): kontrak keamanan & alur kerja dalam Bahasa Indonesia,
  diadaptasi dari plugin Adspirer (MIT) dan disesuaikan dengan tool AdsAi.
- **Verifikasi setelah menulis**: setiap perubahan `confirm=true` membaca ulang kondisi dan
  mengembalikan `terverifikasi`.
- **Peringatan Target CPA tanpa histori**: `update_bid_strategy` memperingatkan bila konversi
  30 hari < 30.
- **Catatan pacing**: Google boleh belanja hingga 2× budget harian per hari.

## 6. Log observasi (tambahkan setiap sesi)

| Tanggal | Yang dicoba di Adspirer | Pola/pelajaran |
|---|---|---|
| 2026-10-14 | Audit konversi, list kampanye & konversi, 2× negatif, 3× ubah kampanye, 6× ubah konversi | Lihat §3 |
