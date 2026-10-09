"""Znalostni baze dokumentu: kNN nad vektory prvni strany, overene stitky (seed/human), zpetna vazba.

Hlasuji jen dokumenty s label_source in (seed, human). Stroje prirazene stitky (auto) cekaji na potvrzeni,
aby se chyby nesnezily do dalsich rozhodnuti.
"""
import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kb import embed
from kb.config import get_settings
from kb.db import UwCase, UwCaseVector, UwDocument, UwFeedback, UwRuleResult, UwVector, get_engine

VERIFIED = ("seed", "human")


def neighbors(vec: np.ndarray, k: int = 5, verified_only: bool = True, exclude_case: str | None = None,
              engine: Engine | None = None) -> list[dict]:
    q = (
        select(UwVector, UwDocument)
        .join(UwDocument, UwDocument.doc_id == UwVector.doc_id)
        .where(UwVector.model == embed.model_name(), UwVector.dim == len(vec))
    )
    if verified_only:
        q = q.where(UwDocument.label_source.in_(VERIFIED))
    if exclude_case:
        q = q.where(UwDocument.case_id != exclude_case)
    with Session(engine or get_engine()) as s:
        rows = s.execute(q).all()
    if not rows:
        return []
    mat = np.vstack([np.frombuffer(v.vector, dtype=np.float32) for v, _ in rows])
    sims = mat @ vec
    out = []
    for i in np.argsort(-sims)[:k]:
        d = rows[i][1]
        out.append({"doc_id": d.doc_id, "case_id": d.case_id, "filename": d.filename, "doc_type": d.doc_type,
                    "label_source": d.label_source, "similarity": round(float(sims[i]), 3)})
    return out


def classify_by_kb(vec: np.ndarray, exclude_case: str | None = None, engine: Engine | None = None) -> tuple[dict | None, list[dict]]:
    """Rychla cesta: vrati ({doc_type, confidence}, sousedi), nebo (None, sousedi) kdyz si baze neni jista."""
    s = get_settings()
    nb = neighbors(vec, s.uw_kb_k, True, exclude_case, engine)
    if not nb or nb[0]["similarity"] < s.uw_kb_min_sim:
        return None, nb
    close = [n for n in nb if n["similarity"] >= s.uw_kb_min_sim]
    votes: dict[str, float] = {}
    for n in close:
        votes[n["doc_type"]] = votes.get(n["doc_type"], 0.0) + n["similarity"]
    label, score = max(votes.items(), key=lambda kv: kv[1])
    agree = score / sum(votes.values())
    if agree < s.uw_kb_min_agree:
        return None, nb
    return {"doc_type": label, "confidence": round(agree * close[0]["similarity"], 3)}, nb


def exemplars(doc_type: str, k: int = 3, engine: Engine | None = None) -> list[dict]:
    with Session(engine or get_engine()) as s:
        rows = s.scalars(select(UwDocument).where(UwDocument.doc_type == doc_type, UwDocument.label_source.in_(VERIFIED))
                         .order_by(UwDocument.doc_id).limit(k)).all()
    return [{"doc_id": d.doc_id, "filename": d.filename, "text": d.first_page_text[:600]} for d in rows]


def confirm_label(doc_id: int, doc_type: str, engine: Engine | None = None) -> dict:
    """Clovek potvrdi nebo opravi stitek -> dokument se stane overenym prikladem."""
    with Session(engine or get_engine()) as s:
        d = s.get(UwDocument, doc_id)
        if d is None:
            return {"error": f"Dokument {doc_id} neexistuje."}
        s.add(UwFeedback(doc_id=doc_id, old_label=d.doc_type, new_label=doc_type))
        changed = d.doc_type != doc_type
        d.doc_type, d.label_source, d.label_method = doc_type, "human", "human"
        s.commit()
        return {"doc_id": doc_id, "doc_type": doc_type, "corrected": changed}


def case_precedents(case_id: str, k: int = 3, engine: Engine | None = None) -> list[dict]:
    """Podobne drivejsi pripady (vektor shrnuti) a pravidla, ktera v nich selhala."""
    with Session(engine or get_engine()) as s:
        me = s.get(UwCaseVector, (case_id, embed.model_name()))
        if me is None:
            return []
        qv = np.frombuffer(me.vector, dtype=np.float32)
        others = s.scalars(select(UwCaseVector).where(UwCaseVector.model == me.model, UwCaseVector.case_id != case_id)).all()
        if not others:
            return []
        sims = np.vstack([np.frombuffer(o.vector, dtype=np.float32) for o in others]) @ qv
        out = []
        for i in np.argsort(-sims)[:k]:
            cid = others[i].case_id
            c = s.get(UwCase, cid)
            fails = s.scalars(select(UwRuleResult.rule_id).where(UwRuleResult.case_id == cid, UwRuleResult.is_fail)).all()
            out.append({"case_id": cid, "similarity": round(float(sims[i]), 3), "product": c.product if c else None,
                        "failed_rules": list(fails)})
        return out


def rule_stats(rule_id: str, engine: Engine | None = None) -> dict:
    with Session(engine or get_engine()) as s:
        rows = s.scalars(select(UwRuleResult).where(UwRuleResult.rule_id == rule_id)).all()
    n = len(rows)
    fails = [r for r in rows if r.is_fail]
    return {"rule_id": rule_id, "cases": n, "fails": len(fails), "fail_rate_pct": round(100 * len(fails) / n, 1) if n else None,
            "example_messages": sorted({r.message for r in fails})[:3]}


def clear_case(case_id: str, s: Session) -> dict[str, tuple[str, str]]:
    """Smaze drivejsi zpracovani pripadu; vrati {filename: (doc_type, label_source)} pro zachovani overenych stitku."""
    docs = s.scalars(select(UwDocument).where(UwDocument.case_id == case_id)).all()
    keep = {d.filename: (d.doc_type, d.label_source) for d in docs if d.label_source in VERIFIED}
    ids = [d.doc_id for d in docs]
    if ids:
        s.execute(delete(UwVector).where(UwVector.doc_id.in_(ids)))
    for tbl in ("UwAttribute",):
        from kb import db
        s.execute(delete(getattr(db, tbl)).where(getattr(db, tbl).case_id == case_id))
    s.execute(delete(UwDocument).where(UwDocument.case_id == case_id))
    s.execute(delete(UwRuleResult).where(UwRuleResult.case_id == case_id))
    s.execute(delete(UwCase).where(UwCase.case_id == case_id))
    s.execute(delete(UwCaseVector).where(UwCaseVector.case_id == case_id))
    s.flush()
    return keep


def stats(engine: Engine | None = None) -> dict:
    with Session(engine or get_engine()) as s:
        docs = s.scalars(select(UwDocument)).all()
    verified = [d for d in docs if d.label_source in VERIFIED]
    by_type: dict[str, int] = {}
    for d in verified:
        by_type[d.doc_type or "?"] = by_type.get(d.doc_type or "?", 0) + 1
    return {"documents": len(docs), "verified": len(verified), "pending": len(docs) - len(verified), "verified_by_type": by_type}
