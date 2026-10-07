from datetime import date

import pytest
from fastapi.testclient import TestClient

from kb import kyb
from kb.aws import bedrock
from kb.clients import vies
from kb.clients.ares import Ares, NotFound, valid_ico
from kb.config import get_settings


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_samples_have_valid_ico(offline):
    for ico in ("28100018", "28100026", "28100034"):
        assert valid_ico(ico)
        assert Ares().get(ico).ico == ico


def test_offline_ares_search_and_not_found(offline):
    assert [c.ico for c in Ares().search("demo")] == ["28100018", "28100026", "28100034"]
    assert [c.ico for c in Ares().search("beta")] == ["28100026"]
    with pytest.raises(NotFound):
        Ares().get("00006947")


def test_offline_vies(offline):
    assert vies.check_vat("CZ", "28100018")["isValid"] is True
    assert vies.check_vat("CZ", "28100034")["isValid"] is False  # bez DIC
    assert vies.check_vat("CZ", "28100026")["isValid"] is False  # zanikla


def test_mock_llm(offline):
    assert bedrock.ask("ahoj").startswith("[MOCK]")
    assert bedrock.ask_json("x") == {"mock": True}


def test_rule_based_assessment(offline):
    today = date(2026, 10, 7)
    assert kyb._rule_based(Ares().get("28100018"), today)["risk"] == "low"
    dissolved = kyb._rule_based(Ares().get("28100026"), today)
    assert dissolved["risk"] == "high" and "zanikl" in dissolved["red_flags"][0]
    young = kyb._rule_based(Ares().get("28100034"), today)
    assert young["risk"] == "medium" and len(young["red_flags"]) == 2


def test_kyb_endpoint_offline(offline, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    get_settings.cache_clear()
    from kb import db
    db.get_engine.cache_clear()
    from kb.api import app

    r = TestClient(app).get("/kyb/28100026")
    assert r.status_code == 200
    assert r.json()["assessment"]["risk"] == "high"
    assert TestClient(app).get("/ares/00006947").status_code == 404
    db.get_engine.cache_clear()
