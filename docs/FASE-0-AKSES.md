# Fase 0 — Menyiapkan Akses Google Ads API

Langkah ini **harus dikerjakan pemilik akun** (butuh login Google dan data bisnis).
> **Perubahan Google per 9 Sep 2026:** developer token tidak lagi menentukan akses (header-nya diabaikan).
> Tingkat akses (Test / Explorer / Basic / Standard) kini melekat pada **project Google Cloud** pemilik
> OAuth client, dan pengajuan Basic Access dilakukan di **Cloud Console**, bukan di Pusat API MCC.
> Formulir Pusat API di MCC sekarang hanya untuk *App Conversion Tracking and Remarketing API* — **jangan dipakai**.

Hasil akhirnya adalah 4 nilai di file `.env`:

| Variabel | Didapat dari |
|---|---|
| `GOOGLE_ADS_CLIENT_ID` | Langkah 3 (Google Cloud) |
| `GOOGLE_ADS_CLIENT_SECRET` | Langkah 3 (Google Cloud) |
| `GOOGLE_ADS_REFRESH_TOKEN` | Langkah 5 (script) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | Langkah 1 (ID akun MCC, bukan rahasia) |

`GOOGLE_ADS_DEVELOPER_TOKEN` opsional — boleh dikosongkan.

> ⚠️ Jangan pernah mengirim client secret / refresh token lewat chat, email, atau commit ke GitHub.
> File `.env` dan `client_secret*.json` sudah di-`.gitignore`.

---

## Langkah 1 — Buat akun Manager (MCC)

1. Buka <https://ads.google.com/home/tools/manager-accounts/> → **Buat akun pengelola**.
2. Login dengan akun Google yang akan dipakai untuk mengelola semua klien.
3. Isi nama (mis. *Kreasi Ads Manager*), pilih negara **Indonesia**, zona waktu **(GMT+07:00) Jakarta**, mata uang **IDR**.
   Untuk "Bagaimana Anda akan menggunakan akun ini?" pilih **Untuk mengelola akun orang lain**.
4. Setelah jadi, catat **ID akun MCC** di pojok kanan atas (format `123-456-7890`).
   → Ini nilai `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (tulis tanpa strip).
5. Tautkan akun klien: di MCC buka **Akun → Sub-akun → tombol + → Tautkan akun yang sudah ada**,
   masukkan ID akun klien (mis. Tiga Pilar `582-757-2362`) → **Kirim permintaan**.
6. Login ke akun klien → **Admin → Akses dan keamanan → tab Pengelola** → **Terima** permintaan.

## Langkah 2 — Project Google Cloud + aktifkan Google Ads API

1. Buka <https://console.cloud.google.com/> (login budi3pilar@gmail.com) → **Select a project → New project**
   → nama `adsai` → **Create**.
2. **Billing**: tautkan akun penagihan ke project (Billing → Link a billing account). Project tanpa penagihan
   atau masih *Free Trial* bisa ditolak untuk Explorer/Basic. Google Ads API sendiri **gratis**; kartu hanya
   untuk verifikasi.
3. **APIs & Services → Library** → cari **Google Ads API** → **Enable**.

## Langkah 3 — Layar persetujuan OAuth, verifikasi merek, dan OAuth client

1. **Google Auth Platform** (APIs & Services → OAuth consent screen):
   - **Branding**: nama aplikasi `AdsAi`, email dukungan, logo (opsional), **homepage https://tigapilar.co.id**,
     link kebijakan privasi (halaman di situs Anda), domain resmi `tigapilar.co.id`.
   - **Audience**: User type **External** → tambahkan budi3pilar@gmail.com sebagai **Test user**.
   - Domain harus diverifikasi lewat **Google Search Console** dengan akun yang sama (biasanya sudah ada bila
     situs terdaftar di Search Console).
   - Klik **Verify branding / Submit for verification**. Ini **verifikasi merek** yang wajib sebelum Basic Access.
2. **Clients → Create client** → Application type **Desktop app** → nama `AdsAi Desktop` → **Create**
   → **Download JSON** → simpan sebagai `client_secret.json` di folder proyek (jangan dibagikan).

## Langkah 4 — Ajukan Basic Access (di Cloud Console)

1. **APIs & Services → Enabled APIs → Google Ads API** → halaman **Overview** (Google Ads API Overview).
2. Buka bagian **Upgrade access level** → **Apply for access** → pilih **Basic**.
   Butuh peran Owner / Editor di project.
3. Jawaban formulir (sesuaikan, harus konsisten dengan situs):
   - *Business model*: perusahaan/agensi yang mengelola Google Ads untuk akun sendiri dan klien yang tertaut
     di MCC 221-446-0264.
   - *Tool usage*: tool internal untuk membaca laporan performa, audit konversi, dan mengelola kata kunci
     negatif/budget/iklan. Tidak dijual ke pihak lain; hanya dipakai tim internal.
4. Menurut Google, pengajuan Basic **ditinjau otomatis dalam hitungan menit** setelah verifikasi merek selesai.
   Sebelum disetujui, project ada di level **Test** (hanya akun uji) — error `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`.

## Langkah 5 — Buat Refresh Token

Di komputer Anda (butuh Python 3.10+):

```bash
git clone https://github.com/bimosadewo/AdsAi.git
cd AdsAi
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e .
python scripts/generate_refresh_token.py --login-customer-id 221-446-0264 --default-customer-id 582-757-2362
```

Browser akan terbuka → login dengan akun Google yang punya akses MCC → **Izinkan**.
Kalau muncul "Google hasn't verified this app", klik **Lanjutkan** (aplikasi Anda sendiri).
Script menulis `CLIENT_ID`, `CLIENT_SECRET`, `REFRESH_TOKEN`, ID MCC, dan akun default **langsung ke `.env`**
(tidak dicetak ke layar). Status OAuth harus **In production** — bila masih *Testing*, refresh token kedaluwarsa 7 hari.

## Langkah 6 — Isi `.env` dan uji

```bash
cp .env.example .env      # Windows: copy .env.example .env
# isi keempat nilai di .env, plus GOOGLE_ADS_DEFAULT_CUSTOMER_ID=5827572362 (opsional)
```

Lanjut ke bagian **"Pasang di Claude Desktop"** di README.

---

### Checklist

- [x] Akun MCC dibuat — TigapilarAdsAi `221-446-0264`
- [x] PT Tiga PILAR `582-757-2362` tertaut ke MCC (Aktif)
- [ ] Project Google Cloud + penagihan + Google Ads API di-enable
- [ ] Branding OAuth + verifikasi merek
- [ ] OAuth client (Desktop) dibuat, `client_secret.json` di-download
- [ ] Basic Access diajukan & disetujui (Cloud Console)
- [ ] Refresh token dibuat
- [ ] `.env` terisi
