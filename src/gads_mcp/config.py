"""Membaca kredensial Google Ads dari environment / file .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REQUIRED = (
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
)


def normalize_customer_id(value: str | int | None) -> str:
    """'582-757-2362' / 5827572362 -> '5827572362'."""
    if value is None:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if digits and len(digits) != 10:
        raise ValueError(f"Customer ID harus 10 digit, didapat: {value!r}")
    return digits


@dataclass(frozen=True)
class Settings:
    developer_token: str
    client_id: str
    client_secret: str
    refresh_token: str
    login_customer_id: str
    default_customer_id: str

    def to_google_ads_dict(self) -> dict:
        cfg = {
            "developer_token": self.developer_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "use_proto_plus": True,
        }
        if self.login_customer_id:
            cfg["login_customer_id"] = self.login_customer_id
        return cfg


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _find_env_file() -> Path | None:
    """Urutan: GOOGLE_ADS_ENV_FILE, .env di folder kerja, .env di root proyek."""
    explicit = os.getenv("GOOGLE_ADS_ENV_FILE")
    if explicit:
        return Path(explicit)
    for candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        if candidate.is_file():
            return candidate
    return None


def load_settings() -> Settings:
    load_dotenv(_find_env_file())
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            "Variabel environment belum diisi: "
            + ", ".join(missing)
            + ". Salin .env.example menjadi .env dan isi nilainya."
        )
    return Settings(
        developer_token=os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        client_id=os.environ["GOOGLE_ADS_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_ADS_CLIENT_SECRET"],
        refresh_token=os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        login_customer_id=normalize_customer_id(os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID")),
        default_customer_id=normalize_customer_id(os.getenv("GOOGLE_ADS_DEFAULT_CUSTOMER_ID")),
    )
