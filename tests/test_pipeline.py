import asyncio
import json
import sys
from datetime import date

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kb import agent, db, ingest, search, semantic
from kb.clean import clean_company, normalize_name
from kb.config import get_settings


@pytest.fixture
def loaded(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/p.db")
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    stats = ingest.ingest_samples()
    yield stats
    get_settings.cache_clear()
    db.get_engine.cache_clear()


def test_normalize_name():
    assert normalize_name("  ŠUMAVSKÁ REALITY   s. r. o.  ") == ("Šumavská Reality s.r.o.", "sumavska reality")
    assert normalize_name("Orlická Finance  a. s.")[0] == "Orlická Finance a.s."
    assert normalize_name("Modrý Gastro, spol. s r.o.")[0] == "Modrý Gastro spol. s r.o."
    assert normalize_name("Nordic Gastro")[0] == "Nordic Gastro"  # bez pravni formy se nic nedoplni


def test_clean_company_typing_and_issues():
    raw = {"ico": "28100034", "obchodniJmeno": "Demo Gama s.r.o.", "pravniForma": "112", "datumVzniku": "2026-08-20",
           "sidlo": {"psc": 7020, "textovaAdresa": "Testovací 3, 07020 Ostrava"}, "czNace": ["70220"]}
    c = clean_company(raw, today=date(2026, 10, 7))
    assert c["zip"] == "07020" and c["city"] == "Ostrava" and c["street"] == "Testovací 3"
    assert c["age_band"] == "do 1 roku" and c["is_active"] and not c["has_dic"]
    assert c["nace_division_label"] == "Poradenství a řízení"
    assert c["quality_issues"] == ["missing_dic"]
    bad = clean_company({"ico": "12345678", "obchodniJmeno": "X", "datumVzniku": "nesmysl"})
    assert {"invalid_ico_checksum", "missing_founded", "missing_dic"} <= set(bad["quality_issues"])


def test_ingest_idempotent_and_updates(loaded):
    assert loaded["new"] == 40
    again = ingest.ingest_samples()
    assert again["new"] == 0 and again["unchanged"] == 40
    assert ingest.counts() == {"ares_raw": 40, "companies_clean": 40, "company_vectors": 40}
    changed = {"ico": "28100018", "obchodniJmeno": "Demo Alfa Nova s.r.o.", "pravniForma": "112",
               "datumVzniku": "2009-03-12", "czNace": ["62010"], "dic": "CZ28100018"}
    assert ingest.ingest_raw([changed])["updated"] == 1
    assert search.get_company("28100018")["name"] == "Demo Alfa Nova s.r.o."


def test_semantic_query_and_safety(loaded):
    r = semantic.query(["company_count", "dissolved_count"], ["status"])
    assert {row[0] for row in r["rows"]} == {"aktivní", "zaniklá"}
    assert sum(row[1] for row in r["rows"]) == 40
    r = semantic.query(["company_count"], filters=["active"], where={"city": ["Praha", "Brno"]})
    assert r["rows"][0][0] > 0
    with pytest.raises(ValueError):
        semantic.query(["company_count; DROP TABLE companies_clean"])
    with pytest.raises(ValueError):
        semantic.query(["company_count"], where={"city; --": "x"})
    # hodnota jde jako parametr, ne do SQL
    r = semantic.query(["company_count"], where={"city": "x' OR '1'='1"})
    assert r["rows"][0][0] == 0


def test_vector_search_and_profile(loaded):
    top = search.search_companies("Programování a IT Praha", limit=3)
    assert top[0]["ico"] == "28100018" and top[0]["score"] >= top[1]["score"]
    assert all(r["status"] == "aktivní" for r in search.search_companies("Demo", limit=10, only_active=True))
    p = search.get_company("28100026")
    assert p["red_flags"] == ["Subjekt zanikl 2022-11-01"]
    assert search.get_company("00006947") is None


def test_mcp_server_over_stdio(loaded):
    import os

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "kb.mcp_server"], env=dict(os.environ))
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            res = await s.call_tool("query_metrics", {"metrics": ["company_count"]})
            bad = await s.call_tool("get_company", {"ico": "12345678"})
            return names, res, bad

    names, res, bad = asyncio.run(run())
    assert names == {"describe_semantic_layer", "query_metrics", "search_companies", "get_company", "ingest_company"}
    assert json.loads(res.content[0].text)["rows"] == [[40]]
    assert "Neplatné IČO" in bad.content[0].text


def test_agent_mock_uses_mcp_tools(loaded):
    out = asyncio.run(agent.ask_agent("Kolik je firem podle právní formy?"))
    assert out["steps"][0]["tool"] == "query_metrics" and "Společnost s ručením omezeným" in out["answer"]
    out = asyncio.run(agent.ask_agent("Co víš o firmě 28100026?"))
    assert out["steps"][0]["tool"] == "get_company" and "zanikl" in out["answer"]
    out = asyncio.run(agent.ask_agent("Programování a IT Praha"))
    assert out["steps"][0]["tool"] == "search_companies" and "28100018" in out["answer"]
