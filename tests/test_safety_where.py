import pytest

from gads_mcp import safety


def test_read_only_error_names_the_server(monkeypatch):
    s = safety.Safety(read_only=True)
    monkeypatch.delenv("ADSAI_PUBLIC_URL", raising=False)
    with pytest.raises(safety.SafetyError, match="LOKAL"):
        safety.ensure_writable(s)
    monkeypatch.setenv("ADSAI_PUBLIC_URL", "https://adsai.example.run.app")
    with pytest.raises(safety.SafetyError, match="ONLINE"):
        safety.ensure_writable(s)
