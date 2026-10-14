---
name: adsai-iklan
description: Menulis, memeriksa, dan memperbarui iklan Responsive Search Ad (RSA) klien dengan AdsAi — dari search terms nyata, validasi panjang/kebijakan, pratinjau, lalu buat (dijeda) atau ubah. Pakai untuk "buat iklan baru", "perbaiki headline", "ad strength jelek", "iklan ditolak".
---

# Iklan RSA: tulis dari bukti, validasi, baru kirim

Ikuti kontrak di `adsai-agent`.

## 1. Kumpulkan bukti dulu

- `campaign_structure` (include=ads) → iklan yang ada, ad strength, status persetujuan, ID.
- `search_terms` / `ngram_analysis` → kata yang benar-benar diketik pembeli. Kata-kata itu masuk ke iklan.
- `ad_performance` → iklan mana dengan CTR/CPA terbaik dan kenapa.
- Landing page: iklan tidak boleh menjanjikan sesuatu yang tidak ada di halaman.
- Tanyakan keunggulan nyata klien bila belum jelas (stok, harga pabrik, pengiriman, sertifikat, garansi).

## 2. Tulis

- **Headline 10–15** (≤30 karakter), **deskripsi 4** (≤90). Variasikan sudut pandang:
  apa produknya · harga/penawaran · kecepatan/stok · bukti (pengalaman, klien, sertifikat) ·
  jaminan/risiko · ajakan (Minta Penawaran, Chat WhatsApp).
- **Satu ide per headline** — Google memutar headline secara terpisah; tiap headline harus masuk akal sendiri.
- **Spesifik > kata sifat**: "Kirim H+1 Jabodetabek" lebih kuat dari "Pengiriman Cepat".
- Sesuaikan dengan niat pencarian: pencari "harga ... per m2" ingin harga & ukuran, bukan sejarah perusahaan.
- **Dilarang**: tanda seru di headline, HURUF KAPITAL berlebihan, klaim "terbaik / nomor 1 / termurah"
  tanpa bukti di landing page.
- Pin hanya bila wajib (mis. nama merek di posisi 1); pin berlebihan menurunkan ad strength.
- Path: kata kunci utama, tanpa spasi (mis. `geotextile` / `non-woven`).

## 3. Validasi tanpa mengirim

`validate_ad_copy` → perbaiki semua `error`, timbang `peringatan`. Tampilkan ke pengguna dalam tabel:
teks · jumlah karakter · sudut pandang.

## 4. Kirim

- Iklan baru: `create_responsive_search_ad` (confirm=false → setuju → confirm=true). Dibuat **DIJEDA**;
  aktifkan dengan `set_status level="ad" ids=["<ad_group_id>~<ad_id>"]` setelah pengguna setuju.
- Ubah iklan lama: `update_responsive_search_ad` — daftar yang dikirim **mengganti** daftar lama, jadi
  kirim daftar lengkap. Lebih aman untuk eksperimen: buat iklan baru, biarkan keduanya jalan 2–4 minggu,
  lalu jeda yang kalah (`ad_performance`).
- Iklan baru/diubah ditinjau Google (biasanya < 1 hari, bisa sampai 48 jam).

## 5. Iklan ditolak

`campaign_structure` menandai `persetujuan: DISAPPROVED`. Alasan detail kebijakan ada di UI Google Ads
(Iklan → kolom Status). Perbaiki teks/landing page, lalu `update_responsive_search_ad`.
