# AdsAi — Google Ads MCP Server

Server [MCP](https://modelcontextprotocol.io) agar Claude bisa langsung membaca dan mengaudit akun Google Ads
(dan nantinya mengubahnya dengan pengaman), tanpa download CSV dan tanpa layanan pihak ketiga.

```
Claude Desktop ──MCP (stdio)──► AdsAi (server ini) ──► Google Ads API ──► Akun klien (via MCC)
```

## Status

| Fase | Isi | Status |
|---|---|---|
| 0 | Akses & kredensial (MCC, developer token, OAuth) | 📋 Panduan: [docs/FASE-0-AKSES.md](docs/FASE-0-AKSES.md) |
| 1 | MVP baca-saja (8 tools) | ✅ Selesai |
| 2 | Tools tulis + pengaman (validate_only, batas budget, log) | ⏳ Berikutnya |
| 3 | Analisa pintar (wasted spend, n-gram, anomali) | ⏳ |
| 4 | Deploy online + login multi-user (custom connector claude.ai) | ⏳ |
| 5 | Otomasi laporan & alert dengan Claude API | ⏳ |

## Tools (Fase 1 — semuanya baca-saja)

| Tool | Fungsi |
|---|---|
| `list_accounts` | Daftar akun klien di bawah MCC |
| `list_campaigns` | Kampanye, status + alasan status, bidding, budget harian |
| `campaign_performance` | Biaya, klik, CTR, CPC, konversi, CPA, pangsa tayangan |
| `search_terms` | Istilah penelusuran + total biaya tanpa konversi |
| `keywords` | Kata kunci, match type, Quality Score & komponennya |
| `conversion_actions` | Konversi Utama/Sekunder, cara hitung, jangka waktu, nilai |
| `account_alerts` | Kampanye aktif tapi tidak tayang, konversi Utama berlebihan, impor offline gagal |
| `run_query` | GAQL bebas (hanya `SELECT`, maks 1000 baris) |

Nilai uang dikembalikan dalam mata uang akun (micros sudah dikonversi).

## Instalasi

1. Selesaikan [Fase 0](docs/FASE-0-AKSES.md) sampai file `.env` terisi.
2. Instal:
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   pytest                           # opsional: tes unit
   ```

## Pasang di Claude Desktop

Buka **Claude Desktop → Settings → Developer → Edit Config**, lalu tambahkan
(ganti path sesuai lokasi folder Anda):

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

File `.env` otomatis dicari di folder proyek. Kalau disimpan di tempat lain, tambahkan
`"env": { "GOOGLE_ADS_ENV_FILE": "path/ke/.env" }` pada konfigurasi di atas.

Restart Claude Desktop. Di chat baru, coba:
> *"Pakai google-ads: list_accounts, lalu account_alerts untuk Tiga Pilar."*

### Pasang di Claude Code

```bash
claude mcp add google-ads -- /path/ke/AdsAi/.venv/bin/google-ads-mcp
```

## Struktur

```
src/gads_mcp/
  config.py   # baca .env, validasi customer ID
  gaql.py     # query GAQL + pengolah hasil (murni, dites)
  client.py   # pembungkus GoogleAdsClient + pesan error yang jelas
  server.py   # definisi tools MCP
scripts/generate_refresh_token.py
tests/test_gaql.py
docs/FASE-0-AKSES.md
```

## Keamanan

- Kredensial hanya dibaca dari `.env` lokal; `.env` dan `client_secret*.json` di-`.gitignore`.
- Fase 1 tidak punya satu pun tool yang bisa mengubah akun.
- `run_query` menolak apa pun selain satu pernyataan `SELECT`.
