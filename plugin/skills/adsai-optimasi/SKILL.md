---
name: adsai-optimasi
description: Memangkas pemborosan Google Ads klien dengan AdsAi — search terms tidak relevan, kata kunci negatif, jeda yang sesempit mungkin, realokasi budget, dan pacing bulanan. Mengusulkan perubahan dengan angka dan hanya menjalankannya setelah disetujui (pratinjau → confirm).
---

# Optimasi: potong pemborosan, atur pacing

Ikuti kontrak di `adsai-agent`. Skill ini mengusulkan; perubahan dijalankan hanya setelah pengguna
menyetujui perubahan spesifik itu.

## 1. Cek dulu sebelum memotong

- **Tracking.** `conversion_actions`. Kampanye yang "tanpa konversi" sering sebenarnya berkonversi tapi
  tidak tercatat. Menjedanya bisa mematikan kampanye yang bekerja — kesalahan mahal yang sering terjadi.
- **Volume.** Belum layak disebut gagal sebelum biaya ±3× target CPA atau ±100 klik.
- **Periode.** Minimal 14–30 hari untuk search terms; 7 hari untuk tren.

## 2. Temukan bentuk pemborosannya

| Bentuk | Tool |
|---|---|
| Istilah/pola tanpa konversi | `suggest_negative_keywords` (pakai `client=` agar kata produk terlindungi), `ngram_analysis` |
| Biaya jauh di atas target CPA | `campaign_performance`, `keywords` |
| Lonjakan mendadak | `detect_anomalies` — cek dulu penjelasan membosankan: kampanye dijeda, budget diubah, iklan ditolak, landing page error, tag konversi hilang |
| Budget bulanan meleset | `budget_pacing` |

## 3. Perbaiki dengan urutan ini

1. **Negatif sebelum jeda.** `add_negative_keywords` presisi, bisa dibatalkan, dan kampanye tetap belajar.
   - PHRASE untuk pola yang berulang; EXACT untuk satu istilah spesifik.
   - Jangan pernah memblokir kata produk klien atau kata niat beli (harga, jual, distributor, terdekat).
     Bila ragu, tanya pengguna dan tambahkan kata produk ke `protected_terms` di `clients.yaml`.
2. **Jeda yang paling sempit**: kata kunci → grup iklan → kampanye (`set_status`).
3. **Realokasi** dana yang dibebaskan ke kampanye dengan CPA terbukti.

Tidak ada tool hapus permanen. Bila pengguna memintanya, arahkan ke UI dan sebutkan histori yang hilang.

## 4. Realokasi budget

- Pindahkan ke CPA terbaik, bukan volume terbesar.
- Bertahap **20–30% sekali ubah**, lalu tunggu seminggu (AdsAi juga menolak kenaikan > batas
  `ADSAI_MAX_BUDGET_INCREASE_FACTOR`).
- Tunjukkan hitungannya: *"Pindah Rp25.000/hari dari Lead-Search (CPA Rp15.600) ke Plastik Cor
  (CPA Rp6.600). Total sama; bila CPA bertahan, ±+110 lead/bulan."*

## 5. Pacing

- **Kurang belanja** biasanya karena bid/target terlalu rendah atau audiens sempit — cek pangsa tayangan
  (`campaign_performance`: `search_budget_lost_impression_share`) sebelum menaikkan budget. Bila tidak
  dibatasi budget, menambah budget tidak mengubah apa pun.
- **Boros** berarti budget habis sebelum akhir bulan: turunkan budget harian ke `saran_budget_harian`
  atau perbaiki yang mahal.

## 6. Setelah mengubah

- Laporkan `terverifikasi` dari hasil tool.
- Beri tahu yang akan terjadi: fase belajar 1–2 minggu, data belum bermakna beberapa hari.
- Jangan berjanji "saya pantau terus" — perubahan akan muncul otomatis di laporan mingguan klien
  (dari `change_log`), dan alert harian berjalan lewat `adsai alerts` terjadwal.
