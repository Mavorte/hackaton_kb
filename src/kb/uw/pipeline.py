"""Zpracovani pripadu: PDF -> klasifikace (kNN / LLM) -> extrakce -> pravidla -> report; vse se uklada do DB.

    python -m kb.uw.pipeline data/uw/eval_a/evalA_001
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kb import embed
from kb.aws import bedrock
from kb.config import get_settings
from kb.db import UwAttribute, UwCase, UwCaseVector, UwDocument, UwRuleResult, UwVector, get_engine
from kb.uw import classify as cls
from kb.uw import extract as ext
from kb.uw import kbase, visual
from kb.uw.prices import estimate_cost
from kb.uw.docio import read_pdf
from kb.uw.report import build_report
from kb.uw.rules import run_rules


def _vec_bytes(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _judge(ask_json):
    """LLM rozhodce pro hranicni textove shody (jen mimo mock rezim)."""
    if get_settings().llm_provider == "mock" or ask_json is None:
        return None

    def judge(a: str, b: str) -> bool:
        out = ask_json(f'Jsou tyto dva zápisy tentýž údaj (tolerance OCR, mezer, diakritiky, ale NE jiný název)?\nA: {a}\nB: {b}\nJSON: {{"same": true|false}}',
                       system="Odpovídej jen JSON.", max_tokens=100)
        return bool(out.get("same"))

    return judge


def _segments(doc, ask_json, scan: bool) -> list[tuple[int, int]]:
    """Vice dokumentu v jednom souboru: sken -> vizualni segmentace (levny model); born-digital -> zdarma podle nadpisu stran."""
    if doc.page_count < 2:
        return [(0, doc.page_count)]
    if get_settings().llm_provider == "mock" or ask_json is None:
        return visual.text_segments(doc) if doc.has_text_layer else [(0, doc.page_count)]
    return visual.segment_pages(doc, ask_json) if scan else visual.text_segments(doc)


def _seed_label(v, idx: int) -> str:
    return v[idx] if isinstance(v, list) else v


def process_case(case_dir: str | Path, engine: Engine | None = None, seed_labels: dict | None = None,
                 learn: bool = False, ask_json=bedrock.ask_json, ares=None) -> dict:
    """seed_labels: {nazev_souboru: doc_type | [doc_type, ...]} -> dokumenty se ulozi jako overene (seed) a uci znalostni bazi.
    Automaticke stitky se ukladaji jako 'auto' (nehlasuji, dokud je clovek nepotvrdi)."""
    case_dir = Path(case_dir)
    engine = engine or get_engine()
    case_id = case_dir.name
    cfg = get_settings()
    real = cfg.llm_provider != "mock" and ask_json is not None
    bedrock.meter.reset()
    pdfs = sorted(case_dir.glob("*.pdf"), key=lambda p: p.name.lower())
    docs_out: list[dict] = []

    with Session(engine) as s:
        keep = kbase.clear_case(case_id, s)
        for pdf in pdfs:
            full = read_pdf(pdf)
            scan = not full.has_text_layer
            segs = _segments(full, ask_json, scan)
            for si, (a, b) in enumerate(segs):
                doc = full if len(segs) == 1 else full.sub(a, b)
                name = pdf.name if len(segs) == 1 else f"{pdf.name}[s{a + 1}-{b}]"
                seg_scan = not doc.has_text_layer
                images, text = None, doc.first_page_text
                if seg_scan and real:
                    hdr = visual.transcribe_header(doc, ask_json)  # sken: hlavicku precte levny model, z ni se pocita vektor pro kNN
                    text = hdr["header_text"] or hdr["title"]
                    images = doc.page_images(cfg.uw_scan_dpi, max_pages=2, autocontrast=True)
                vec = embed.embed_texts([text[:2000] or pdf.stem])[0]
                if seed_labels and pdf.name in seed_labels:
                    c = cls.Classification(_seed_label(seed_labels[pdf.name], si), 1.0, "truth", [], "seed ze znamého štítku")
                    source = "seed"
                elif name in keep:
                    c = cls.Classification(keep[name][0], 1.0, "human", [], "ověřený štítek z dřívějška")
                    source = keep[name][1]
                else:
                    c = cls.classify(text, vec, images, exclude_case=case_id, engine=engine, ask_json=ask_json)
                    source = "auto"
                attrs, sigs, esc = ext.extract_ex(c.doc_type, doc, ask_json) if c.doc_type else ({}, doc.signatures, [])

                row = UwDocument(case_id=case_id, filename=name, page_count=doc.page_count, doc_type=c.doc_type, label_source=source,
                                 label_method=c.method, label_confidence=c.confidence, first_page_text=text[:3000], signatures=sigs)
                s.add(row)
                s.flush()
                s.add(UwVector(doc_id=row.doc_id, model=embed.model_name(), dim=len(vec), vector=_vec_bytes(vec)))
                for at, v in attrs.items():
                    s.add(UwAttribute(case_id=case_id, doc_id=row.doc_id, doc_type=c.doc_type, attribute=at, value=str(v["value"]),
                                      page=v.get("page"), quote=v.get("quote"), confidence=v.get("confidence")))
                docs_out.append({"doc_id": row.doc_id, "filename": name, "doc_type": c.doc_type, "label_method": c.method,
                                 "label_source": source, "confidence": c.confidence, "neighbors": c.neighbors, "reason": c.reason,
                                 "attrs": attrs, "signatures": sigs, "pages": doc.page_count, "scan": seg_scan, "escalations": esc})

        results = run_rules(docs_out, ares, _judge(ask_json))
        icos = [d["attrs"]["NAJEMCE__ICO"]["value"] for d in docs_out if "NAJEMCE__ICO" in d["attrs"]]
        product = next((d["attrs"]["PREDMET__Nazev"]["value"] for d in docs_out if "PREDMET__Nazev" in d["attrs"]), None)
        company_ico = Counter(icos).most_common(1)[0][0] if icos else None
        failed = [r.rule_id for r in results if r.outcome == "FAIL"]
        summary = (f"Produkt: {product or 'neznámý'}. Dokumenty: {', '.join(sorted({d['doc_type'] or '?' for d in docs_out}))}. "
                   f"Selhaná pravidla: {', '.join(failed) or 'žádná'}.")
        s.add(UwCase(case_id=case_id, product=product, company_ico=company_ico, summary=summary))
        cv = embed.embed_texts([summary])[0]
        s.add(UwCaseVector(case_id=case_id, model=embed.model_name(), dim=len(cv), vector=_vec_bytes(cv)))
        for r in results:
            s.add(UwRuleResult(case_id=case_id, product=product, rule_id=r.rule_id, rule_type=r.rule_type, outcome=r.outcome,
                               is_fail=r.outcome == "FAIL", message=r.message, details=r.details))
        s.commit()

    usage = bedrock.meter.snapshot()
    result = {"case_id": case_id, "product": product, "company_ico": company_ico, "documents": docs_out,
              "rules": [r.__dict__ for r in results], "failed_rules": failed, "usage": usage, "cost": estimate_cost(usage)}
    result["report"] = build_report(result)
    return result


def seed_case(case_dir: str | Path, engine: Engine | None = None) -> dict:
    """Zpracuje pripad se spravnymi stitky z truth.json -> prida overene priklady do znalostni baze."""
    truth = json.loads((Path(case_dir) / "truth.json").read_text(encoding="utf-8"))
    return process_case(case_dir, engine, seed_labels=truth["files"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(process_case(sys.argv[1])["report"])
