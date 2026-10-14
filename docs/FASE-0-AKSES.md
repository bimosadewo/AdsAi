# Fase 0 — Menyiapkan Akses Google Ads API

Langkah ini **harus dikerjakan pemilik akun** (butuh login Google dan data bisnis).
Hasil akhirnya adalah 5 nilai rahasia di file `.env`:

| Variabel | Didapat dari |
|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Langkah 2 (Pusat API di MCC) |
| `GOOGLE_ADS_CLIENT_ID` | Langkah 4 (Google Cloud) |
| `GOOGLE_ADS_CLIENT_SECRET` | Langkah 4 (Google Cloud) |
| `GOOGLE_ADS_REFRESH_TOKEN` | Langkah 5 (script) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | Langkah 1 (ID akun MCC) |

> ⚠️ Jangan pernah mengirim nilai-nilai ini lewat chat, email, atau commit ke GitHub.
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

## Langkah 2 — Ajukan Developer Token

1. Masuk ke **akun MCC** (bukan akun klien).
2. Menu **Admin → Pusat API** (API Center).
   Kalau menu ini tidak muncul, pastikan Anda sedang berada di akun MCC.
3. Isi formulir: nama perusahaan, URL situs, email kontak API, centang persetujuan Ketentuan API → **Kirim**.
4. Token langsung muncul dengan level **Test Account / Explorer**.
   Salin → nilai `GOOGLE_ADS_DEVELOPER_TOKEN`.

## Langkah 3 — Ajukan Basic Access (agar bisa dipakai di akun sungguhan)

Token level Test hanya bisa membaca **akun test**. Untuk akun klien sungguhan:

1. Di **Pusat API**, klik **Ajukan Akses Dasar (Apply for Basic Access)**.
2. Contoh jawaban untuk formulir (sesuaikan):
   - *Business model*: Agensi digital marketing yang mengelola Google Ads untuk klien sendiri.
   - *Tool usage*: Tool internal untuk membaca laporan performa, audit konversi, dan mengelola
     kata kunci negatif/budget akun klien yang tertaut di MCC kami. Tidak dijual ke pihak lain.
   - *Who uses it*: Tim internal agensi saja.
   - *Design document*: boleh lampirkan README repo ini (export PDF).
3. Tunggu email dari Google (biasanya 3–7 hari kerja). Selama menunggu, Fase 1 tetap bisa
   dites dengan **akun test** (buat di <https://ads.google.com/aw/overview> dengan MCC test).

## Langkah 4 — Buat OAuth Client di Google Cloud

1. Buka <https://console.cloud.google.com/> → pilih/buat project baru (mis. `adsai`).
2. **APIs & Services → Library** → cari **Google Ads API** → **Enable**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type: **External** → isi nama aplikasi (`AdsAi`), email support, email developer → simpan.
   - Bagian **Audience / Test users**: tambahkan email Google yang dipakai login Google Ads.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Desktop app** → nama `AdsAi Desktop` → **Create**.
   - Klik **Download JSON** → simpan sebagai `client_secret.json` di folder proyek ini.

## Langkah 5 — Buat Refresh Token

Di komputer Anda (butuh Python 3.10+):

```bash
git clone https://github.com/bimosadewo/AdsAi.git
cd AdsAi
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e .
python scripts/generate_refresh_token.py --client-secrets client_secret.json
```

Browser akan terbuka → login dengan akun Google yang punya akses MCC → **Izinkan**.
Kalau muncul "Google hasn't verified this app", klik **Lanjutkan** (aplikasi Anda sendiri).
Script mencetak `CLIENT_ID`, `CLIENT_SECRET`, dan `REFRESH_TOKEN`.

## Langkah 6 — Isi `.env` dan uji

```bash
cp .env.example .env      # Windows: copy .env.example .env
# isi kelima nilai di .env, plus GOOGLE_ADS_DEFAULT_CUSTOMER_ID=5827572362 (opsional)
```

Lanjut ke bagian **"Pasang di Claude Desktop"** di README.

---

### Checklist

- [ ] Akun MCC dibuat, ID dicatat
- [ ] Akun klien tertaut ke MCC
- [ ] Developer token didapat
- [ ] Basic Access diajukan (menunggu approval)
- [ ] Google Ads API di-enable di Google Cloud
- [ ] OAuth client (Desktop) dibuat, `client_secret.json` di-download
- [ ] Refresh token dibuat
- [ ] `.env` terisi
