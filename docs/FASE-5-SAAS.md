# Fase 5 — SaaS (DITUNDA)

Status: **ditunda** sesuai keputusan Model A (layanan terkelola). Dokumen ini menyimpan rancangan
agar kode Fase 1–4 tetap kompatibel saat SaaS dibangun.

## Perbedaan dengan Model A

| | Model A (sekarang) | Model B (SaaS) |
|---|---|---|
| Login Google | 1 akun agensi (MCC) | Setiap klien login sendiri (OAuth) |
| Kredensial | `.env` lokal | Refresh token per klien, terenkripsi di database |
| Akses | Claude Desktop (stdio) | Server online (HTTP) + login, dipasang sebagai custom connector di claude.ai |
| Verifikasi Google | Tidak perlu | Verifikasi aplikasi OAuth (scope sensitif), kemungkinan Standard Access |
| Penagihan | Kontrak jasa | Paket + kuota + pembayaran (Midtrans/Xendit) |

## Komponen yang perlu dibangun

1. **Database** (PostgreSQL): tenants, users, google_connections (refresh token terenkripsi, KMS),
   clients (pengganti `clients.yaml`), audit_log (pengganti file JSONL), usage (kuota).
2. **OAuth Google per tenant**: alur web (bukan Desktop), simpan refresh token terenkripsi.
3. **Transport MCP HTTP** (`streamable-http`) + otorisasi MCP (OAuth 2.1) agar bisa dipasang sebagai
   custom connector di claude.ai.
4. **Isolasi tenant**: setiap tool menerima tenant dari token, bukan dari parameter.
5. **Kuota & paket**: hitung pemanggilan tool per tenant per bulan.
6. **Pembayaran**: Midtrans/Xendit (QRIS, VA, e-wallet), webhook status langganan.
7. **Kepatuhan**: kebijakan privasi, S&K, UU PDP, Google API Services User Data Policy,
   verifikasi OAuth Google (domain, homepage, video demo).

## Standar keamanan minimum (pelajaran dari Adspirer)

- Login klien: **OAuth 2.1 + PKCE**; transport MCP Streamable HTTP lewat HTTPS.
- Refresh token platform **terenkripsi at rest** (KMS), tidak pernah dikirim ke sesi AI; diperbarui otomatis.
- **Tidak menyimpan data kampanye** jangka panjang — baca langsung dari API (cache singkat boleh, mis. ≤15 menit).
- Tidak mencatat isi percakapan; log audit hanya untuk perubahan akun (sudah ada di AdsAi).
- Integrasi pihak ketiga (GA4, CRM) lewat broker token (mis. Pipedream) agar kredensial mentah tidak dipegang.
- Tool destruktif dikunci flag eksplisit (AdsAi: `confirm=true` + tidak ada tool hapus permanen).

## Titik sambung dengan kode sekarang

- `Ads(settings)` → dibuat per tenant dari kredensial di database (bukan `.env`).
- `AuditLog` → implementasi kedua yang menulis ke tabel `audit_log` (antarmuka sama: `write`, `read`).
- `load_clients()` → sumber dari tabel `clients` (struktur `Client` tetap).
- `service.py`, `analysis.py`, `reports.py`, `notify.py` → dipakai apa adanya.
