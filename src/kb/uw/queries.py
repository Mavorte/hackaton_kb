"""Cteni vysledku zpracovanych pripadu z DB (pro MCP, UI a agenta)."""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kb.config import get_settings
from kb.db import UwAttribute, UwCase, UwDocument, UwRuleResult, get_engine
from kb.uw import taxonomy
from kb.uw.report import build_report


def safe_case_dir(path: str) -> Path:
    """MCP/agent smi zpracovavat jen slozky pod UW_CASES_DIR (zadne cteni libovolnych cest)."""
    base = Path(get_settings().uw_cases_dir).resolve()
    p = Path(path).resolve()
    if base != p and base not in p.parents:
        raise ValueError(f"Cesta musí být pod {base}.")
    if not p.is_dir():
        raise ValueError("Složka případu neexistuje.")
    return p


def list_cases(engine: Engine | None = None) -> list[dict]:
    with Session(engine or get_engine()) as s:
        cases = s.scalars(select(UwCase).order_by(UwCase.case_id)).all()
        out = []
        for c in cases:
            fails = s.scalars(select(UwRuleResult.rule_id).where(UwRuleResult.case_id == c.case_id, UwRuleResult.is_fail)).all()
            out.append({"case_id": c.case_id, "product": c.product, "company_ico": c.company_ico, "failed_rules": list(fails)})
        return out


def get_case(case_id: str, engine: Engine | None = None) -> dict | None:
    with Session(engine or get_engine()) as s:
        c = s.get(UwCase, case_id)
        if c is None:
            return None
        docs = s.scalars(select(UwDocument).where(UwDocument.case_id == case_id).order_by(UwDocument.filename)).all()
        attrs = s.scalars(select(UwAttribute).where(UwAttribute.case_id == case_id)).all()
        rules = s.scalars(select(UwRuleResult).where(UwRuleResult.case_id == case_id).order_by(UwRuleResult.rule_id)).all()
        by_doc: dict[int, dict] = {}
        for a in attrs:
            by_doc.setdefault(a.doc_id, {})[a.attribute] = {"value": a.value, "page": a.page, "quote": a.quote, "confidence": a.confidence}
        documents = [{"doc_id": d.doc_id, "filename": d.filename, "doc_type": d.doc_type, "label_method": d.label_method,
                      "label_source": d.label_source, "confidence": d.label_confidence or 0.0, "signatures": d.signatures,
                      "attrs": by_doc.get(d.doc_id, {}), "neighbors": []} for d in docs]
        res = {"case_id": case_id, "product": c.product, "company_ico": c.company_ico, "documents": documents,
               "rules": [{"rule_id": r.rule_id, "rule_type": r.rule_type, "outcome": r.outcome, "message": r.message, "details": r.details}
                         for r in rules],
               "failed_rules": [r.rule_id for r in rules if r.is_fail]}
        res["report"] = build_report(res)
        return res


def explain_rule(case_id: str, rule_id: str, engine: Engine | None = None) -> dict:
    """Proc pravidlo selhalo: popis pravidla, vysledek a dukazy (hodnoty atributu ze vsech dokumentu s citaci a stranou)."""
    case = get_case(case_id, engine)
    if case is None:
        return {"error": f"Případ {case_id} neexistuje."}
    rule = next((r for r in case["rules"] if r["rule_id"] == rule_id), None)
    spec = next((r for r in taxonomy.rules() if r["id"] == rule_id), None)
    if rule is None or spec is None:
        return {"error": f"Pravidlo {rule_id} v případu není."}
    evidence = []
    attr = spec.get("attribute") or ("NAJEMCE__ICO" if spec["type"] == "kyc" else None)
    if attr:
        for d in case["documents"]:
            if attr in d["attrs"]:
                evidence.append({"filename": d["filename"], "doc_type": d["doc_type"], "attribute": attr, **d["attrs"][attr]})
    return {"case_id": case_id, "rule_id": rule_id, "description": spec.get("description"), "outcome": rule["outcome"],
            "message": rule["message"], "details": rule["details"], "evidence": evidence}
