# AdsAi online (Cloud Run) — panduan klik-demi-klik

Hasil akhir: AdsAi berjalan di `https://adsai-654186807798.asia-southeast2.run.app`, dipasang sebagai
**custom connector** di claude.ai (berlaku di web, Desktop, dan HP), login dengan Google, dan **setiap push ke
GitHub `main` otomatis terpasang** (±3–5 menit). Claude tidak di-hosting — hanya AdsAi.

| Komponen | Isi |
|---|---|
| Project | `adsai-511706` (nomor 654186807798) |
| Region | `asia-southeast2` (Jakarta) |
| Server | Cloud Run service `adsai`, maks 1 instance |
| Kunci rahasia | Secret Manager |
| Data (log audit, clients.yaml, strategi, dossier, sesi login) | bucket `adsai-data-654186807798` → `/data` |
| Login | Google, hanya email di `ADSAI_ALLOWED_EMAILS` |

> Keamanan: tanpa login semua akses ke `/mcp` ditolak (401). Token login disimpan sebagai hash. Kredensial Google
> Ads tidak pernah keluar dari Secret Manager. Mulai dengan `ADSAI_READ_ONLY=true`.

## Langkah 1 — Penagihan + alarm biaya
1. Cloud Console → **Billing** → pastikan project **AdsAi** tertaut ke akun penagihan (bukan *Free Trial*).
2. **Billing → Budgets & alerts → Create budget**: project AdsAi, jumlah **Rp50.000/bulan**, alarm 50/90/100% ke email.

## Langkah 2 — Bucket data
**Cloud Storage → Buckets → Create**
- Name: `adsai-data-654186807798` · Location type **Region** → `asia-southeast2 (Jakarta)`
- Storage class **Standard** · Access control **Uniform** · ✅ *Enforce public access prevention*
- Create. (Nanti `clients.yaml` diunggah ke sini.)

## Langkah 3 — OAuth client untuk login (Web application)
**Google Auth Platform → Clients → Create client**
- Application type: **Web application** · Name: `AdsAi Login`
- **Authorized redirect URIs** → Add URI: `https://adsai-654186807798.asia-southeast2.run.app/auth/google/callback`
- Create → salin **Client ID** (bukan rahasia) dan **Client secret** (rahasia — langsung ke langkah 4).

## Langkah 4 — Secret Manager
**Security → Secret Manager → Create secret** (Enable API bila diminta), buat 4 rahasia:

| Nama secret | Nilai (dari mana) |
|---|---|
| `adsai-ads-client-id` | `GOOGLE_ADS_CLIENT_ID` di `.env` laptop |
| `adsai-ads-client-secret` | `GOOGLE_ADS_CLIENT_SECRET` di `.env` laptop |
| `adsai-ads-refresh-token` | `GOOGLE_ADS_REFRESH_TOKEN` di `.env` laptop |
| `adsai-login-client-secret` | Client secret `AdsAi Login` (langkah 3) |

Buka `.env` di laptop dengan Notepad, salin nilainya satu per satu (tanpa `NAMA=`). Jangan kirim ke chat.

## Langkah 5 — Cloud Run + deploy otomatis dari GitHub
**Cloud Run → Deploy container → Service** → pilih **Continuously deploy from a repository**
→ **Set up with Cloud Build** (Enable API bila diminta) → Repository provider **GitHub** → authorize →
repo **bimosadewo/AdsAi** → Branch `^main$` → Build type **Dockerfile**, source `/Dockerfile` → Save.

Lalu isi:
- Service name: **`adsai`** · Region: **asia-southeast2 (Jakarta)**
- Authentication: **Allow public access** (Claude memanggil dari internet; perlindungan = login AdsAi)
- Scaling: min **0**, max **1** instance

**Containers → Edit container**
- Port: **8080** · Memory **512 MiB** · CPU **1**
- **Variables & secrets → Add variable**:
  | Nama | Nilai |
  |---|---|
  | `ADSAI_PUBLIC_URL` | `https://adsai-654186807798.asia-southeast2.run.app` |
  | `ADSAI_LOGIN_CLIENT_ID` | Client ID `AdsAi Login` |
  | `ADSAI_ALLOWED_EMAILS` | `budi3pilar@gmail.com` |
  | `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | `2214460264` |
  | `GOOGLE_ADS_DEFAULT_CUSTOMER_ID` | `5827572362` |
  | `ADSAI_READ_ONLY` | `true` |
- **Reference a secret** (exposed as environment variable, version `latest`) — Console akan menawarkan memberi
  akses ke service account, pilih **Grant**:
  | Nama variabel | Secret |
  |---|---|
  | `GOOGLE_ADS_CLIENT_ID` | `adsai-ads-client-id` |
  | `GOOGLE_ADS_CLIENT_SECRET` | `adsai-ads-client-secret` |
  | `GOOGLE_ADS_REFRESH_TOKEN` | `adsai-ads-refresh-token` |
  | `ADSAI_LOGIN_CLIENT_SECRET` | `adsai-login-client-secret` |

**Volumes → Add volume** → type **Cloud Storage bucket** → name `data` → bucket `adsai-data-654186807798`.
Kembali ke **Containers → Volume mounts → Mount volume** `data` di **`/data`**.
**Execution environment: Second generation** (wajib untuk volume Cloud Storage).

**Create.** Build pertama ±5 menit. Setelah selesai buka `https://adsai-654186807798.asia-southeast2.run.app/healthz`
→ harus tampil `ok`.

## Langkah 6 — Pasang di Claude
**claude.ai → Settings → Connectors → Add custom connector**
- Name: `AdsAi` · URL: `https://adsai-654186807798.asia-southeast2.run.app/mcp` → **Add** → **Connect**
- Login Google **budi3pilar@gmail.com** → izinkan. Connector berlaku di claude.ai, Claude Desktop, dan aplikasi HP.
- Uji: *"Tampilkan performa iklan saya kemarin, akun 5827572362."* → kartu dashboard tampil.

## Sehari-hari
- **Pembaruan**: tidak perlu apa-apa — tiap push ke `main` otomatis di-build & dipasang (cek di Cloud Build → History).
- **Mengaktifkan perubahan akun**: Cloud Run → `adsai` → Edit & deploy new revision → `ADSAI_READ_ONLY=false`.
- **Menambah anggota tim**: tambah email di `ADSAI_ALLOWED_EMAILS` (pisah koma) → deploy revisi baru.
- **clients.yaml / strategi**: unggah/ubah di bucket (`clients.yaml` di akar bucket, strategi di folder `strategi/`).
- **Mencabut akses**: hapus email dari `ADSAI_ALLOWED_EMAILS` — token yang sudah terbit langsung ditolak.

## Jika ada masalah
| Gejala | Penyebab & perbaikan |
|---|---|
| Google: *redirect_uri_mismatch* | URI di langkah 3 harus persis sama, termasuk `https://` dan `/auth/google/callback` |
| Google menolak domain run.app | Google Auth Platform → Branding → Authorized domains → tambahkan `adsai-654186807798.asia-southeast2.run.app`; bila tetap ditolak, kabari — kita pakai subdomain `adsai.tigapilar.co.id` |
| `/healthz` 503 / revisi gagal start | Cloud Run → Logs. Biasanya secret belum diberi akses atau variabel `ADSAI_*` belum lengkap |
| Login sukses tapi "Akses ditolak" | Email tidak ada di `ADSAI_ALLOWED_EMAILS` |
| Tool error soal kredensial | Nilai secret Google Ads salah salin (spasi/baris baru) — buat versi baru secret lalu deploy revisi baru |
