# SOP Operasional AdsAi (Layanan Terkelola)

## Onboarding klien baru (±15 menit)

1. Minta **Customer ID** Google Ads klien → di MCC: *Akun → Sub-akun → + → Tautkan akun yang sudah ada*.
2. Klien menyetujui: *Admin → Akses dan keamanan → Pengelola → Terima*.
3. Tambahkan blok klien di `clients.yaml` (nama, customer_id, nomor WA, email, target CPA,
   budget bulanan, **protected_terms** = nama produk/merek klien).
4. `adsai clients` → pastikan klien muncul.
5. Di Claude: *"client_health untuk <klien>"* → audit awal. Biasanya temuan pertama:
   konversi Utama terlalu banyak, kampanye tidak tayang, search terms boros.
6. Kontrak jasa: pastikan ada klausul izin akses akun iklan & penggunaan data (UU PDP).
7. `adsai dossier --client <klien>` → buat Claude Project per klien: tempel `AGENT_INSTRUCTIONS.md` ke
   Project instructions dan unggah `SKILL.md` + `PROMPTBOOK.md` sebagai pengetahuan project.

## Rutinitas

| Kapan | Siapa | Apa |
|---|---|---|
| Setiap hari 08:00 | Otomatis | `adsai alerts --send --critical-only` → WA ke klien hanya bila KRITIS |
| Setiap hari 08:00 | Otomatis | `adsai alerts` → hasil ke tim (lihat log / email internal) |
| Senin pagi | Tim | Di Claude: `client_health` semua klien → tangani alert |
| Senin | Tim | `suggest_negative_keywords` per klien → review → `add_negative_keywords` (pratinjau → confirm) |
| Senin | Tim | `budget_pacing` → sesuaikan budget bila BOROS/KURANG (`update_budget`) |
| Senin 10:00 | Otomatis | `adsai report --send` → laporan mingguan ke semua klien aktif |
| Awal bulan | Tim | Review `change_log` per klien untuk laporan bulanan |
| Awal bulan | Otomatis/Tim | `adsai dossier` → perbarui kit dossier semua klien, unggah ulang ke Project masing-masing |

Laporan mingguan otomatis mencantumkan **"Yang dikerjakan tim minggu ini"** dari log audit —
jadi optimasi di hari Senin pagi ikut tampil di laporan Senin siang.

## Jadwal otomatis

**Linux / macOS (cron)** — `crontab -e`:
```cron
0 8 * * *   cd /path/AdsAi && .venv/bin/adsai alerts --send --critical-only >> logs/alerts.log 2>&1
0 10 * * 1  cd /path/AdsAi && .venv/bin/adsai report --send >> logs/report.log 2>&1
```

**Windows (Task Scheduler)**:
- Program: `C:\Users\NAMA\AdsAi\.venv\Scripts\adsai.exe`
- Arguments: `alerts --send --critical-only` (harian 08:00) / `report --send` (Senin 10:00)
- Start in: `C:\Users\NAMA\AdsAi`

Mulailah dengan `ADSAI_WA_PROVIDER=console` selama 1–2 minggu (pesan hanya tampil di log),
cek kualitasnya, baru ganti ke `fonnte` / `webhook`.

## WhatsApp

- **Fonnte**: `ADSAI_WA_PROVIDER=fonnte`, `ADSAI_FONNTE_TOKEN=<token device>`.
- **DripSender / gateway lain**: `ADSAI_WA_PROVIDER=webhook`, lalu isi URL, header, dan body JSON
  sesuai dokumentasi API gateway tersebut. `{phone}` (format 62xxx) dan `{message}` diganti otomatis.
  Contoh pola (sesuaikan nama field dengan dokumentasi resmi gateway Anda):
  ```
  ADSAI_WA_WEBHOOK_URL=https://<endpoint-kirim-pesan>
  ADSAI_WA_WEBHOOK_BODY={"api_key": "<API_KEY>", "phone": "{phone}", "text": "{message}"}
  ```
- Uji ke nomor sendiri dulu: buat klien uji di `clients.yaml` dengan nomor tim, lalu
  `adsai report --client uji --send`.

## Aturan emas perubahan akun

1. Selalu pratinjau (`confirm=false`) dan baca rencananya sebelum `confirm=true`.
2. Jangan ubah strategi bidding/konversi lebih dari sekali per 2 minggu per kampanye (fase belajar).
3. Budget naik bertahap (maks 2× per perubahan — dijaga otomatis).
4. Usulan negatif: periksa kata produk klien; tambahkan ke `protected_terms` bila ada yang keliru.
