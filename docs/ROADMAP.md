# Roadmap AdsAi

## Keputusan bisnis: Model A — Layanan Terkelola (dipilih Okt 2026)

Tim agensi yang memakai AdsAi. Klien **tidak login** ke AdsAi; klien menerima hasilnya
(laporan, alert, optimasi) sebagai bagian dari paket jasa pengelolaan iklan.

```
Klien ──(tautkan akun ke MCC agensi)──► MCC agensi ◄── AdsAi ◄── Claude (tim agensi)
                                                        │
                                                        └──► Laporan & alert ke klien (WA/email)
```

Konsekuensi:
- Satu developer token + satu login OAuth (milik agensi). Tidak perlu verifikasi OAuth publik.
- Semua akun klien wajib ditautkan ke MCC agensi.
- Kontrak jasa dengan klien perlu klausul izin akses akun iklan dan penggunaan data
  (selaras UU 27/2022 tentang Pelindungan Data Pribadi).
- Model B (SaaS, klien login sendiri) ditunda. Desain kode tetap menjaga jalur ke sana
  (data per klien terpisah, log audit), tapi tidak dibangun sekarang.

## Fase

Kode Fase 1–4 selesai dan dites dengan simulasi Google Ads. Setelah Fase 0 selesai, cukup isi `.env`.
SOP harian/mingguan tim: [OPERASIONAL.md](OPERASIONAL.md).

| Fase | Isi | Nilai jual | Status |
|---|---|---|---|
| 0 | Akses: MCC, developer token, Basic Access, OAuth | — | 📋 Menunggu pemilik/Google — [Panduan](FASE-0-AKSES.md) |
| 1 | MVP baca-saja (8 tools) | Audit cepat tanpa download CSV | ✅ |
| 2 | Tools tulis + pengaman | Tim bisa mengeksekusi optimasi dari Claude | ✅ |
| 3 | Daftar klien, laporan mingguan otomatis, alert harian | **Produk yang dijual ke klien** | ✅ |
| 4 | Analisa lanjutan (n-gram search terms, anomali, pacing) | Kualitas rekomendasi | ✅ |
| 5 | (Model B) Server online, login klien, paket & pembayaran | SaaS | 🅿️ Ditunda — [rancangan](FASE-5-SAAS.md) |

### Fase 2 — Tools tulis + pengaman
- `add_negative_keywords`, `remove_negative_keywords` (level kampanye; shared list menyusul bila dibutuhkan)
- `update_budget`, `set_status` (kampanye / grup iklan / kata kunci)
- `update_conversion_action` (Utama/Sekunder, jangka waktu, cara hitung)
- `update_bid_strategy`, `add_keywords`
- Pengaman: simulasi `validate_only` sebelum eksekusi, batas kenaikan budget,
  log audit (sebelum → sesudah) per klien, perubahan selalu butuh konfirmasi.

### Fase 3 — Produk untuk klien
- `clients.yaml`: nama klien, customer ID, kontak WA/email, target CPA, budget bulanan.
- **Alert harian** (dijalankan terjadwal): saldo/billing bermasalah, kampanye aktif tidak tayang,
  konversi nol N hari, lonjakan CPC/CPA, impor konversi gagal.
- **Laporan mingguan** per klien: ringkasan performa, perubahan yang dilakukan tim,
  rekomendasi minggu depan — Bahasa Indonesia, siap kirim.
- Pengiriman WA lewat gateway yang sudah dipakai agensi (mis. DripSender) atau WhatsApp Cloud API.

### Fase 4 — Analisa lanjutan
- N-gram search terms & usulan negative keyword otomatis
- Deteksi anomali dan proyeksi pacing budget bulanan

## Backlog dari perbandingan dengan Adspirer

Detail: [BELAJAR-DARI-ADSPIRER.md](BELAJAR-DARI-ADSPIRER.md) §4.

| Prioritas | Fitur |
|---|---|
| ✅ Selesai | Shared negative keyword list (7 tools) |
| ✅ Selesai | `campaign_structure` + audit struktur |
| ✅ Selesai | Iklan RSA: validasi, buat (dijeda), ubah, performa per iklan, status per iklan |
| ✅ Selesai | Ekstensi: sitelink, callout, snippet, telepon — tambah, ubah (atomik), jeda, lepas (8 tools) |
| ✅ Selesai | Pengaturan kampanye & targeting: jaringan, AI Max, mode lokasi, lokasi/radius, jadwal, perangkat (8 tools) |
| Sedang | Keyword research (Keyword Planner) |
| Sedang | Performance Max: asset group, search themes, sinyal audiens, search terms PMax |
| Sedang | Meta Ads (klien FB Ads) dengan pola yang sama |
| Rendah | Membuat kampanye baru (Model A: tim membuat di UI) |

## Ide paket jasa (untuk divalidasi ke klien)
| Paket | Isi |
|---|---|
| Monitoring | Alert harian WA + laporan mingguan |
| Kelola | Monitoring + optimasi mingguan oleh tim (negative keyword, bid, budget) |
| Kelola+ | Kelola + audit konversi/CRM dan rapat bulanan |
