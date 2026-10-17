from gads_mcp import config


def test_credentials_are_trimmed(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_ADS_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_ID", " abc.apps.googleusercontent.com\n")
    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "GOCSPX-xyz\r\n")
    monkeypatch.setenv("GOOGLE_ADS_REFRESH_TOKEN", '"1//tok"\n')
    s = config.load_settings()
    assert (s.client_id, s.client_secret, s.refresh_token) == ("abc.apps.googleusercontent.com", "GOCSPX-xyz", "1//tok")
