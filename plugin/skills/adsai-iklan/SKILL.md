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

## 6. Ekstensi (sitelink, callout, snippet, telepon)

Ekstensi gratis, menaikkan CTR, dan memengaruhi Ad Rank. **Kampanye Search belum selesai tanpa ekstensi.**
`campaign_structure` menandai kekurangannya; `list_extensions` menampilkan yang terpasang beserta `asset_id`.

| Ekstensi | Tool | Minimum | Aturan |
|---|---|---|---|
| Sitelink | `add_sitelinks` | 4 | Teks ≤25, deskripsi ≤35 (berpasangan), tiap sitelink ke halaman berbeda |
| Callout | `add_callouts` | 4–6 | ≤25, fakta/keunggulan, bukan ajakan, tanpa titik/tanda seru |
| Structured snippet | `add_structured_snippets` | 1 header | Header resmi (Jenis, Merek, Layanan, Model, …), 3–10 nilai ≤25 |
| Telepon | `add_call_extension` | — | Wajib untuk lead B2B/lokal; pasang `conversion_action_id` agar panggilan terhitung |

- **Sitelink jangan mengulang iklan.** Bila headline sudah "Jual Geotextile", sitelink sebaiknya Harga, Minta
  Penawaran, Spesifikasi Produk, Proyek Kami — langkah berikut pembeli yang siap. Halaman tujuannya harus ada
  dan sesuai label.
- **Callout bukan slogan.** "Kualitas Terpercaya" tidak berarti apa-apa; "Stok Ready Surabaya", "Harga Pabrik",
  "Bisa Potong Ukuran" adalah alasan memilih.
- **Snippet butuh kategori nyata** — jangan mencampur hal yang tidak sejenis dalam satu header.
- Grup iklan bertema berbeda boleh punya sitelink/callout sendiri (`ad_group_id`); ekstensi grup iklan
  mengalahkan ekstensi kampanye untuk grup itu.
- **Mengubah** = `update_extension` (aset baru dibuat & ditautkan, tautan lama dilepas, atomik).
  **Jeda sementara** = `set_extension_status` (mis. telepon saat kantor libur). **Lepas** = `remove_extensions`
  (aset tetap di library). Utamakan mengganti daripada melepas tanpa pengganti.
