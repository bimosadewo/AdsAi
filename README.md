# AdsAi — Google Ads untuk Agensi, dari Claude

AdsAi menghubungkan Claude ke akun Google Ads klien agensi lewat [MCP](https://modelcontextprotocol.io):
audit, optimasi dengan pengaman, alert harian, dan laporan mingguan ke WhatsApp klien —
tanpa download CSV dan tanpa layanan pihak ketiga.

```
Claude Desktop ──MCP──► AdsAi ──► Google Ads API ──► Akun klien (via MCC agensi)
                          │
cron / Task Scheduler ──► adsai alerts / report ──► WhatsApp & email klien
```

Model bisnis: **layanan terkelola** — tim agensi memakai AdsAi, klien menerima laporan & alert.
Rencana lengkap: [docs/ROADMAP.md](docs/ROADMAP.md) · SOP tim: [docs/OPERASIONAL.md](docs/OPERASIONAL.md)

## Status

| Fase | Isi | Status |
|---|---|---|
| 0 | Akses Google Ads API (MCC, developer token, OAuth) | 📋 Dikerjakan pemilik: [panduan](docs/FASE-0-AKSES.md) |
| 1 | 8 tools baca | ✅ |
| 2 | 7 tools ubah + pengaman + log audit | ✅ |
| 3 | Daftar klien, cek kesehatan, laporan mingguan, kirim WA/email, CLI terjadwal | ✅ |
| 4 | N-gram, usulan negatif otomatis, deteksi anomali, pacing budget | ✅ |
| 5 | SaaS (klien login sendiri) | 🅿️ Ditunda — [rancangan](docs/FASE-5-SAAS.md) |

Semua kode sudah dites dengan simulasi Google Ads (45 tes). Begitu developer token & OAuth dari
Fase 0 tersedia, cukup isi `.env` — tidak ada kode yang perlu diubah.

## Tools MCP (24)

**Baca (Fase 1)**
| Tool | Fungsi |
|---|---|
| `list_accounts` | Akun klien di bawah MCC |
| `list_campaigns` | Kampanye, status + alasan status, bidding, budget harian |
| `campaign_performance` | Biaya, klik, CTR, CPC, konversi, CPA, pangsa tayangan |
| `search_terms` | Istilah penelusuran + total biaya tanpa konversi |
| `keywords` | Kata kunci, match type, Quality Score & komponennya |
| `conversion_actions` | Konversi Utama/Sekunder, cara hitung, jangka waktu, nilai |
| `account_alerts` | Pemeriksaan cepat satu akun |
| `run_query` | GAQL bebas (hanya `SELECT`, maks 1000 baris) |

**Ubah (Fase 2)** — selalu dua langkah: `confirm=false` = pratinjau yang divalidasi Google tanpa mengubah
apa pun; `confirm=true` = dijalankan dan dicatat di log audit.
| Tool | Fungsi |
|---|---|
| `add_negative_keywords` / `remove_negative_keywords` | Kata kunci negatif kampanye (duplikat otomatis dilewati) |
| `update_budget` | Budget harian (dibatasi batas maksimum & kenaikan maks per perubahan) |
| `set_status` | Jeda / aktifkan kampanye, grup iklan, kata kunci |
| `update_conversion_action` | Utama/Sekunder, cara hitung, jangka waktu, status |
| `update_bid_strategy` | Maks. konversi (± target CPA), maks. klik (± CPC maks), CPC manual |
| `add_keywords` | Tambah kata kunci ke grup iklan |
| `change_log` | Riwayat perubahan lewat AdsAi |

**Klien & laporan (Fase 3)**
| Tool | Fungsi |
|---|---|
| `list_clients` | Isi `clients.yaml` |
| `client_health` | Alert satu/semua klien + pesan WA siap kirim |
| `weekly_report` | Laporan mingguan (pratinjau) |
| `send_to_client` | Kirim pesan ke WA/email klien (pratinjau dulu, lalu `confirm=true`) |

**Analisa (Fase 4)**
| Tool | Fungsi |
|---|---|
| `ngram_analysis` | Biaya/konversi per kata atau frasa di search terms |
| `suggest_negative_keywords` | Usulan negatif — kata produk klien & kata niat beli selalu dilindungi |
| `detect_anomalies` | Lonjakan/penurunan biaya, klik, konversi, CPC + hari tanpa konversi |
| `budget_pacing` | Proyeksi akhir bulan vs budget bulanan + saran budget harian |

## CLI terjadwal (`adsai`)

```bash
adsai clients                      # cek isi clients.yaml (tidak butuh kredensial Google)
adsai alerts                       # cek semua klien aktif
adsai alerts --send --critical-only  # kirim WA hanya bila ada masalah KRITIS
adsai report --client tigapilar    # pratinjau laporan mingguan
adsai report --send                # kirim laporan mingguan ke semua klien aktif
```
Kode keluar: `0` aman · `2` ada alert KRITIS · `1` error. Jadwal yang disarankan ada di
[docs/OPERASIONAL.md](docs/OPERASIONAL.md).

## Instalasi

1. Selesaikan [Fase 0](docs/FASE-0-AKSES.md) sampai `.env` terisi.
2. Instal:
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   pytest                           # opsional: 45 tes
   ```
3. Salin `clients.example.yaml` → `clients.yaml`, isi data klien, lalu `adsai clients`.
4. Atur pengaman & WhatsApp di `.env` (lihat `.env.example`). Default WA = `console` (tidak mengirim apa pun).

## Pasang di Claude Desktop

**Claude Desktop → Settings → Developer → Edit Config**:

**Windows**
```json
{
  "mcpServers": {
    "google-ads": {
      "command": "C:\\Users\\NAMA\\AdsAi\\.venv\\Scripts\\google-ads-mcp.exe"
    }
  }
}
```

**macOS / Linux**
```json
{
  "mcpServers": {
    "google-ads": {
      "command": "/Users/NAMA/AdsAi/.venv/bin/google-ads-mcp"
    }
  }
}
```

`.env` otomatis dicari di folder proyek. Kalau disimpan di tempat lain, tambahkan
`"env": { "GOOGLE_ADS_ENV_FILE": "path/ke/.env" }`. Restart Claude Desktop, lalu coba:
> *"Pakai google-ads: client_health untuk semua klien."*

Claude Code: `claude mcp add google-ads -- /path/ke/AdsAi/.venv/bin/google-ads-mcp`

## Pengaman

- `ADSAI_READ_ONLY=true` mematikan semua perubahan.
- `ADSAI_MAX_DAILY_BUDGET` & `ADSAI_MAX_BUDGET_INCREASE_FACTOR` membatasi perubahan budget;
  budget bersama (shared) ditolak kecuali `allow_shared=true`.
- Setiap perubahan divalidasi Google (`validate_only`) sebelum dijalankan, dan tercatat di
  `logs/<customer_id>.jsonl` (operator, waktu, sebelum → sesudah).
- Menghapus kampanye/grup iklan permanen sengaja tidak disediakan.
- Kredensial (`.env`, `client_secret*.json`) dan data klien (`clients.yaml`, `logs/`) di-`.gitignore`.

## Struktur

```
src/gads_mcp/
  config.py     .env, pengaturan pengaman
  gaql.py       query GAQL + pengolah hasil
  client.py     pembungkus Google Ads API + pesan error
  mutations.py  operasi ubah (pratinjau → konfirmasi → audit)
  safety.py     aturan pengaman + log audit
  clients.py    clients.yaml
  collect.py    snapshot data per klien
  analysis.py   n-gram, usulan negatif, anomali, pacing, aturan alert
  reports.py    laporan mingguan & teks alert (WhatsApp/Markdown)
  notify.py     WhatsApp (Fonnte / webhook) & email
  service.py    alur tingkat klien (dipakai server & CLI)
  server.py     24 tools MCP
  cli.py        perintah `adsai`
```
