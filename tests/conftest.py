import pytest


@pytest.fixture(autouse=True)
def kong_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KONG_HOME", str(tmp_path / "home"))
    return tmp_path / "home"
