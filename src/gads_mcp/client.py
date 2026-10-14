"""Pembungkus tipis GoogleAdsClient: menjalankan GAQL dan mengembalikan list dict yang rapi."""

from __future__ import annotations

from functools import cached_property
from typing import Any

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.auth.exceptions import RefreshError
from google.protobuf.json_format import MessageToDict

from .config import Settings, normalize_customer_id
from .gaql import flatten, humanize


class AdsError(RuntimeError):
    """Error Google Ads yang sudah diringkas agar mudah dibaca Claude."""


AUTH_HINT = (
    "Login OAuth gagal ({}). Periksa GOOGLE_ADS_CLIENT_ID/SECRET, atau buat ulang "
    "GOOGLE_ADS_REFRESH_TOKEN dengan scripts/generate_refresh_token.py."
)


def _describe(ex: GoogleAdsException) -> str:
    parts = [f"{e.error_code}: {e.message}".replace("\n", " ") for e in ex.failure.errors]
    return f"Google Ads API menolak permintaan (request_id={ex.request_id}). " + " | ".join(parts)


class Ads:
    def __init__(self, settings: Settings):
        self.settings = settings

    @cached_property
    def client(self) -> GoogleAdsClient:
        try:
            return GoogleAdsClient.load_from_dict(self.settings.to_google_ads_dict())
        except RefreshError as ex:
            raise AdsError(AUTH_HINT.format(ex.args[0] if ex.args else ex)) from ex

    def customer_id(self, value: str | None) -> str:
        cid = normalize_customer_id(value) or self.settings.default_customer_id
        if not cid:
            raise AdsError(
                "customer_id belum diisi dan GOOGLE_ADS_DEFAULT_CUSTOMER_ID kosong. "
                "Panggil list_accounts untuk melihat ID akun."
            )
        return cid

    def search(self, customer_id: str, query: str) -> list[dict[str, Any]]:
        service = self.client.get_service("GoogleAdsService")
        rows: list[dict[str, Any]] = []
        try:
            for batch in service.search_stream(customer_id=customer_id, query=query):
                for row in batch.results:
                    raw = MessageToDict(row._pb, preserving_proto_field_name=True)
                    rows.append(humanize(flatten(raw)))
        except GoogleAdsException as ex:
            raise AdsError(_describe(ex)) from ex
        return rows

    def accessible_customers(self) -> list[str]:
        service = self.client.get_service("CustomerService")
        try:
            names = service.list_accessible_customers().resource_names
        except GoogleAdsException as ex:
            raise AdsError(_describe(ex)) from ex
        return [n.split("/")[-1] for n in names]
