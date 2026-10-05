"""    uvicorn kb.api:app --reload      ->  http://localhost:8000/docs"""
import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from kb.aws import bedrock
from kb.clients import sanctions, vies
from kb.clients.ares import Ares, Company, NotFound, valid_ico
from kb.db import CompanyRow, get_engine, table_names, upsert_company

app = FastAPI(title="KB hackathon toolkit")


@app.exception_handler(httpx.HTTPError)
def upstream_error(_, exc: httpx.HTTPError):
    return JSONResponse({"detail": f"Upstream chyba: {exc}"}, status_code=502)


@app.get("/health")
def health():
    return {"ok": True}


# --- domena: firmy / KYB ---------------------------------------------------

def _ares_get(ico: str) -> Company:
    if not valid_ico(ico):
        raise HTTPException(422, "Neplatne ICO (kontrolni soucet)")
    try:
        c = Ares().get(ico)
    except NotFound:
        raise HTTPException(404, "ICO nenalezeno v ARES")
    upsert_company(c)
    return c


@app.get("/ares/search")
def ares_search(name: str, limit: int = Query(10, le=100)):
    return [c.model_dump(exclude={"raw"}) for c in Ares().search(name, limit)]


@app.get("/ares/{ico}")
def ares_get(ico: str, raw: bool = False):
    return _ares_get(ico).model_dump(exclude=None if raw else {"raw"})


@app.get("/ares/{ico}/vr")
def ares_vr(ico: str):
    try:
        return Ares().get_vr(ico)
    except NotFound:
        raise HTTPException(404, "Neni ve verejnem rejstriku")


@app.get("/vies/{country}/{number}")
def vies_check(country: str, number: str):
    return vies.check_vat(country, number)


@app.get("/sanctions")
def sanctions_match(name: str, country: str = "cz"):
    try:
        return sanctions.match_company(name, country)
    except RuntimeError as e:
        raise HTTPException(501, str(e))


@app.get("/companies")
def companies(limit: int = Query(50, le=500)):
    with Session(get_engine()) as s:
        rows = s.scalars(select(CompanyRow).limit(limit)).all()
        return [{c.name: getattr(r, c.name) for c in CompanyRow.__table__.columns if c.name != "raw"} for r in rows]


# --- LLM -------------------------------------------------------------------

class Prompt(BaseModel):
    prompt: str
    system: str | None = None


@app.post("/llm")
def llm(p: Prompt):
    return {"answer": bedrock.ask(p.prompt, system=p.system)}


@app.get("/kyb/{ico}")
def kyb(ico: str):
    """Mini KYB: ARES -> LLM shrnuti + red flags (kostra pro demo)."""
    c = _ares_get(ico)
    result = bedrock.ask_json(
        "Jsi KYB analytik banky. Z techto dat z ARES vytvor strucne shrnuti a seznam red flags "
        '(napr. zaniklý subjekt, mlady subjekt, chybejici DIC). Format: {"summary": str, "red_flags": [str], "risk": "low|medium|high"}\n\n'
        + c.model_dump_json(),
        system="Odpovidej cesky. Vychazej jen z dodanych dat, nic si nevymyslej.",
    )
    return {"company": c.model_dump(exclude={"raw"}), "assessment": result}


# --- generic CRUD-read nad libovolnou tabulkou (po kb.loader) ----------------

@app.get("/tables")
def tables():
    return table_names()


@app.get("/tables/{name}")
def table_rows(name: str, request: Request, limit: int = Query(50, le=1000), offset: int = 0):
    """Filtrovani: /tables/platby?mena=CZK&castka=100 (rovnost na sloupci)."""
    if name not in table_names():
        raise HTTPException(404, "Tabulka neexistuje")
    engine = get_engine()
    t = Table(name, MetaData(), autoload_with=engine)
    q = select(t)
    for k, v in request.query_params.items():
        if k in ("limit", "offset"):
            continue
        if k not in t.c:
            raise HTTPException(422, f"Neznamy sloupec: {k}")
        q = q.where(t.c[k] == v)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(q.limit(limit).offset(offset))]
