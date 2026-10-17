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

### Putaran 6 (dokumentasi publik adspirer.com, via pencarian web)

Domain adspirer.com diblokir di jaringan sesi cloud; isi diambil lewat pencarian web. Semua klaim dari vendor.
- **Arsitektur:** remote MCP, satu endpoint, Streamable HTTP, **OAuth 2.1 + PKCE**. Access token platform 1 jam,
  refresh token 30 hari, diperbarui otomatis; token **terenkripsi at rest** di server, tidak pernah di sesi AI.
- **Data:** data kampanye tidak disimpan permanen (dibaca langsung dari API); percakapan tidak dicatat.
- **Integrasi pihak ketiga (GA4, Klaviyo):** token disimpan di **Pipedream** dan panggilan diproksikan ke MCP
  Pipedream — Adspirer tidak memegang kredensial mentahnya. Pola ini layak bila AdsAi menambah GA4/CRM.
- **Pengaman:** tool destruktif dikunci flag `confirm_delete`; kampanye baru dijeda.
- **Agensi:** akses lewat MCC/Business Manager; isolasi per pengguna. **Dokumen Adspirer sendiri mengakui tidak
  dirancang untuk alur pelaporan klien agensi** dan menyarankan alat dashboard untuk agensi 10+ klien.
- **Tidak ada penjadwal bawaan** — laporan rutin = prompt yang dijalankan ulang dari sisi klien AI.
- Angka tidak konsisten antar halaman (4 platform/100+ tools vs 6 platform/400+ tools).

**Implikasi untuk AdsAi:** celah yang diakui Adspirer (pelaporan agensi multi-klien, otomatis) adalah fokus
AdsAi Model A — laporan mingguan & alert WA terjadwal, `clients.yaml`, kit dossier per klien. Pola keamanan
(OAuth 2.1 + PKCE, token terenkripsi, tanpa simpan data kampanye, tanpa log percakapan) masuk rancangan Fase 5.

Sumber: developer.adspirer.com/introduction, adspirer.com/docs/introduction, adspirer.com/docs/how-it-works,
adspirer.com/docs/knowledge-base/mcp-server-comparison, adspirer.com/privacy,
adspirer.com/blog/automation-platform-for-agencies.

### Putaran 7 (seluruh dokumentasi adspirer.com + developer.adspirer.com, dibaca langsung)

Dibaca: ±100 halaman panduan/knowledge base/agent skills/platform, 178 halaman referensi API, dan changelog
(Sep 2025 – Okt 2026, ±110 entri). Semua klaim dari vendor; dokumen mereka sendiri kadang bertentangan
(mis. umur token 1 jam vs 7 hari; "tidak bisa mengubah budget" padahal ada `update_campaign`).

**Fakta penting**
- 178 tool di referensi API (Google 51 · LinkedIn 45 · Meta 36 · TikTok 29 · monitoring 13 · umum 3 · audit 1);
  halaman lain menyebut 400+ tool / 156 Google. Tool tulis **tidak punya flag confirm/dry-run** — keamanan
  hanya lewat teks deskripsi ("Confirm with user BEFORE"), `confirm_delete` untuk hapus, dan antrean persetujuan
  (`manage_action`, kedaluwarsa 48 jam). Pola AdsAi (pratinjau tervalidasi Google → confirm) lebih ketat.
- 9 tool diagnosis gratis: `start_here`, `suggest_next_action`, `list_what_i_can_do`, `get_campaign_spec`,
  `validate_campaign_draft`, `explain_platform_error`, `why_did_this_fail`, `verify_campaign_is_live`,
  `usage_value_summary`. Empat tool ditarik 24 Agu 2026 karena salah melapor — "diagnosis salah lebih buruk
  dari tidak ada".
