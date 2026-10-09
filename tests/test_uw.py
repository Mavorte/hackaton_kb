import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kb import agent, db, semantic
from kb.config import get_settings
from kb.uw import evaluate, gen, kbase, pipeline, queries, taxonomy
from kb.uw.compare import equal
from kb.uw.docio import read_pdf


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/uw.db")
    monkeypatch.setenv("UW_CASES_DIR", str(tmp_path / "uw"))
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    yield tmp_path / "uw"
    get_settings.cache_clear()
    db.get_engine.cache_clear()


@pytest.fixture
def corpus(env):
    gen.make_corpus(env / "seed", 6, "A", 1, defect_rate=0.3, prefix="seed")
    gen.make_corpus(env / "eval_a", 6, "A", 2, prefix="evalA")
    gen.make_corpus(env / "eval_c", 3, "C", 4, prefix="evalC")
    for c in sorted((env / "seed").iterdir()):
        pipeline.seed_case(c)
    return env


def test_taxonomy_labels_cover_generator_styles_a_b():
    attrs = taxonomy.attributes()
    for style in ("A", "B"):
        for dt, m in gen.LABELS[style].items():
            for attr, label in m.items():
                assert label in attrs[attr]["labels"], (style, dt, attr, label)
                assert attr in taxonomy.doc_types()[dt]["attributes"]
    for r in taxonomy.rules():
        if r["type"] == "consistency":
            assert r["attribute"] in attrs


def test_compare_modes():
    assert equal("text", "Šumavská Reality s.r.o.", "SUMAVSKA   reality, s. r. o.")
    assert equal("text", "rozmet adlo", "rozmetadlo")
    assert not equal("text", "Polabské Realita s.r.o.", "Polabské Reality s.r.o.")  # 1 pismeno = jiny nazev
    assert equal("number", "485 000 Kč", "485000")
    assert not equal("number", "485 000 Kč", "585 000 Kč")
    assert equal("exact", "2900 0076", "29000076")
    assert equal("text", "Alfa Beta", "Alpha Beta", judge=lambda a, b: True)  # hranicni -> rozhoduje LLM


def test_generator_pdfs_and_truth(env):
    t = gen.make_case(env, "x1", gen.load_companies()[3], __import__("random").Random(1), "A", ["missing_signature"])
    d = read_pdf(env / "x1" / next(f for f, dt in t["files"].items() if dt == "smlouva_o_uveru"))
    assert d.has_text_layer and "Nájemce:" in d.first_page_text and d.signatures == 1
    assert t["expected_rules"]["R10_podpisy_smlouva"] == "FAIL"


def test_scanify_removes_text_layer(env):
    gen.make_case(env, "scan1", gen.load_companies()[3], __import__("random").Random(2), "A", [], scan=True)
    d = read_pdf(next((env / "scan1").glob("*.pdf")))
    assert d.page_count >= 1 and not d.has_text_layer and len(d.page_images()) >= 1


def test_pipeline_finds_injected_defects_and_kb_fast_path(corpus):
    res = evaluate.evaluate(corpus / "eval_a")
    s = res["summary"]
    assert s["doc_class_pct"] == 100 and s["rules_pct"] == 100 and s["attr_pct"] == 100
    assert res["by_method"]["kb"][1] == res["docs"]  # vse rychlou cestou z baze
    assert any(x["accuracy_pct"] == 100 for x in res["sweep"] if x["accuracy_pct"] is not None)


def test_hard_style_c_exposes_mock_limits(corpus):
    # mock extraktor nezna slovnik stylu C -> evaluace to poctive ukaze (tady ma v realu vyhrat vizualni model)
    s = evaluate.evaluate(corpus / "eval_c")["summary"]
    assert s["attr_pct"] == 0 and s["rules_pct"] < 60


def test_kb_unverified_do_not_vote_and_feedback_teaches(env):
    gen.make_corpus(env / "seed", 3, "A", 1, defect_rate=0, prefix="seed")
    case = gen.make_corpus(env / "x", 1, "B", 9, defect_rate=0, prefix="b")[0]
    # prazdna baze: mock klasifikuje klicovymi slovy (metoda 'mock'), nic z baze
    res = pipeline.process_case(env / "x" / case["case_id"])
    assert all(d["label_method"] in ("mock", "none") for d in res["documents"])
    assert kbase.stats()["verified"] == 0  # auto stitky nehlasuji
    doc = res["documents"][0]
    kbase.confirm_label(doc["doc_id"], "objednavka")
    assert kbase.stats()["verified"] == 1
    again = pipeline.process_case(env / "x" / case["case_id"])  # overeny stitek se zachova
    kept = next(d for d in again["documents"] if d["filename"] == doc["filename"])
    assert kept["doc_type"] == "objednavka" and kept["label_source"] == "human"


