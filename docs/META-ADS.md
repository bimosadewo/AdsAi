# Meta Ads (Facebook & Instagram) di AdsAi

**Tahap 1: baca saja.** AdsAi bisa membaca:
- performa kampanye
- hasil: percakapan WhatsApp, prospek, pembelian, kontak
- penempatan: Facebook / Instagram / Audience Network
- umur, gender, dan wilayah

**Tahap 2: membuat kampanye dari iklan yang sudah terbukti, serta mengaktifkan/menjeda.** Aturannya sama seperti
Google Ads: pratinjau dulu (divalidasi Meta), baru dijalankan setelah disetujui. Kampanye baru **selalu dibuat dalam
keadaan dijeda**. Tahap ini butuh izin tambahan, lihat bagian *Izin untuk membuat & mengubah iklan*.

Koneksinya memakai **token System User** dari Business Manager:
- tidak terikat ke akun Facebook pribadi
- bisa dibuat tanpa kedaluwarsa
- bisa dicabut kapan saja

> ⚠️ Token ini setara kunci akun iklan. Jangan kirim lewat chat atau screenshot. Tempel langsung ke `.env` /
> Secret Manager.

Nama menu Meta sering berubah sedikit. Kalau tidak sama persis, cari menu yang paling mirip.

## Langkah 1 — Buat aplikasi Meta (sekali saja)
1. Buka <https://developers.facebook.com/apps> dan login dengan akun Facebook admin Business Manager.
2. Klik **Create App**.
3. Isi nama `AdsAi`.
4. Pilih kasus penggunaan **Create & manage ads with Marketing API**. Kalau tidak ada, pilih
   *Other → Business*.
5. Hubungkan ke **business portfolio** (Business Manager) Bapak, lalu **Create**.

## Langkah 2 — Buat System User
1. Buka <https://business.facebook.com/settings>.
2. Masuk ke **Pengguna → Pengguna sistem** (System users), lalu klik **Tambahkan**.
3. Nama `AdsAi`, peran **Karyawan** (Employee). Peran Admin tidak perlu.
4. Pilih **Tetapkan aset** (Assign assets):
   - **Akun iklan:** pilih akun iklan Bapak, izin **Lihat performa** (View performance).
   - **Aplikasi:** pilih `AdsAi`.

## Langkah 3 — Buat token
1. Masih di halaman System User `AdsAi`, klik **Buat token baru** (Generate new token).
2. Pilih aplikasi **AdsAi**.
3. Masa berlaku: **Tidak pernah** (Never).
4. Izin: centang **ads_read**. Tambahkan juga **business_management** bila diminta.
5. Klik **Buat token**, lalu salin. Token hanya tampil sekali.

## Langkah 4 — Pasang token

**Laptop (Claude Desktop):** tambahkan ke `.env` di folder AdsAi:
```
META_ACCESS_TOKEN=<tempel token>
META_DEFAULT_AD_ACCOUNT=act_<ID akun iklan>   # opsional, bila punya lebih dari satu akun
```

**Server online (Cloud Run):**
1. **Secret Manager**: buat secret `adsai-meta-token` berisi token tersebut.
2. Beri akses **Secret Accessor** ke service account Cloud Run.
3. Cloud Run → adsai → **Edit & deploy new revision**:
   - tambahkan variabel `META_ACCESS_TOKEN` dari secret `adsai-meta-token`
   - opsional: `META_DEFAULT_AD_ACCOUNT`

Lalu di Claude ketik *"cek status Meta"*. Hasil yang diharapkan: status **SIAP** dan daftar akun iklan.

## Izin untuk membuat & mengubah iklan (tahap 2)
Token yang hanya punya `ads_read` bisa membaca, tapi ditolak saat membuat kampanye. Untuk menambah izin:
1. Business Manager → **Pengguna sistem → AdsAi → Tetapkan aset**. Ubah izin akun iklan menjadi **Kelola kampanye**
   (Manage campaigns).
2. Klik **Buat token baru**, centang **ads_read** dan **ads_management**, masa berlaku *Tidak pernah*.
3. Ganti nilai token lama:
   - Laptop: ganti `META_ACCESS_TOKEN` di `.env`.
   - Server online: Secret Manager → `adsai-meta-token` → **+ New version**, lalu Cloud Run → **Edit & deploy new
     revision** → **Deploy**.

## Akun iklan klien
- Klien menambahkan Business Manager Bapak sebagai **partner** di akun iklannya. Ini dilakukan di Business Manager
  klien → Akun iklan → Tetapkan partner.
- Setelah itu, akun iklan klien bisa ditetapkan ke System User `AdsAi` (Langkah 2).
- Untuk akun iklan milik bisnis lain, Meta bisa meminta **Advanced Access** lewat *App Review* untuk `ads_read`.
  Coba dulu: kalau `meta_status` menampilkan akun klien, berarti sudah cukup. Kalau tidak, ajukan App Review.
  Saya bantu isi formulirnya.

## Tool
| Tool | Fungsi |
|---|---|
| `meta_status` | Koneksi + daftar akun iklan dan statusnya (aktif / tunggakan / ditinjau) |
| `meta_performance` | Ringkasan: biaya, jangkauan, frekuensi, hasil WA/prospek, biaya per hasil, perbandingan periode, per kampanye, porsi biaya per platform, temuan |
| `meta_insights` | Rincian: per kampanye/ad set/iklan, per umur/gender/penempatan/wilayah, per hari |
| `meta_ads` | Daftar iklan + teks + biaya per hasil (termasuk iklan lama yang diarsip) untuk mencari yang terbukti |
| `meta_create_campaign_from_proven` | Kampanye baru dari iklan terbukti. Materi dipakai ulang (postingan lama: like & komentar ikut), pengaturan WhatsApp disalin dari set iklan lama. Budget per set iklan, atur umur/gender/penempatan. **Selalu dijeda** |
| `meta_set_status` | Aktifkan/jeda kampanye, set iklan, atau iklan |

Contoh perintah:
- *"Tampilkan performa iklan Meta 30 hari terakhir."*
- *"Umur dan gender mana yang paling murah per chat WA?"*
- *"Berapa persen biaya iklan Meta tayang di Audience Network?"* AdsAi otomatis memberi peringatan bila porsinya
  ≥ 15%. Ini masalah yang sama dengan PMax kemarin yang tayang di game.

Versi API default adalah `v26.0` (Juli 2026). Meta menghentikan versi lama sekitar 2 tahun setelah rilis. Ganti
lewat variabel `META_API_VERSION` bila diperlukan.
