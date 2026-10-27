# Google Analytics 4 di AdsAi (baca saja)

Dengan GA4, AdsAi bisa menjawab:
- berapa orang yang datang ke website
- dari mana mereka datang (iklan, Google organik, langsung, sosial)
- halaman mana yang dibuka
- berapa yang klik WhatsApp/telepon

**Berbeda dengan Google Business Profile, GA4 tidak perlu persetujuan Google.** Cukup aktifkan API-nya, lalu buat
token. Totalnya ±10 menit.

AdsAi hanya **membaca** GA4 (scope `analytics.readonly`). Pengaturan GA4 tidak pernah diubah.

## Satu token untuk semua klien

Polanya sama seperti MCC Google Ads: cukup **satu login agensi** (`budi3pilar@gmail.com`).
- Klien **tidak perlu** membuat project Cloud, API, atau token sendiri.
- Klien cukup menambahkan email agensi ke properti GA4 mereka:
  1. Buka GA4 → **Admin** (ikon roda gigi).
  2. **Akses properti** (Property access management) → **+** → **Tambahkan pengguna**.
  3. Email: `budi3pilar@gmail.com`, peran **Pembaca (Viewer)**. Matikan *Beri tahu pengguna baru melalui email*
     bila tidak perlu. Klik **Tambahkan**.
- Properti baru langsung bisa dibaca AdsAi tanpa membuat token baru. Cek dengan `ga4_properties`.
- Klien bisa mencabut aksesnya kapan saja dari menu yang sama.

Kuota gratis GA4 dihitung **per properti**. Laporan klien A tidak menghabiskan kuota klien B.

## Langkah 1 — Aktifkan API (project `adsai-511706`)

Cloud Console → **APIs & Services → Library**, aktifkan:
1. **Google Analytics Data API**
2. **Google Analytics Admin API**

Lalu buka **Google Auth Platform → Data Access → Add or remove scopes**, tambahkan
`https://www.googleapis.com/auth/analytics.readonly`, dan simpan.

## Langkah 2 — Buat token

Di laptop, folder AdsAi:

```bash
python scripts/generate_refresh_token.py --ga4
```

- Login dengan `budi3pilar@gmail.com` → **Izinkan**.
- Hanya `GA4_REFRESH_TOKEN` yang ditulis ke `.env`.

## Langkah 3 — Server online (Cloud Run)

1. **Secret Manager** → secret baru `adsai-ga4-refresh-token`, isi nilai `GA4_REFRESH_TOKEN`.
2. Beri akses **Secret Accessor** ke service account Cloud Run.
3. Cloud Run → adsai → **Edit & deploy new revision** → tambahkan variabel `GA4_REFRESH_TOKEN` dari secret
   tersebut.
4. Di Claude, ketik *"cek properti GA4"*. Hasil yang diharapkan: status **SIAP** dan daftar properti.

## Tool

| Tool | Fungsi |
|---|---|
| `ga4_properties` | Properti yang bisa dibaca (milik sendiri + klien yang memberi akses) |
| `ga4_overview` | Kunjungan per saluran, halaman masuk teratas + keterlibatan, event teratas (klik WA), temuan |
| `ga4_report` | Laporan bebas: dimensi/metrik apa saja, filter teks (`contains`) |

Contoh perintah:
- *"Ringkasan website Tiga Pilar 30 hari terakhir."*
- *"Dari kota mana pengunjung yang klik WhatsApp?"*
- *"Bandingkan klik WA di GA4 dengan konversi Google Ads bulan ini."*

Agar klik WhatsApp/telepon terukur, tandai event-nya sebagai **key event**: GA4 → Admin → Events → aktifkan
*Mark as key event*. `ga4_overview` akan mengingatkan bila belum ada key event.