def test_semantic_layer_over_rule_results(corpus):
    for c in sorted((corpus / "eval_a").iterdir()):
        pipeline.process_case(c)
    r = semantic.query(["rule_count", "fail_count"], ["rule_type"], entity="uw_rules")
    assert {row[0] for row in r["rows"]} == {"consistency", "presence", "kyc", "signatures"}
    r = semantic.query(["fail_count"], ["rule_id"], ["failed"], entity="uw_rules")
    assert all(row[1] > 0 for row in r["rows"])
    r = semantic.query(["doc_count"], ["label_method"], entity="uw_docs")
    assert r["rows"]
    with pytest.raises(ValueError):
        semantic.query(["company_count"], entity="uw_rules")


def test_queries_and_stats(corpus):
    c = sorted((corpus / "eval_a").iterdir())[0]
    pipeline.process_case(c)
    case = queries.get_case(c.name)
    assert case["documents"] and case["rules"]
    ex = queries.explain_rule(c.name, "R04_cena")
    assert ex["rule_id"] == "R04_cena" and ex["evidence"]
    assert kbase.rule_stats("R04_cena")["cases"] >= 1
    assert queries.explain_rule(c.name, "nope")["error"]


def test_safe_case_dir_blocks_path_traversal(env):
    env.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        queries.safe_case_dir("/etc")
    with pytest.raises(ValueError):
        queries.safe_case_dir(str(env / ".." / ".."))


def test_llm_extract_request_shape_with_fake_model(corpus):
    """Vizualni cesta (bez AWS): model dostane obrazky stran a definici jen atributu daneho typu."""
    from kb.uw import extract

    seen = {}

    def fake(prompt, **kw):
        seen.update(kw, prompt=prompt)
        return {"attributes": {"NAJEMCE__Nazev": {"value": "X s.r.o.", "page": 1, "quote": "Nájemce: X s.r.o.", "confidence": 0.9},
                               "NEEXISTUJE": {"value": "zahodit"}}, "signatures": 2}

    pdf = next((corpus / "eval_a").glob("*/*.pdf"))
    get_settings().llm_provider = "bedrock"
    try:
        attrs, sigs = extract.extract("predavaci_protokol", read_pdf(pdf), fake)
    finally:
        get_settings.cache_clear()
    assert set(attrs) == {"NAJEMCE__Nazev"} and sigs == 2
    assert seen["images"] and isinstance(seen["images"][0], bytes)
    assert "PREDMET__Nazev" in seen["prompt"] and "CENA" not in seen["prompt"]


def test_bedrock_ask_builds_image_blocks():
    from kb.aws import bedrock

    seen = {}

    class Fake:
        def converse(self, **kw):
            seen.update(kw)
            return {"output": {"message": {"content": [{"text": "ok"}]}}}

    bedrock.ask("q", client=Fake(), images=[b"\x89PNG"])
    blocks = seen["messages"][0]["content"]
    assert blocks[0]["image"]["format"] == "png" and blocks[0]["image"]["source"]["bytes"] == b"\x89PNG" and blocks[-1] == {"text": "q"}


def test_mcp_uw_tools_and_agent_mock(corpus):
    import os

    case = sorted((corpus / "eval_a").iterdir())[0]

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "kb.mcp_server"], env=dict(os.environ))
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            ok = await s.call_tool("uw_process_case", {"case_dir": str(case)})
            bad = await s.call_tool("uw_process_case", {"case_dir": "/etc"})
            return names, json.loads(ok.content[0].text), json.loads(bad.content[0].text)

    names, ok, bad = asyncio.run(run())
    assert {"uw_process_case", "uw_get_case", "uw_explain_rule", "uw_find_similar_documents", "uw_record_feedback"} <= names
    assert ok["case_id"] == case.name and "REPORT" in ok["report"]
    assert "error" in bad
    out = asyncio.run(agent.ask_agent(f"Proč selhalo pravidlo R04_cena v případu {case.name}?"))
    assert out["steps"][0]["tool"] == "uw_explain_rule"
    out = asyncio.run(agent.ask_agent("Která pravidla nejčastěji selhávají?"))
    assert out["steps"][0]["tool"] == "query_metrics" and out["steps"][0]["args"]["entity"] == "uw_rules"
