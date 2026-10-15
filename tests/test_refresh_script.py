import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "generate_refresh_token.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def test_update_env_replaces_and_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# komentar\nGOOGLE_ADS_CLIENT_ID=\nADSAI_READ_ONLY=true\n#GOOGLE_ADS_REFRESH_TOKEN=lama\n")
    gen.update_env(env, {"GOOGLE_ADS_CLIENT_ID": "abc", "GOOGLE_ADS_REFRESH_TOKEN": "r1",
                         "GOOGLE_ADS_LOGIN_CUSTOMER_ID": gen.digits("221-446-0264")})
    lines = env.read_text().splitlines()
    assert "GOOGLE_ADS_CLIENT_ID=abc" in lines and "ADSAI_READ_ONLY=true" in lines
    assert "#GOOGLE_ADS_REFRESH_TOKEN=lama" in lines and "GOOGLE_ADS_REFRESH_TOKEN=r1" in lines
    assert "GOOGLE_ADS_LOGIN_CUSTOMER_ID=2214460264" in lines


def test_update_env_creates_from_example(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text("GOOGLE_ADS_CLIENT_ID=\n")
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    gen.update_env(tmp_path / ".env", {"GOOGLE_ADS_CLIENT_ID": "x"})
    assert (tmp_path / ".env").read_text() == "GOOGLE_ADS_CLIENT_ID=x\n"
