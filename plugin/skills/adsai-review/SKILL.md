---
name: adsai-review
description: Review performa Google Ads klien dengan AdsAi — kondisi akun, scorecard mingguan/bulanan, CPA, tren, cek tracking konversi, dan laporan untuk klien. Hanya membaca; tidak mengubah apa pun.
---

# Review performa

Ikuti kontrak di `adsai-agent`. Skill ini hanya membaca.

## Urutan

1. **Klien & target**: `list_clients` (target_cpa, budget_bulanan).
2. **Kesehatan cepat**: `client_health client=<klien>` — sudah mencakup tidak tayang, konversi nol,
   anomali, CPA naik, pacing, konversi Utama berlebih, impor offline gagal.
3. **Tracking**: `conversion_actions`. Bila tracking bermasalah, jawaban jujur untuk "bagaimana iklan
   saya?" adalah *"belum bisa dinilai, ini alasannya"* — sampaikan itu sebelum menyebut CPA.
4. **Angka**: `campaign_performance` (7 hari vs 7 hari sebelumnya, atau bulan ini vs bulan lalu).
5. **Detail bila perlu**: `keywords` (Quality Score), `search_terms`, `detect_anomalies`.

## Scorecard

Biaya (dan pacing vs budget bulanan) · konversi & biaya/konversi vs target · CTR & CPC (diagnosa,
bukan tujuan) · perubahan vs periode setara.

## Sampaikan maknanya

Tabel bukan review. Mulai dengan jawaban:

> Plastik Cor bekerja: 30 lead di Rp6.600, jauh di bawah target Rp15.000. Lead-Search memakai 75% budget
> dengan CPA Rp15.600 dan 49% biayanya di "istilah lainnya". Saran: pindahkan Rp20.000/hari ke Plastik Cor.

Sebut masalah terbesar dan peluang terbesar, satu rekomendasi dengan angka. Tawarkan tindakan —
jangan jalankan. Perubahan adalah tugas `adsai-optimasi`.

## Untuk klien

- `weekly_report client=<klien>` menghasilkan teks WhatsApp/Markdown siap kirim (termasuk pekerjaan tim
  dari `change_log` dan rencana minggu depan).
- Kirim hanya lewat `send_to_client` setelah pengguna membaca isi dan penerimanya.

## Jangan

- Menyebut tren dari jendela 3 hari.
- Membandingkan minggu penuh dengan minggu yang belum selesai.
- Mengarang angka saat tool gagal.
