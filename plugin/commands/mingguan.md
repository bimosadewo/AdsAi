---
description: Siapkan laporan mingguan untuk klien (pratinjau, kirim setelah disetujui)
argument-hint: "<klien>"
---
Siapkan laporan mingguan untuk klien $ARGUMENTS:

1. `weekly_report` (fmt whatsapp) untuk klien ini.
2. Tampilkan teks laporan apa adanya, lalu 2–3 catatan internal untuk tim bila ada yang perlu
   ditindaklanjuti sebelum dikirim (mis. alert KRITIS yang belum ditangani).
3. Tanyakan apakah laporan boleh dikirim. Hanya bila pengguna setuju: `send_to_client` dengan
   confirm=false (tunjukkan penerima), lalu confirm=true.
