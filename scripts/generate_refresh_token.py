"""Buat GOOGLE_ADS_REFRESH_TOKEN sekali saja, di komputer Anda sendiri.

Cara pakai:
    1. Download file JSON OAuth client (tipe "Desktop app") dari Google Cloud Console,
       simpan sebagai client_secret.json di folder proyek (sudah di-.gitignore).
    2. python scripts/generate_refresh_token.py --client-secrets client_secret.json
    3. Browser terbuka -> login dengan akun Google yang punya akses ke MCC -> Izinkan.
    4. Salin nilai yang dicetak ke file .env.
"""

from __future__ import annotations

import argparse
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-secrets", default="client_secret.json", help="Path file JSON OAuth client")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, scopes=SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    with open(args.client_secrets) as fh:
        info = json.load(fh)
    client = info.get("installed") or info.get("web") or {}

    print("\nBerhasil. Tambahkan baris berikut ke file .env:\n")
    print(f"GOOGLE_ADS_CLIENT_ID={client.get('client_id', '')}")
    print(f"GOOGLE_ADS_CLIENT_SECRET={client.get('client_secret', '')}")
    print(f"GOOGLE_ADS_REFRESH_TOKEN={creds.refresh_token}")
    print("\nJangan bagikan nilai ini ke siapa pun (termasuk di chat).")


if __name__ == "__main__":
    main()
