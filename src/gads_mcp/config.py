"""Membaca kredensial Google Ads dari environment / file .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

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


def _float_env(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as ex:
        raise RuntimeError(f"{name} harus berupa angka, didapat: {raw!r}") from ex


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "ya", "on")


@dataclass(frozen=True)
class Safety:
    """Pengaman untuk tools yang mengubah akun."""

    read_only: bool = False
    max_daily_budget: float | None = None
    max_budget_increase_factor: float = 2.0
    audit_log_dir: Path = Path("logs")

    @classmethod
    def from_env(cls) -> "Safety":
        factor = _float_env("ADSAI_MAX_BUDGET_INCREASE_FACTOR")
        log_dir = os.getenv("ADSAI_AUDIT_LOG_DIR")
        return cls(
            read_only=_bool_env("ADSAI_READ_ONLY"),
            max_daily_budget=_float_env("ADSAI_MAX_DAILY_BUDGET"),
            max_budget_increase_factor=factor if factor else 2.0,
            audit_log_dir=Path(log_dir) if log_dir else PROJECT_ROOT / "logs",
        )


@dataclass(frozen=True)
class Settings:
    developer_token: str
    client_id: str
    client_secret: str
    refresh_token: str
    login_customer_id: str
    default_customer_id: str
    safety: Safety = field(default_factory=Safety)
    clients_file: Path = Path("clients.yaml")

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


def find_env_file() -> Path | None:
    """Urutan: GOOGLE_ADS_ENV_FILE, .env di folder kerja, .env di root proyek."""
    explicit = os.getenv("GOOGLE_ADS_ENV_FILE")
    if explicit:
        return Path(explicit)
    for candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        if candidate.is_file():
            return candidate
    return None


def load_settings() -> Settings:
    load_dotenv(find_env_file())
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
        safety=Safety.from_env(),
        clients_file=Path(os.getenv("ADSAI_CLIENTS_FILE") or PROJECT_ROOT / "clients.yaml"),
    )
