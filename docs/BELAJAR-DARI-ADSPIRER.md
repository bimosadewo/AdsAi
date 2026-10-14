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
| **Shared negative keyword list** | ✅ | ✅ (+ dedup, cek tipe list, info jumlah kampanye terdampak) | — |
| **Struktur kampanye** (`get_campaign_structure`) | ✅ | ✅ `campaign_structure` (+ temuan audit struktur otomatis) | — |
| **Iklan RSA**: baca/buat/ubah, performa per iklan | ✅ | ✅ (+ `validate_ad_copy` tanpa API, pin, cek superlatif Bahasa Indonesia, dibuat dijeda) | — |
| **Ekstensi**: sitelink, callout, snippet, call | ✅ | ✅ (+ ubah atomik, alias header Indonesia, nomor Indonesia, dedup lintas format nomor) | — |
| Keyword research (Keyword Planner) | ✅ | ❌ | Sedang |

| Buat kampanye Search/PMax/Demand Gen/Video/Display | ✅ | ❌ | Rendah untuk Model A (tim buat di UI) |
| Analisa anomali & wasted spend | ✅ | ✅ (+ n-gram, perlindungan kata produk) | — |
| Pengaturan kampanye & targeting (jaringan, AI Max, lokasi, radius, jadwal, perangkat) | ✅ | ✅ (+ ganti jadwal atomik, peringatan Smart Bidding, pengaman lokasi/perangkat) | — |
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

### Putaran 2 (shared list, struktur, RSA)

Dipelajari dari skema tool Adspirer (dibaca gratis via `get_tool_schema`) dan skill `adspirer-creative`:
- Struktur kampanye dibagi per halaman grup iklan + ringkasan total + opsi satu grup iklan → diadopsi.
- Performa per iklan dengan ad strength untuk memilih pemenang → `ad_performance`.
- Aturan teks RSA: tanpa "!", tanpa superlatif tanpa bukti, 3–15 headline ≤30, 2–4 deskripsi ≤90 →
  `rsa.validate` (+ versi Bahasa Indonesia: terbaik, nomor 1, termurah, terlengkap).
- Prinsip menulis iklan: tulis dari search terms nyata, satu ide per headline, spesifik > kata sifat,
  variasikan sudut pandang → skill `adsai-iklan`.
- Ekstensi wajib untuk Search: ≥4 sitelink, 4–6 callout, 1 snippet → temuan audit di `campaign_structure`.
- Peningkatan AdsAi di luar Adspirer: alert `IKLAN_DITOLAK` di `client_health`/laporan; iklan baru
  dibuat **dijeda** dan diaktifkan lewat `set_status level="ad"`.

### Putaran 3 (ekstensi)

Dari skema `add_sitelinks`, `add_callout_extensions`, `add_structured_snippets`, `add_call_asset`,
`list_campaign_extensions`, `remove_*`, `set_call_asset_status` dan referensi `extensions.md`:
- Batas: sitelink teks ≤25, deskripsi ≤35 berpasangan; callout ≤25 tanpa titik/CTA; snippet header resmi,
  3–10 nilai ≤25 → validator `extensions.py`.
- Telepon idempoten (nomor sama tidak ditambah lagi) → diadopsi, dicocokkan lintas format (0812…/+62 812…).
- Ekstensi per grup iklan mengalahkan level kampanye → opsi `ad_group_id`.
- "Remove" = lepas tautan; aset tetap → sama di AdsAi. **Perbedaan**: Adspirer melepas SEMUA bila
  `asset_ids` kosong; AdsAi sengaja menolak (wajib daftar ID) agar tidak ada penghapusan massal tak sengaja.
- Adspirer tidak punya "ubah" langsung (hapus lalu tambah, dua langkah). AdsAi punya `update_extension`
  yang atomik: aset baru + tautan baru + lepas lama dalam satu request, gagal satu batal semua.