- STRATEGY.md: `AVOID / PREFER / CONSTRAINT / REQUIRE: [teks] — [alasan]`, dikelompokkan per bagian, plus
  Decision Log bertanggal dan Archived Directives. Disimpan hanya setelah pengguna mengonfirmasi; percakapan
  eksplorasi tidak pernah disimpan. Semua skill membacanya dulu; pelanggaran = "Strategy Drift".
- Kit Playbook: `SKILL.md`, `AGENTS.md` (satu-satunya instruksi; `AGENT_INSTRUCTIONS.md` dibuang karena
  duplikat), `PROMPTBOOK.md` ("menu, bukan naskah"), `README.md`, `references/account-profile.md`,
  `references/working-with-adspirer.md`. Pembuatannya mengklasifikasi akun: scaling / stalled / newly built /
  seasonal. Aturan pasang: instruksi = AGENTS.md, pengetahuan = account-profile.md.
- Changelog — pelajaran Google Ads yang mahal: tulis yang "berhasil" padahal tidak berubah (bid strategy,
  CPC ceiling 191 panggilan, budget) → wajib baca ulang; field konversi yang immutable menggagalkan seluruh
  update atomik; strategi portofolio → `OPERATION_NOT_PERMITTED_FOR_CONTEXT`; kampanye AI Max broad-only
  menolak EXACT/PHRASE; ekstensi ada di 3 tingkat (akun/kampanye/grup — 609 cek salah "Sitelinks (0)");
  aset telepon tidak pernah dideduplikasi Google; `CONCURRENT_MODIFICATION` perlu dicoba ulang; audit dengan
  MCC salah sempat memberi "Grade A" pada akun yang tidak terbaca (kini "Incomplete"); IDR mata uang tanpa
  desimal (budget Meta sempat 100× terlalu besar).
- Otomasi: Watch Agent tiap 15 menit, monitor (`consecutive_days` 1–14, `test_monitor`), brief terjadwal,
  agen Ad Ops dengan antrean persetujuan; panduan otomasi: mode *shadow*, maks ±20% per putaran, satu penulis
  per portofolio, potong 1–2 hari terakhir karena jeda atribusi.
- Kuota: Free 15 · Plus 150 ($49) · Pro 600 ($99) · Max 3.000 ($199) panggilan/bulan; peluncuran satu
  kampanye ±8–12 panggilan; Playbook $39/akun.
- Celah Adspirer untuk pasar kita: `research_keywords` default "United States" tanpa parameter bahasa;
  beberapa tool menyebut nominal dalam "dollar"; zona waktu brief default America/New_York.

**Diterapkan ke AdsAi (putaran ini)**
- Kit dossier mengikuti tata letak di atas (README + `references/`, AGENT_INSTRUCTIONS.md dihapus otomatis,
  PROMPTBOOK sebagai menu) + **klasifikasi jenis akun** MACET / BARU / BERTUMBUH / STABIL dengan prioritas &
  ritme kerja (musiman ditandai tim lewat arahan strategi).
- **Strategi klien** `strategi/<klien>.md` (format STRATEGY.md): tool `client_strategy` &
  `add_strategy_directive` (pratinjau → confirm, Decision Log otomatis). Istilah `PREFER "…"` dilindungi dari
  usulan negatif; istilah `AVOID "…"` yang masih memakan biaya dilaporkan sebagai penyimpangan strategi di
  `suggest_negative_keywords`, dossier, dan `suggest_next_action`.
- **Penerjemah error** (`errors.py`, 30+ kode yang divalidasi terhadap enum API v25) → setiap error membawa
  `Saran:`; error sementara dicoba ulang otomatis (1 dtk, 3 dtk). **Log kegagalan** + `why_did_this_fail`,
  `explain_error`.
- `suggest_next_action` (setara start_here per klien) dan `verify_campaign_is_live` (data tak terbaca tidak
  pernah dinyatakan lolos).
- `update_keyword_bids` (maks 2×, peringatan Smart Bidding) dan `remove_keywords` dengan gerbang
  `confirm_delete`; `set_status` kata kunci kini dibaca ulang.

