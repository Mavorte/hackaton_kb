"""KYB demo UI.   streamlit run src/kb/ui.py

Offline bez AWS a site:   LLM_PROVIDER=mock OFFLINE=1 streamlit run src/kb/ui.py
"""
import httpx
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from kb import kyb
from kb.clients import vies
from kb.clients.ares import Ares, Company, NotFound, valid_ico
from kb.config import get_settings
from kb.db import CompanyRow, get_engine, upsert_company

RISK_LABEL = {"low": "Nízké riziko", "medium": "Střední riziko", "high": "Vysoké riziko"}
RISK_BOX = {"low": st.success, "medium": st.warning, "high": st.error}


def _lookup(query: str) -> list[Company]:
    query = query.strip()
    if query.replace(" ", "").isdigit():
        ico = query.replace(" ", "")
        if not valid_ico(ico):
            st.error("Neplatné IČO (nesedí kontrolní součet).")
            return []
        return [Ares().get(ico)]
    return Ares().search(query, limit=10)


def _show_company(c: Company) -> None:
    st.subheader(c.name)
    col1, col2, col3 = st.columns(3)
    col1.metric("IČO", c.ico)
    col2.metric("DIČ", c.dic or "—")
    col3.metric("Stav", "Zaniklá" if c.dissolved else "Aktivní")
    st.write(f"**Sídlo:** {c.address or '—'}")
    st.write(f"**Vznik:** {c.founded or '—'}" + (f" · **Zánik:** {c.dissolved}" if c.dissolved else ""))
    st.write(f"**Právní forma (kód):** {c.legal_form or '—'} · **CZ-NACE:** {', '.join(c.nace) or '—'}")


def _show_assessment(c: Company) -> None:
    with st.spinner("Posuzuji…"):
        result = kyb.assess(c)
    risk = result.get("risk", "medium")
    RISK_BOX.get(risk, st.info)(f"{RISK_LABEL.get(risk, risk)}")
    st.write(result.get("summary", ""))
    flags = result.get("red_flags") or []
    if flags:
        st.markdown("**Red flags**")
        for f in flags:
            st.markdown(f"- {f}")
    else:
        st.write("Žádné red flags.")
    if result.get("mock"):
        st.caption("Posouzení je z mock režimu (pravidla místo LLM).")
    with st.expander("Surová odpověď"):
        st.json(result)


def _body() -> None:
    st.set_page_config(page_title="KYB demo", page_icon="🏢", layout="wide")
    s = get_settings()
    st.title("KYB v pár vteřinách")
    st.caption(
        f"Zdroj dat: {'ukázková (offline)' if s.offline else 'ARES'} · "
        f"LLM: {'mock' if s.llm_provider == 'mock' else s.bedrock_model_id}"
    )

    query = st.text_input("IČO nebo název firmy", key="query", placeholder="např. 28100018 nebo Demo")
    if not query:
        st.info("Zadej IČO nebo část názvu firmy.")
        return

    try:
        found = _lookup(query)
    except NotFound:
        st.warning("IČO nebylo nalezeno.")
        return
    except httpx.HTTPError as e:
        st.error(f"Registr je nedostupný: {e}")
        return

    if not found:
        st.warning("Nic nenalezeno.")
        return

    if len(found) == 1:
        company = found[0]
    else:
        labels = [f"{c.name} ({c.ico})" for c in found]
        company = found[labels.index(st.selectbox("Nalezeno více firem", labels))]

    upsert_company(company)
    _show_company(company)
    st.divider()

    left, right = st.columns(2)
    with left:
        st.markdown("### KYB posouzení")
        try:
            _show_assessment(company)
        except Exception as e:  # LLM / AWS chyba nesmi shodit demo
            st.error(f"Posouzení se nepovedlo: {e}")
    with right:
        st.markdown("### Plátce DPH (VIES)")
        try:
            v = vies.check_vat("CZ", company.ico)
            (st.success if v.get("isValid") else st.warning)("Platný plátce DPH" if v.get("isValid") else "Neplatný / nenalezeno")
            st.json({k: v.get(k) for k in ("name", "address") if v.get(k)})
        except Exception as e:
            st.error(f"VIES nedostupný: {e}")


def _set_query(ico: str) -> None:
    st.session_state["query"] = ico


def _history() -> None:
    with st.sidebar:
        st.header("Poslední ověřené")
        with Session(get_engine()) as session:
            rows = session.scalars(select(CompanyRow).order_by(CompanyRow.fetched_at.desc()).limit(10)).all()
        if not rows:
            st.write("Zatím nic.")
        for r in rows:
            st.button(f"{r.name} ({r.ico})", key=f"hist-{r.ico}", use_container_width=True,
                      on_click=_set_query, args=(r.ico,))


def main() -> None:
    _body()
    _history()


main()
