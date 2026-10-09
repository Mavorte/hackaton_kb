from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from kb import db
from kb.config import get_settings

APP = str(Path(__file__).resolve().parents[1] / "src" / "kb" / "ui.py")


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/ui.db")
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    db.get_engine.cache_clear()


def run(query):
    at = AppTest.from_file(APP, default_timeout=20).run()
    return at.text_input[0].set_value(query).run()


def test_empty_query_shows_hint(offline):
    at = AppTest.from_file(APP, default_timeout=20).run()
    assert not at.exception
    assert "Zadej IČO" in at.info[0].value


def test_dissolved_company_is_high_risk(offline):
    at = run("28100026")
    assert not at.exception
    assert at.subheader[0].value == "Demo Beta a.s."
    assert any("Vysoké riziko" in e.value for e in at.error)


def test_search_by_name_and_invalid_ico(offline):
    at = run("demo")
    assert at.selectbox[0].options[0].startswith("Demo Alfa")
    at = run("12345678")
    assert any("Neplatné IČO" in e.value for e in at.error)


def test_agent_tab_answers_via_mcp(offline):
    from kb import ingest

    ingest.ingest_samples()
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert len(at.tabs) == 3
    at = at.chat_input[0].set_value("Kolik je firem podle právní formy?").run(timeout=60)
    assert not at.exception
    texts = [m.markdown[0].value for m in at.chat_message if m.markdown]
    assert any("Společnost s ručením omezeným" in t for t in texts)
    assert any("query_metrics" in e.label for e in at.expander)


def test_agent_tab_empty_db_hint(offline):
    at = AppTest.from_file(APP, default_timeout=20).run()
    assert any("Databáze je prázdná" in i.value for i in at.info)