**Backlog baru** (lihat ROADMAP): keyword research Indonesia, monitor + antrean persetujuan, ekstensi tingkat
akun, deteksi broad-only/portofolio sebelum menulis, audit tracking berskor A–F.

### Putaran 8 (paket Plus, uji langsung di akun Tiga Pilar — 15 Okt 2026)

- **Kuota:** Plus = 150 panggilan/bulan (terpakai 10 setelah upgrade). `get_tool_schema`, `search_tools`,
  `get_usage_status` tidak memotong kuota.
- **Dashboard `get_campaign_performance`:** sumber data = *cache* malam hari (`freshness.source_mix: cache`).
  Diminta `last_7_days`, jawaban berlabel "30 hari" padahal cache baru berisi **1 hari** (14 Okt) → total
  terlihat seperti 30 hari padahal 1 hari. Rekomendasi generik ("Continue monitoring…"). Yang bagus: blok
  `freshness` (last_data_date, as_of_days_ago, partial_last_day), `budget_lost_is` per kampanye,
  keyword bermasalah (biaya tanpa konversi), ad group terlemah.
  → Pelajaran AdsAi: AdsAi membaca API langsung (tidak basi), tetapi tetap harus **menyebut rentang data yang
  benar-benar ada** dan menandai hari ini sebagai belum lengkap.
- **Watch Agent (`run_watch_now`):** "service unreachable" saat dicoba — fitur unggulan belum bisa diandalkan.
- **Monitor:** satu kondisi saja (metric + greater_than/less_than + threshold + `consecutive_days` 1–14),
  dicek harian, maks 5 monitor aktif di paket berbayar, kirim email digest. Tidak ada AND/OR, tidak ada
  ambang relatif. `test_monitor` = uji kering; hasilnya **mengabaikan `consecutive_days`** (dengan data 1 hari
  sudah dinyatakan "would trigger" untuk syarat 3 hari).
  → AdsAi sudah setara/lebih: `adsai alerts` mengevaluasi konversi-nol beruntun, CPA naik, anomali (z-score),
  pacing, tidak tayang, iklan ditolak — sekaligus, per klien, dengan ambang di `clients.yaml`, dan kirim WA.
- **Auto-action:** monitor bisa mengantre aksi (pause/budget) yang menunggu persetujuan, kedaluwarsa 48 jam
  (`list_pending_actions` → `manage_action`). Pola antrean persetujuan ini masih backlog AdsAi.
- **Laporan:** `schedule_brief` (default zona waktu America/New_York — harus diisi Asia/Jakarta) dan
  `generate_report_now` (PDF/HTML/JSON, async 2–5 menit, email/Slack/webhook). AdsAi: laporan WA/email
  berbahasa Indonesia lewat `adsai report --send` terjadwal.

- **Mengubah konversi lewat Adspirer (uji 15 Okt):**
  - `update_conversion_action` mendokumentasikan `status: PAUSED`, padahal Google Ads API tidak punya status
    itu untuk konversi (hanya ENABLED/HIDDEN/REMOVED) → gagal "Failed to complete operation: PAUSED".
  - Konversi `WEBPAGE_CODELESS` ("Add to basket (Page load …)") ditolak: *Mutates are not allowed* — Adspirer
    baru tahu setelah mencoba; tidak ada pratinjau.
  - ContactWA & PurchaseWA → Sekunder **berhasil**, tetapi `list_conversion_actions` sesudahnya masih
    menampilkan nilai lama karena **cache Redis 1 jam** — klaim "read-back" mereka tidak berlaku di sini.
  - `click_through_lookback_window_days` dibatasi 1–30, padahal Google mengizinkan sampai 90 (akun ini memakai 90).
  → AdsAi sudah lebih aman di ketiga titik: pratinjau `validate_only` menangkap penolakan sebelum eksekusi,
  status yang ditawarkan hanya ENABLED/HIDDEN, dan verifikasi membaca langsung dari API (tanpa cache).

