"""Buat GOOGLE_ADS_REFRESH_TOKEN sekali saja, di komputer Anda sendiri, dan tulis langsung ke .env.

Cara pakai:
    1. Download file JSON OAuth client (tipe "Desktop app") dari Google Cloud Console,
       simpan sebagai client_secret.json di folder proyek (sudah di-.gitignore).
    2. python scripts/generate_refresh_token.py --login-customer-id 221-446-0264 --default-customer-id 582-757-2362
    3. Browser terbuka -> login dengan akun Google yang punya akses ke MCC -> Izinkan.
    4. Nilai rahasia ditulis langsung ke .env (tidak dicetak ke layar). Pakai --print bila ingin melihatnya.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/adwords"]
ROOT = Path(__file__).resolve().parents[1]


def digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def update_env(path: Path, values: dict[str, str]) -> None:
    """Ganti/isi KEY=... di .env; baris lain (komentar, pengaturan) dibiarkan."""
    if not path.exists():
        example = ROOT / ".env.example"
        if example.exists():
            shutil.copyfile(example, path)
        else:
            path.write_text("", encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines()
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={values[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-secrets", default="client_secret.json", help="Path file JSON OAuth client")
    parser.add_argument("--env", default=str(ROOT / ".env"), help="File .env tujuan (dibuat dari .env.example)")
    parser.add_argument("--login-customer-id", help="ID akun MCC, mis. 221-446-0264")
    parser.add_argument("--default-customer-id", help="Akun klien default, mis. 582-757-2362")
    parser.add_argument("--print", action="store_true", help="Cetak nilai rahasia ke layar (jangan dibagikan)")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secrets, scopes=SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        raise SystemExit("Google tidak mengembalikan refresh token. Hapus akses AdsAi di "
                         "myaccount.google.com/permissions lalu jalankan ulang.")

    with open(args.client_secrets, encoding="utf-8") as fh:
        info = json.load(fh)
    client = info.get("installed") or info.get("web") or {}
    values = {
        "GOOGLE_ADS_CLIENT_ID": client.get("client_id", ""),
        "GOOGLE_ADS_CLIENT_SECRET": client.get("client_secret", ""),
        "GOOGLE_ADS_REFRESH_TOKEN": creds.refresh_token,
    }
    if args.login_customer_id:
        values["GOOGLE_ADS_LOGIN_CUSTOMER_ID"] = digits(args.login_customer_id)
    if args.default_customer_id:
        values["GOOGLE_ADS_DEFAULT_CUSTOMER_ID"] = digits(args.default_customer_id)

    env = Path(args.env)
    update_env(env, values)
    print(f"\nBerhasil. {len(values)} nilai ditulis ke {env}: {', '.join(values)}")
    if args.print:
        print()
        for k, v in values.items():
            print(f"{k}={v}")
    print("\nJangan bagikan isi .env ke siapa pun (termasuk di chat).")


if __name__ == "__main__":
    main()
