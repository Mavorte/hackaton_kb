"""ZIVY test pripojeni: ty (SSO) i nas program (Bedrock) na skutecnem uctu. Vola modely a stoji male penize (radove USD).

    aws sso login --profile hackathon
    LIVE_AWS=1 python -m pytest tests/test_live_aws.py -v -s        (Windows PowerShell: $env:LIVE_AWS="1"; python -m pytest ...)

Bez LIVE_AWS=1 se vse preskoci, takze to nerusi bezne testy. Profil a region se berou z .env (AWS_PROFILE, AWS_REGION).
"""
import asyncio
import io
import json
import os
import random

import pytest

from kb import db, embed, ingest
from kb.aws import bedrock
from kb.config import get_settings
from kb.uw import gen, pipeline
from kb.uw.prices import estimate_cost

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("LIVE_AWS") != "1", reason="nastav LIVE_AWS=1 (a prihlas se: aws sso login)")]


@pytest.fixture(autouse=True)
def live_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("OFFLINE", "1")  # ARES nepotrebujeme, pravidla pojedou nad vzorkem
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/live.db")
    monkeypatch.setenv("UW_CASES_DIR", str(tmp_path / "uw"))
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    bedrock.meter.reset()
    yield tmp_path / "uw"
    get_settings.cache_clear()
    db.get_engine.cache_clear()


@pytest.fixture
def embeddings(monkeypatch):
    """Titan nemusi byt na hackathonovem uctu -> kdyz selze, pouzije se mock hash (stejne jako EMBED_PROVIDER=mock)."""
    try:
        embed.embed_texts(["test"])
        return "bedrock"
    except Exception as e:  # noqa: BLE001
        print(f"\n  ! Embeddingy z Bedrocku nejdou ({str(e)[:90]}) -> EMBED_PROVIDER=mock")
        monkeypatch.setenv("EMBED_PROVIDER", "mock")
        get_settings.cache_clear()
        return "mock"


def test_01_identity_and_region():
    s = get_settings()
    ident = bedrock.session().client("sts").get_caller_identity()
    print(f"\n  ucet {ident['Account']} | {ident['Arn']} | region {s.aws_region}")
    assert s.aws_region == "eu-central-1", "Hackathonovy ucet povoluje jen Frankfurt (AWS_REGION=eu-central-1)"


@pytest.mark.parametrize("label", ["bedrock_model_id", "uw_model_fast", "uw_model_strong", "uw_model_hard"])
def test_02_each_model_answers(label):
    model = getattr(get_settings(), label)
    out = bedrock.ask("Odpovez jednim slovem: ahoj", model_id=model, max_tokens=20)
    print(f"\n  {label} {model}: {out.strip()[:30]}")
    assert out.strip()


def test_03_image_input_works():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (260, 90), "white")
    ImageDraw.Draw(img).text((20, 35), "FAKTURA 42", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    out = bedrock.ask("Co je napsano na obrazku? Odpovez jen textem z obrazku.", model_id=get_settings().uw_model_fast,
                      max_tokens=30, images=[buf.getvalue()])
    print(f"\n  odpoved: {out.strip()}")
    assert "42" in out


def test_04_embeddings(embeddings):
    vec = embed.embed_texts(["Smlouva o úvěru"])[0]
    print(f"\n  embeddingy: {embeddings}, model {embed.model_name()}, dim {len(vec)}")
    assert len(vec) > 100


def test_05_program_processes_a_text_case(live_env, embeddings):
    """Nas program na skutecnem Bedrocku: klasifikace + extrakce z textu + pravidla; ma najit vlozenou chybu v cene."""
    t = gen.make_case(live_env, "live_text", gen.load_companies()[3], random.Random(1), "A", ["price_mismatch"])
    res = pipeline.process_case(live_env / "live_text")
    got = {d["filename"]: d["doc_type"] for d in res["documents"]}
    ok = sum(got.get(f) == dt for f, dt in t["files"].items())
    print(f"\n  klasifikace {ok}/{len(t['files'])}, selhala pravidla: {res['failed_rules']}, naklad ~{res['cost']['usd']} USD, "
          f"volani: {sum(u['calls'] for u in res['usage'].values())}")
    assert ok >= len(t["files"]) - 1
    assert "R04_cena" in res["failed_rules"]


def test_06_program_processes_a_scan_case(live_env, embeddings):
    """Vizualni cesta: sken bez textove vrstvy (a se sloucenymi dokumenty) -> hlavicka, segmentace, klasifikace, extrakce z obrazku."""
    t = gen.make_case(live_env, "live_scan", gen.load_companies()[5], random.Random(2), "A", [], scan=True, merge=True)
    res = pipeline.process_case(live_env / "live_scan")
    by_base: dict[str, list] = {}
    for d in res["documents"]:
        by_base.setdefault(d["filename"].split("[")[0], []).append(d["doc_type"])
    ok = sum(sorted(map(str, by_base.get(f, []))) == sorted(v if isinstance(v, list) else [v]) for f, v in t["files"].items())
    print(f"\n  soubory spravne rozdelene a zarazene: {ok}/{len(t['files'])}, selhala pravidla: {res['failed_rules']}, "
          f"naklad ~{res['cost']['usd']} USD, volani: {sum(u['calls'] for u in res['usage'].values())}")
    assert all(d["scan"] for d in res["documents"])
    assert ok >= len(t["files"]) - 1


def test_07_agent_uses_mcp_tools_with_real_model(embeddings):
    """Agent: Bedrock Converse tool-use smycka volajici nastroje MCP serveru nad nactenymi daty."""
    from kb.agent import ask_agent

    ingest.ingest_samples()
    out = asyncio.run(ask_agent("Kolik je firem podle právní formy? Použij nástroje."))
    print(f"\n  nastroje: {[s['tool'] for s in out['steps']]}\n  odpoved: {out['answer'][:200]}")
    assert out["steps"], "agent nezavolal zadny nastroj"
    assert out["answer"].strip()


def test_99_spend_summary():
    usage = bedrock.meter.snapshot()
    print(f"\n  tokeny podle modelu: {json.dumps(usage)}\n  odhad nakladu: {estimate_cost(usage)}")
