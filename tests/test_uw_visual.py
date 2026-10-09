"""Vizualni cesta pro skeny, ovena na 'falesnem modelu' (zadne volani AWS): hlavicka -> segmentace -> kNN/LLM klasifikace -> extrakce s eskalaci."""
import hashlib
import json
import random
from pathlib import Path

import pytest

from kb import db
from kb.config import get_settings
from kb.uw import evaluate, gen, kbase, pipeline, taxonomy, visual
from kb.uw.docio import read_pdf


@pytest.fixture
def real_env(monkeypatch, tmp_path):
    """llm_provider=bedrock (vizualni cesta), ale embeddingy mock a modely nahrazene falesnym ask_json."""
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("EMBED_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/v.db")
    monkeypatch.setenv("UW_CASES_DIR", str(tmp_path / "uw"))
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    yield tmp_path / "uw"
    get_settings.cache_clear()
    db.get_engine.cache_clear()


def _h(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


class FakeVision:
    """Chova se jako ideální model: odpovi podle truth.json, ale jen na zaklade obrazku stran (hash -> soubor, strana).
    Prvni (fast) model u jedne vybrane smlouvy vrati neplatne ICO s nizkou jistotou -> musi se eskalovat na strong."""

    def __init__(self, case_dir: Path, truth: dict):
        self.truth, self.calls, self.break_first = truth, [], True
        self.by_hash: dict[str, tuple[str, int]] = {}
        s = get_settings()
        for fname in truth["files"]:
            d = read_pdf(case_dir / fname)
            for dpi in (s.uw_scan_dpi, s.uw_escalate_dpi, 70):
                for p in range(d.page_count):
                    self.by_hash[_h(d.sub(p, p + 1).page_images(dpi, 1, autocontrast=True)[0])] = (fname, p)
        self.pages = {f: read_pdf(case_dir / f).page_count for f in truth["files"]}

    def _where(self, images):
        return self.by_hash[_h(images[0])]

    def _types_by_page(self, fname):
        t = self.truth["files"][fname]
        types = t if isinstance(t, list) else [t]
        n, per = self.pages[fname], max(1, self.pages[fname] // len(types))
        return [types[min(i // per, len(types) - 1)] for i in range(n)]

    def __call__(self, prompt, system=None, images=None, model_id=None, **kw):
        self.calls.append((prompt.split(":")[0][:20], model_id, len(images or [])))
        if prompt.startswith("TRANSKRIPCE_HLAVICKY"):
            fname, p = self._where(images)
            dt = self._types_by_page(fname)[p]
            lines = [f"{gen.LABELS[self.truth['style']].get(dt, {}).get(a, a)}: {v}" for a, v in self.truth["attributes"][fname].items()
                     if a in taxonomy.doc_types()[dt]["attributes"]]
            title = gen.TITLES[self.truth["style"]][dt]
            return {"title": title, "header_text": "\n".join([title] + lines)}
        if prompt.startswith("SEGMENTACE_STRAN"):
            fname, _ = self._where(images)
            ty = self._types_by_page(fname)
            return {"pages": [{"page": i + 1, "doc_type": t, "starts_new_document": i > 0 and t != ty[i - 1]} for i, t in enumerate(ty)]}
        if prompt.startswith("Zařaď dokument"):
            fname, p = self._where(images)
            return {"doc_type": self._types_by_page(fname)[p], "confidence": 0.9, "reason": "vizuálně"}
        if "vytáhni tyto atributy" in prompt:
            fname, p = self._where(images)
            dt = self._types_by_page(fname)[p]
            attrs = {a: {"value": v, "page": 1, "quote": f"{a}: {v}", "confidence": 0.95}
                     for a, v in self.truth["attributes"][fname].items() if a in taxonomy.doc_types()[dt]["attributes"]}
            if self.break_first and model_id == get_settings().uw_model_fast and "NAJEMCE__ICO" in attrs and dt == "prohlaseni_o_rucenii":
                attrs["NAJEMCE__ICO"] = {"value": "1234", "page": 1, "quote": "?", "confidence": 0.4}
            sigs = self.truth["signatures"][fname] if not isinstance(self.truth["files"][fname], list) else self.pages[fname] // 2 + 0
            return {"attributes": attrs, "signatures": sigs}
        if prompt.startswith("Jsou tyto"):
            return {"same": False}
        raise AssertionError(f"neznamy prompt: {prompt[:60]}")


def _scan_case(root, seed=5, merge=True, defects=()):
    t = gen.make_case(root, "scan_x", gen.load_companies()[5], random.Random(seed), "A", list(defects), scan=True, merge=merge)
    return root / "scan_x", t


def test_header_segmentation_ranges():
    assert visual.ranges_from_pages([{"page": 1, "doc_type": "a", "starts_new_document": False},
                                     {"page": 2, "doc_type": "b", "starts_new_document": True}], 2) == [(0, 1), (1, 2)]
    assert visual.ranges_from_pages([{"page": 1, "doc_type": "a"}, {"page": 2, "doc_type": "a"}], 2) == [(0, 2)]


def test_needs_escalation_rules():
    ok = {"NAJEMCE__Nazev": {"value": "X", "confidence": 0.9}, "NAJEMCE__ICO": {"value": "29000076", "confidence": 0.9}}
    assert visual.needs_escalation("prohlaseni_o_rucenii", ok) is None
    assert "neplatné IČO" in visual.needs_escalation("prohlaseni_o_rucenii", {**ok, "NAJEMCE__ICO": {"value": "1234", "confidence": 0.9}})
    assert "nízká jistota" in visual.needs_escalation("prohlaseni_o_rucenii", {**ok, "NAJEMCE__Nazev": {"value": "X", "confidence": 0.3}})
    assert "nalezeno jen" in visual.needs_escalation("smlouva_o_uveru", ok)
    full = {k: {"value": "29000076" if k.endswith("__ICO") else "x", "confidence": 1} for k in taxonomy.doc_types()["objednavka"]["attributes"]}
    assert visual.needs_escalation("objednavka", {**full, "CENA": {"value": "485 000 Kč", "confidence": 1}}) is None
    assert "nečitelná cena" in visual.needs_escalation("objednavka", {**full, "CENA": {"value": "abc", "confidence": 1}})


def test_scan_end_to_end_with_segmentation_and_escalation(real_env):
    case_dir, truth = _scan_case(real_env, merge=True)
    assert any(isinstance(v, list) for v in truth["files"].values())
    assert not read_pdf(next(case_dir.glob("*.pdf"))).has_text_layer
    fake = FakeVision(case_dir, truth)
    res = pipeline.process_case(case_dir, ask_json=fake)

    merged = next(f for f, v in truth["files"].items() if isinstance(v, list))
    segs = [d for d in res["documents"] if d["filename"].startswith(merged)]
    assert [d["doc_type"] for d in segs] == truth["files"][merged]  # sloucene dokumenty rozdeleny podle stran
    assert all(d["scan"] for d in res["documents"])
    # klasifikace sla pres LLM (prazdna baze) a hlavicku precetl levny model
    assert {d["label_method"] for d in res["documents"]} == {"llm"}
    assert any(c[0] == "TRANSKRIPCE_HLAVICKY" and c[1] == get_settings().uw_model_fast for c in fake.calls)
    # eskalace: fast vratil neplatne ICO u prohlaseni -> strong to opravil
    guar = next(d for d in res["documents"] if d["doc_type"] == "prohlaseni_o_rucenii")
    assert guar["escalations"] and guar["attrs"]["NAJEMCE__ICO"]["value"] == truth["company_ico"]
    assert any(c[1] == get_settings().uw_model_strong for c in fake.calls if c[0].startswith("Z dokumentu typu"))
    assert res["failed_rules"] == []  # bez vlozenych chyb vse projde
    assert res["usage"] == {} or isinstance(res["usage"], dict)  # fake nevola Converse, meter je prazdny


def test_scan_defect_detected_after_visual_extraction(real_env):
    case_dir, truth = _scan_case(real_env, seed=6, merge=False, defects=["price_mismatch"])
    res = pipeline.process_case(case_dir, ask_json=FakeVision(case_dir, truth))
    assert "R04_cena" in res["failed_rules"]


def test_kb_fast_path_skips_llm_classification_for_scans(real_env, monkeypatch):
    """Overene priklady v bazi -> kNN rozhodne po precteni hlavicky; klasifikacni LLM volani uz neni potreba."""
    d1, t1 = _scan_case(real_env, seed=7, merge=False)
    f1 = FakeVision(d1, t1)
    pipeline.process_case(d1, ask_json=f1)
    # potvrdime stitky cloveka -> overene priklady
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from kb.db import UwDocument, get_engine
    with Session(get_engine()) as s:
        ids = [(x.doc_id, x.doc_type) for x in s.scalars(select(UwDocument))]
    for i, t in ids:
        kbase.confirm_label(i, t)
    # s jedinym seed pripadem je podobnost 0.5-0.8 (nizsi nez vychozi prah 0.75 -> radsi se zeptat modelu); pro test prah snizime
    monkeypatch.setenv("UW_KB_MIN_SIM", "0.45")
    get_settings.cache_clear()
    # novy sken stejneho stylu
    real_env2 = real_env / "second"
    t2 = gen.make_case(real_env2, "scan_y", gen.load_companies()[8], random.Random(8), "A", [], scan=True)
    d2 = real_env2 / "scan_y"
    f2 = FakeVision(d2, t2)
    res = pipeline.process_case(d2, ask_json=f2)
    docs = res["documents"]
    via_kb = [d for d in docs if d["label_method"] == "kb"]
    assert len(via_kb) >= 3  # jednoznacne dokumenty vyresi baze bez LLM
    # kde byly dva typy blizko sebe (napr. objednavka vs. smlouva), baze se radsi neprepocitala a zeptala se modelu
    assert sum(1 for c in f2.calls if c[0].startswith("Zařaď dokument")) == len(docs) - len(via_kb)
    assert {d["filename"]: d["doc_type"] for d in docs} == t2["files"]  # a vsechno je spravne


def test_evaluate_counts_merged_files(real_env):
    root = real_env / "set"
    gen.make_corpus(root, 3, "A", 11, defect_rate=0.0, scan=True, merge_rate=1.0, prefix="m")
    # eval s falesnym modelem pro kazdy pripad zvlast
    fakes = {}

    def ask(prompt, system=None, images=None, model_id=None, **kw):
        for cdir in sorted(root.iterdir()):
            if cdir.name not in fakes:  # setdefault by pocital hashe vsech stran znovu pri kazdem volani
                fakes[cdir.name] = FakeVision(cdir, json.loads((cdir / "truth.json").read_text(encoding="utf-8")))
            fk = fakes[cdir.name]
            fk.break_first = False
            if images and _h(images[0]) in fk.by_hash:
                return fk(prompt, system, images, model_id, **kw)
        return {"same": False}

    r = evaluate.evaluate(root, ask_json=ask)
    assert r["docs"] > r["cases"] * 4  # sloucene soubory se pocitaji jako vice dokumentu
    assert r["summary"]["doc_class_pct"] == 100 and r["summary"]["attr_pct"] == 100 and r["summary"]["rules_pct"] == 100
