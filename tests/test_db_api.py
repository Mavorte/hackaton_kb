import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from kb import db, loader
from kb.aws import bedrock
from kb.clients.ares import Company


@pytest.fixture
def engine(monkeypatch):
    e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    db.Base.metadata.create_all(e)
    db.get_engine.cache_clear()
    monkeypatch.setattr(db, "get_engine", lambda: e)
    monkeypatch.setattr("kb.api.get_engine", lambda: e)
    monkeypatch.setattr("kb.loader.get_engine", lambda: e)
    return e


def test_loader_and_generic_api(engine, tmp_path):
    f = tmp_path / "platby.csv"
    f.write_text("id_platby;castka;mena\nA1;100.5;CZK\nA2;20;EUR\n", encoding="utf-8")
    assert loader.load_file(f, engine=engine) == ("platby", 2)

    from kb.api import app
    c = TestClient(app)
    assert "platby" in c.get("/tables").json()
    assert len(c.get("/tables/platby").json()) == 2
    rows = c.get("/tables/platby", params={"mena": "EUR"}).json()
    assert rows[0]["id_platby"] == "A2" and rows[0]["castka"] == 20.0
    assert c.get("/tables/platby", params={"nope": "x"}).status_code == 422
    assert c.get("/tables/nope").status_code == 404


def test_ares_endpoint_caches_and_kyb(engine, monkeypatch):
    comp = Company(ico="00006947", name="MF", dic="CZ00006947")
    monkeypatch.setattr("kb.api.Ares", lambda: type("A", (), {"get": lambda self, i: comp})())
    monkeypatch.setattr(bedrock, "ask_json", lambda *a, **k: {"summary": "ok", "red_flags": [], "risk": "low"})

    from kb.api import app
    c = TestClient(app)
    assert c.get("/ares/00006947").json()["name"] == "MF"
    assert c.get("/ares/00006948").status_code == 422
    assert c.get("/companies").json()[0]["ico"] == "00006947"
    assert c.get("/kyb/00006947").json()["assessment"]["risk"] == "low"


def test_ask_json_strips_fences(monkeypatch):
    monkeypatch.setattr(bedrock, "ask", lambda *a, **k: '```json\n{"a": 1}\n```')
    assert bedrock.ask_json("x") == {"a": 1}


def test_ask_uses_converse():
    class Fake:
        def converse(self, **kw):
            assert kw["messages"][0]["content"][0]["text"] == "hi"
            return {"output": {"message": {"content": [{"text": "a"}, {"text": "b"}]}}}

    assert bedrock.ask("hi", client=Fake()) == "ab"


def test_loader_keeps_own_id_column(engine, tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("id,castka\n1,10\n2,20\n", encoding="utf-8")
    assert loader.load_file(f, engine=engine) == ("x", 2)


def test_ask_model_override_and_no_temperature_by_default():
    seen = {}

    class Fake:
        def converse(self, **kw):
            seen.update(kw)
            return {"output": {"message": {"content": [{"text": "x"}]}}}

    bedrock.ask("hi", model_id="some.model", client=Fake())
    assert seen["modelId"] == "some.model"
    assert "temperature" not in seen["inferenceConfig"]
    bedrock.ask("hi", temperature=0.5, client=Fake())
    assert seen["inferenceConfig"]["temperature"] == 0.5