- Ditemukan & diperbaiki saat membangun: `humanize` menghapus angka 0 di depan nomor telepon
  ('081234…' → 81234…). Konversi angka kini dibatasi ke metrik & field hitungan.

### Putaran 4 (pengaturan kampanye & targeting)

Dari skema `get_campaign_targeting`, `update_campaign`, `update_location_targeting`, `add_campaign_proximity`,
`resolve_google_locations`, `set_campaign_ad_schedule`, `set_campaign_device_bid_modifiers`,
`set_ad_group_device_bid_modifiers`:
- PRESENCE lebih efisien dari default PRESENCE_OR_INTEREST untuk bisnis lokal → diadopsi + jadi temuan audit.
- Search Partners & perluasan Display default OFF di kampanye baru Adspirer → audit AdsAi menandai bila ON,
  dan `client_health` kini memberi alert `PENGATURAN_BERISIKO` untuk kampanye Search aktif.
- Jadwal: begitu ada jendela, iklan hanya tayang di jendela itu. Adspirer hanya *menambah* (menambah jendela
  sama = error; mengganti harus hapus manual dulu). **AdsAi `set_ad_schedule` mengganti seluruh jadwal secara
  atomik** + validasi tumpang tindih, menit 00/15/30/45, alias hari Indonesia (hari_kerja, akhir_pekan, …).
- Perangkat: update-or-create (kriteria perangkat sudah ada otomatis) → diadopsi, kampanye & grup iklan.
- **Ditambahkan AdsAi (tidak ada di skema Adspirer):** peringatan bahwa Smart Bidding mengabaikan penyesuaian bid
  perangkat/jadwal/lokasi kecuali -100%; penolakan menghapus semua target lokasi (iklan jadi tayang ke seluruh
  dunia) dan menolak -100% di semua perangkat.

### Putaran 5 ("Expert Space", dari screenshot web app adspirer.ai)

- Produk berbayar terpisah: **"expert kit" $39 sekali bayar per akun iklan**, "free refreshes, on demand",
  dipakai di Claude, ChatGPT, Claude Code, Cursor.
- Isi kit: Agent Skill (`SKILL.md` + `references/` + `PROMPTBOOK.md` + `AGENT_INSTRUCTIONS.md` + `AGENTS.md`) —
  "durable expertise plus a dossier of this account's state"; prompt bertingkat Beginner → Expert;
  instruksi project siap tempel yang mengunci akun + guardrail.
- Untuk Tiga Pilar halaman menyebut "2 campaigns, 1 month of history" — kemungkinan hanya kampanye/data
  terbaru yang dihitung.
- **Pelajaran:** nilai jual bukan hanya tools, tapi *konteks akun yang dikemas* agar AI langsung paham akun.
- **Diterapkan:** `client_dossier` / `adsai dossier` — kit setara, otomatis dari data 90 hari, gratis untuk semua
  klien, berbahasa Indonesia, dengan kandidat negatif yang sudah melewati filter pengaman dan tanpa kontak klien.
  Bisa dijadikan deliverable paket jasa ("Project AI khusus untuk bisnis Anda").

## 6. Log observasi (tambahkan setiap sesi)

| Tanggal | Yang dicoba di Adspirer | Pola/pelajaran |
|---|---|---|
| 2026-10-14 | Audit konversi, list kampanye & konversi, 2× negatif, 3× ubah kampanye, 6× ubah konversi | Lihat §3 |
| 2026-10-14 | `search_tools` (gratis) + `get_tool_schema` 12 tool: shared list, struktur, RSA, performa iklan | Lihat §5 putaran 2 — tidak memakai kuota |
| 2026-10-14 | `get_tool_schema` 8 tool ekstensi + skill `extensions.md` | Lihat §5 putaran 3 — tidak memakai kuota |
| 2026-10-14 | `get_tool_schema` 7 tool targeting | Lihat §5 putaran 4 — tidak memakai kuota |
| 2026-10-14 | Screenshot halaman Expert Space (adspirer.ai) dari pengguna | Lihat §5 putaran 5 |
