# Fase 1 — Google Business Profile (profil Google Maps)

Tujuan: semua profil cabang dikelola dari AdsAi lewat chat:
- audit kelengkapan
- promo serentak
- balas ulasan
- jam libur serentak

Diuji dulu di Tiga Pilar, lalu dipakai untuk RS Sari Asih.

Kode AdsAi **sudah siap** (lihat bagian terakhir). Yang tersisa adalah **izin dari Google**, dan itu harus diurus
pemilik akun. Langkah 1–3 dikerjakan sekarang. Langkah 4–6 dikerjakan setelah Google menyetujui.

> ⚠️ Jangan kirim client secret / refresh token lewat chat. Semua nilai rahasia ditulis langsung ke `.env` /
> Secret Manager.

---

## Langkah 1 — Cek syarat (5 menit)

Google hanya menyetujui pemohon yang:

1. **Mengelola profil bisnis yang terverifikasi dan aktif minimal 60 hari.** Profil Tiga Pilar memenuhi syarat
   kalau sudah terverifikasi lebih dari 2 bulan.
2. **Punya website yang mewakili bisnis di profil itu.** Pastikan kolom *Situs web* di profil Tiga Pilar berisi
   `https://www.tigapilar.co.id`.
3. **Mengirim formulir dari email yang menjadi Pemilik/Pengelola profil tersebut.** Cek di
   <https://business.google.com>: buka profil → menu ⋮ → **Setelan profil bisnis → Orang dan akses**.
   `budi3pilar@gmail.com` harus ada di sana sebagai *Pemilik* atau *Pengelola*.

Saran Google: lengkapi dulu profilnya sebelum mengajukan, supaya peninjauan lancar. Yang perlu lengkap: jam buka,
kategori, deskripsi, foto, dan website.

## Langkah 2 — (Disarankan) Akun Organisasi untuk agensi

Google juga mencantumkan *Organization account* di daftar prasyaratnya. Akun ini untuk agensi yang mengelola
profil banyak klien. Cara membuatnya ada di pusat bantuan:
<https://support.google.com/business/answer/7663063>. Buat dengan `budi3pilar@gmail.com`.

## Langkah 3 — Kirim formulir permohonan akses

1. Catat **Project number**: `654186807798` (project `adsai-511706`). Ini project yang sama dengan Google Ads API,
   jadi OAuth client-nya juga sama.
2. Buka formulir: <https://support.google.com/business/contact/api_default>
   (login sebagai `budi3pilar@gmail.com`).
3. Pilih **"Application for Basic API Access"** di menu tarik-turun.
4. Contoh isian. Formulirnya berbahasa Inggris; sesuaikan dengan kondisi sebenarnya:

   | Kolom | Isi |
   |---|---|
   | Company name | PT Tiga Pilar (dan nama agensi Bapak, bila beda) |
   | Company website | https://www.tigapilar.co.id |
   | Google Cloud project number | 654186807798 |
   | Business Profile yang dikelola | Profil Tiga Pilar (link Maps) |
   | Use case | lihat teks di bawah |

   Use case (salin & sesuaikan):

   > We are a local business and digital marketing agency in Indonesia. We manage our own Business Profiles
   > (PT Tiga Pilar, construction materials supplier) and, with explicit permission, profiles of our clients
   > (e.g. a multi-branch hospital). We are building an internal tool to: (1) audit profile completeness across
   > locations, (2) publish the same update or offer to many locations at once with per-location text,
   > (3) read reviews and reply after a human approves each reply, and (4) set special (holiday) hours across
   > locations. Every change is previewed and approved by a person before it is sent. The tool is used only by our
   > staff, not resold, and we do not scrape or collect data of businesses we do not manage.

5. Kirim. Google akan membalas lewat email. Menurut sumber pihak ketiga, prosesnya biasanya beberapa hari sampai
   ±2 minggu.

**Cara cek apakah sudah disetujui** (sesuai dokumentasi Google):
1. Buka Cloud Console → **APIs & Services → Library**.
2. Cari **My Business Business Information API** → **Quotas**.
3. Baca angkanya:
   - **0 QPM** = belum disetujui.
   - **300 QPM** = sudah disetujui.

Bisa juga cukup minta di Claude: *"cek status GBP"*. Tool `gbp_status` akan bilang "kuota masih 0 → belum
disetujui".

Kalau ditolak: lengkapi syarat di Langkah 1, lalu ajukan ulang.

---

## Langkah 4 — Setelah disetujui: aktifkan API

Cloud Console → **APIs & Services → Library**, aktifkan ketujuh API ini (Enable):

1. Google My Business API (hanya muncul setelah disetujui)
2. My Business Account Management API
3. My Business Business Information API
4. My Business Lodging API
5. My Business Place Actions API
6. My Business Notifications API
7. My Business Verifications API