- **Riset kata kunci untuk Geopipe (uji 15 Okt):** `research_keywords` dengan lokasi "Indonesia" + bahasa "id":
  percobaan 1 (8 seed spesifik) → hanya "geopipe" (320/bln, CPC ±Rp20.809); 7 seed lain hilang tanpa
  penjelasan; rekomendasi budget Rp1.040.451/hari (50 klik × CPC) — tidak realistis untuk UKM.
  Percobaan 2 (seed umum "pipa hdpe", "pipa drainase", …) → `returned_by_google: 0`. Riset mereka tidak
  andal untuk Indonesia/B2B niche.
  → **Diterapkan:** AdsAi `research_keywords` sendiri (geo 2360 + bahasa 1025 eksplisit, metrik historis untuk
  setiap seed, tanpa ambang volume, niat dari persentil bid + kata beli/informasi, budget 10/20/40 klik).

- **Review performa 16 Okt (skill `adspirer-performance-review`):** urutannya — audit tracking dulu →
  scorecard periode setara → mulai dari kesimpulan, 1 masalah + 1 peluang + 1 rekomendasi berangka →
  tawarkan artifact visual → tawarkan jadwal mingguan. Kartu dashboard dirender host (MCP Apps), bukan
  isi datanya yang lebih kaya.
  - `get_campaign_performance` kemarin: Rp79.276, 19 klik, CTR 4,5%, **2 konversi** (Rp39.638/konv.).
    Rekomendasinya keliru: "ROAS 0.0x … confirmed loss" untuk akun prospek (nilai konversi 0/1).
  - `audit_conversion_tracking` memberi **A (96,7)** padahal: "Add to basket (Page load)" berstatus Utama
    dinilai PASS; status tag "Perlu diperhatikan" tidak diperiksa; G-CT-01 menyebut 2 konversi 7 hari
    sementara G-CT-06 menyebut "current 7d: 0" (tidak konsisten).
  → Diterapkan: skill `adsai-review` (+artifact, +jadwal, +aturan ROAS akun prospek, +jangan percaya skor
  audit mentah). Backlog: tool audit tracking AdsAi berskor yang memeriksa aksi ringan Utama & status tag.

Monitor uji yang dibuat: "Tiga Pilar - Konversi 0 selama 3 hari" (2 kampanye Search).

## 6. Log observasi (tambahkan setiap sesi)

| Tanggal | Yang dicoba di Adspirer | Pola/pelajaran |
|---|---|---|
| 2026-10-14 | Audit konversi, list kampanye & konversi, 2× negatif, 3× ubah kampanye, 6× ubah konversi | Lihat §3 |
| 2026-10-14 | `search_tools` (gratis) + `get_tool_schema` 12 tool: shared list, struktur, RSA, performa iklan | Lihat §5 putaran 2 — tidak memakai kuota |
| 2026-10-14 | `get_tool_schema` 8 tool ekstensi + skill `extensions.md` | Lihat §5 putaran 3 — tidak memakai kuota |
| 2026-10-14 | `get_tool_schema` 7 tool targeting | Lihat §5 putaran 4 — tidak memakai kuota |
| 2026-10-14 | Screenshot halaman Expert Space (adspirer.ai) dari pengguna | Lihat §5 putaran 5 |
| 2026-10-14 | Dokumentasi publik adspirer.com (via pencarian web) | Lihat §5 putaran 6 |
| 2026-10-14 | Seluruh dokumentasi publik (panduan, KB, 178 referensi API, changelog) dibaca langsung | Lihat §5 putaran 7 |
| 2026-10-15 | Paket Plus: dashboard, Watch Agent (gagal: unreachable), create_monitor + test_monitor | Lihat §5 putaran 8 |
