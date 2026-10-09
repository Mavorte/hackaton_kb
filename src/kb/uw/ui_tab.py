"""Streamlit zalozka: underwritingovy pripad (dokumenty -> pravidla -> report) a znalostni baze, ktera se uci z potvrzeni."""
import json
from pathlib import Path

import streamlit as st

from kb.config import get_settings
from kb.uw import gen, kbase, pipeline, queries, taxonomy

OUTCOME = {"PASS": st.success, "FAIL": st.error, "NA": st.info}
LABEL = {"PASS": "OK", "FAIL": "CHYBA", "NA": "n/a"}


def _cases_root() -> Path:
    return Path(get_settings().uw_cases_dir)


def _generate_and_seed() -> str:
    root = _cases_root()
    gen.make_corpus(root / "seed", 12, "A", 1, defect_rate=0.3, prefix="seed")
    gen.make_corpus(root / "eval_a", 8, "A", 2, prefix="evalA")
    gen.make_corpus(root / "eval_b", 8, "B", 3, prefix="evalB")
    gen.make_corpus(root / "eval_c", 4, "C", 4, prefix="evalC")
    for c in sorted((root / "seed").iterdir()):
        pipeline.seed_case(c)
    return f"Vygenerováno a do znalostní báze nahráno {len(list((root / 'seed').iterdir()))} ověřených případů."


def _show_result(res: dict) -> None:
    st.markdown("#### Dokumenty")
    titles = {k: v["title"] for k, v in taxonomy.doc_types().items()}
    st.dataframe([{"soubor": d["filename"], "typ": titles.get(d["doc_type"], "NEROZPOZNÁNO"), "určeno": d["label_method"],
                   "štítek": d["label_source"], "jistota": round(d["confidence"], 2), "podpisů": d["signatures"]} for d in res["documents"]],
                 use_container_width=True, hide_index=True)

    st.markdown("#### Atributy napříč dokumenty")
    attrs = sorted({a for d in res["documents"] for a in d["attrs"]})
    rows = [{"atribut": a, **{d["filename"][:22]: d["attrs"].get(a, {}).get("value", "") for d in res["documents"]}} for a in attrs]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("#### Pravidla")
    for r in res["rules"]:
        OUTCOME[r["outcome"]](f"**{LABEL[r['outcome']]}** · {r['rule_id']} — {r['message']}")

    with st.expander("Textový report"):
        st.code(res["report"])
    prec = kbase.case_precedents(res["case_id"], 3)
    if prec:
        with st.expander("Podobné dřívější případy (předpověď rizika)"):
            st.dataframe(prec, use_container_width=True, hide_index=True)

    with st.expander("Potvrdit nebo opravit typ dokumentu (znalostní báze se učí)"):
        doc = st.selectbox("Dokument", res["documents"], format_func=lambda d: f"{d['filename']} ({d['doc_type']}, {d['label_source']})")
        names = list(taxonomy.doc_types())
        new = st.selectbox("Správný typ", names, index=names.index(doc["doc_type"]) if doc["doc_type"] in names else 0)
        if st.button("Uložit štítek jako ověřený"):
            out = kbase.confirm_label(doc["doc_id"], new)
            st.success(f"Uloženo ({'opraveno' if out.get('corrected') else 'potvrzeno'}). Příští podobný dokument se zařadí rychlou cestou.")


def uw_tab() -> None:
    root = _cases_root()
    st.caption("Pipeline: PDF → klasifikace (kNN ze znalostní báze, jinak model) → extrakce atributů s důkazem → pravidla z YAML → report. "
               + ("Mock režim: čtení textové vrstvy PDF." if get_settings().llm_provider == "mock" else "Vizuální model čte obrázky stran."))
    s = kbase.stats()
    left, right = st.columns([3, 1])
    left.write(f"Znalostní báze: **{s['verified']}** ověřených dokumentů ({', '.join(f'{k}: {v}' for k, v in s['verified_by_type'].items()) or 'prázdná'}), "
               f"{s['pending']} čeká na potvrzení.")
    if right.button("Vygenerovat vzorová data", use_container_width=True):
        with st.spinner("Generuji syntetické případy a plním znalostní bázi…"):
            st.success(_generate_and_seed())
        st.rerun()

    folders = sorted(p for d in root.glob("*") if d.is_dir() and d.name != "seed" for p in d.iterdir() if p.is_dir()) if root.exists() else []
    if not folders:
        st.info("Zatím nejsou žádné případy. Klikni na „Vygenerovat vzorová data“.")
        return
    case = st.selectbox("Případ", folders, format_func=lambda p: f"{p.parent.name}/{p.name}")
    truth_file = case / "truth.json"
    if truth_file.exists():
        t = json.loads(truth_file.read_text(encoding="utf-8"))
        st.caption(f"Styl dokumentů: {t['style']} · vložené chyby (jen pro kontrolu): {', '.join(t['defects']) or 'žádné'}")
    if st.button("Zpracovat případ", type="primary"):
        with st.spinner("Zpracovávám…"):
            st.session_state["uw_result"] = pipeline.process_case(case)
    res = st.session_state.get("uw_result")
    if res and res["case_id"] == case.name:
        _show_result(res)
    elif (stored := queries.get_case(case.name)):
        _show_result(stored)