Lalu buka **Google Auth Platform → Data Access → Add or remove scopes**, tambahkan
`https://www.googleapis.com/auth/business.manage`, dan simpan.

## Langkah 5 — Buat token GBP

Di laptop, di folder AdsAi. Pakai `client_secret.json` yang sama dengan Google Ads:

```bash
python scripts/generate_refresh_token.py --gbp
```

- Login dengan `budi3pilar@gmail.com` → **Izinkan**.
- Hanya `GBP_REFRESH_TOKEN` yang ditulis ke `.env`. Token Google Ads tidak disentuh.
- Untuk server lokal (Claude Desktop): mulai ulang Claude Desktop.

## Langkah 6 — Pasang di server online (Cloud Run)

1. **Secret Manager → Create secret**:
   - nama `adsai-gbp-refresh-token`
   - isi: nilai `GBP_REFRESH_TOKEN` dari `.env` laptop (salin langsung, tanpa spasi atau baris baru)
2. Beri akses **Secret Manager Secret Accessor** ke service account Cloud Run. Caranya sama dengan secret lain di
   [SERVER-ONLINE.md](SERVER-ONLINE.md).
3. **Cloud Run → adsai → Edit & deploy new revision → Variables & secrets**:
   - tambahkan `GBP_REFRESH_TOKEN`
   - sumbernya secret `adsai-gbp-refresh-token`, versi latest
4. Di Claude, ketik *"cek status GBP"*. Hasil yang diharapkan: status **SIAP** dan daftar akun/grup lokasi.

---

## Menjadikan agensi Pengelola profil (t102, t103)

AdsAi hanya bisa mengelola profil yang memberi akses ke `budi3pilar@gmail.com`.

- **Tiga Pilar (2 lokasi):** di business.google.com → profil → ⋮ → **Setelan profil bisnis → Orang dan akses →
  Tambahkan** → `budi3pilar@gmail.com` → peran **Pengelola**. Ulangi untuk lokasi kedua. Lewati kalau email ini
  sudah Pemilik.
- **RS Sari Asih (banyak cabang):** pihak RS (pemilik profil) melakukan hal yang sama untuk tiap cabang. Kalau
  cabang-cabangnya sudah dikumpulkan dalam satu **grup lokasi**, cukup undang email agensi ke grup itu. Semua
  cabang langsung ikut, dan di AdsAi grup itu muncul sebagai satu "akun" (`gbp_status`).
- Minta peran **Pengelola**, bukan Pemilik. Pengelola cukup untuk postingan, ulasan, dan jam khusus, dan pihak RS
  tetap pegang kendali penuh.

**Khusus layanan kesehatan:** setiap promo atau postingan RS sebaiknya disetujui humas/legal RS lebih dulu.
- Hindari klaim kesembuhan dan janji hasil.
- Saat membalas ulasan, jangan pernah membenarkan bahwa pengulas adalah pasien. Panduan ini sudah tertanam di
  `gbp_reviews`.

---

## Yang sudah dibangun di AdsAi

| Tool | Fungsi | Mengubah profil? |
|---|---|---|
| `gbp_status` | Cek kesiapan akses + daftar akun/grup lokasi | Tidak |
| `gbp_locations` | Daftar cabang + **audit kelengkapan** (skor 0–100 per cabang, apa yang kurang) | Tidak |
| `gbp_reviews` | Ulasan per cabang (default: yang belum dibalas), rata-rata bintang, prioritas bintang 1–2 | Tidak |
| `gbp_reply_review` | Balas/ganti balasan ulasan | Ya — pratinjau dulu |
| `gbp_post` | Berita/promo/acara **serentak** ke banyak cabang, teks per cabang (`{nama}`, `{kota}`, `{alamat}`, `{telepon}`, `{website}`, `{kode}`) | Ya — pratinjau dulu |
| `gbp_special_hours` | Jam libur/jam khusus **serentak**. Jam khusus lama tetap dipertahankan. Pratinjau divalidasi Google | Ya — pratinjau dulu |

Semua perubahan:
- mengikuti mode baca-saja (`ADSAI_READ_ONLY`)
- tercatat di log audit `logs/gbp-<akun>.jsonl`
- bisa diusulkan lewat antrean persetujuan (`propose_change`)

Kalau satu cabang gagal, cabang lain tetap jalan. Hasilnya dilaporkan per cabang.

Contoh perintah di Claude setelah aktif:
- *"Audit profil Google semua cabang RS Sari Asih."*
- *"Tampilkan ulasan bintang 1–2 yang belum dibalas, buatkan draf balasannya."*
- *"Pasang tutup tanggal 25 Desember di semua cabang."*
- *"Posting promo cek kesehatan Rp199rb, berlaku 1–30 Nov, tombol Booking ke halaman promo, di semua cabang."*
