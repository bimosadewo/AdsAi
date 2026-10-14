"""Pengganti Google Ads untuk tes: client asli (tanpa jaringan) + layanan palsu yang mencatat request."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from gads_mcp.config import Safety, Settings


class FakeService:
    def __init__(self, real, calls: list, extras: dict | None = None):
        self._real = real
        self._calls = calls
        self._extras = extras or {}

    def __getattr__(self, name):
        if name in self._extras:
            def extra(request):
                self._calls.append((name, request))
                return self._extras[name]
            return extra
        if name == "mutate":
            def mutate_all(request):
                self._calls.append((name, request))
                n = len(request["mutate_operations"])
                return SimpleNamespace(mutate_operation_responses=[SimpleNamespace(resource_name=f"res/{i}")
                                                                   for i in range(n)])
            return mutate_all
        if name.startswith("mutate_"):
            def mutate(request):
                self._calls.append((name, request))
                n = len(request["operations"])
                return SimpleNamespace(results=[SimpleNamespace(resource_name=f"res/{i}") for i in range(n)])
            return mutate
        if name == "search_stream":
            def stream(customer_id, query):
                row = SimpleNamespace(campaign=SimpleNamespace(campaign_budget="customers/1/campaignBudgets/99"))
                return [SimpleNamespace(results=[row])]
            return stream
        return getattr(self._real, name)  # *_path helpers


class FakeAds:
    """Meniru gads_mcp.client.Ads."""

    def __init__(self, search: Callable[[str, str], list[dict[str, Any]]] | None = None, safety: Safety | None = None):
        self.settings = Settings("t", "c", "s", "r", "", "1234567890", safety or Safety())
        self.client = GoogleAdsClient(credentials=AnonymousCredentials(), developer_token="t", use_proto_plus=True)
        self.calls: list = []
        self.extras: dict = {}
        real_get_service = self.client.get_service
        self.client.get_service = lambda name, **kw: FakeService(real_get_service(name), self.calls, self.extras)
        self._search = search or (lambda cid, q: [])
        self.queries: list[str] = []

    def search(self, cid: str, query: str):
        self.queries.append(query)
        return self._search(cid, query)

    def customer_id(self, value):
        return value or self.settings.default_customer_id
