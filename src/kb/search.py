"""Vektorove hledani a profil firmy. Kosinova podobnost v numpy (pro stovky tisic radku -> pgvector/OpenSearch)."""
from datetime import date

import numpy as np
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kb import embed
from kb.db import CompanyClean, CompanyVector, get_engine


def _row(c: CompanyClean) -> dict:
    d = {col.name: getattr(c, col.name) for col in CompanyClean.__table__.columns}
    for k in ("founded", "dissolved"):
        d[k] = d[k].isoformat() if d[k] else None
    d["cleaned_at"] = d["cleaned_at"].isoformat()
    return d


def search_companies(query: str, limit: int = 5, only_active: bool = False, engine: Engine | None = None) -> list[dict]:
    qv = embed.embed_texts([query])[0]
    q = (
        select(CompanyVector, CompanyClean)
        .join(CompanyClean, CompanyClean.ico == CompanyVector.ico)
        .where(CompanyVector.model == embed.model_name(), CompanyVector.dim == len(qv))
    )
    if only_active:
        q = q.where(CompanyClean.is_active)
    with Session(engine or get_engine()) as s:
        rows = s.execute(q).all()
    if not rows:
        return []
    mat = np.vstack([np.frombuffer(v.vector, dtype=np.float32) for v, _ in rows])
    scores = mat @ qv
    out = []
    for i in np.argsort(-scores)[: max(1, min(limit, 50))]:
        c = rows[i][1]
        out.append({"ico": c.ico, "name": c.name, "city": c.city, "legal_form": c.legal_form,
                    "nace": c.nace_division_label, "status": c.status, "score": round(float(scores[i]), 3)})
    return out


def flags(c: CompanyClean) -> list[str]:
    out = []
    if not c.is_active:
        out.append(f"Subjekt zanikl {c.dissolved.isoformat()}")
    if c.age_years is not None and c.age_years < 1 and c.is_active:
        out.append("Mladý subjekt (méně než 1 rok)")
    if not c.has_dic:
        out.append("Chybí DIČ")
    return out


def get_company(ico: str, engine: Engine | None = None) -> dict | None:
    ico = ico.strip().zfill(8)
    with Session(engine or get_engine()) as s:
        c = s.get(CompanyClean, ico)
        if c is None:
            return None
        return {**_row(c), "red_flags": flags(c)}
