"""Evaluace proti truth.json (jako tabulka vysledku v PoV): klasifikace, atributy, podpisy, pravidla, podil rychle cesty z baze.

    python -m kb.uw.evaluate data/uw/eval_a [data/uw/eval_b ...]
Vyhodnocuje se bez uceni (automaticke stitky nehlasuji), takze je mereni ferove.
"""
import json
import sys
from pathlib import Path

from kb.aws import bedrock
from kb.uw import pipeline, taxonomy
from kb.uw.compare import equal


def _pct(k: int, n: int) -> float | None:
    return round(100 * k / n, 1) if n else None


def evaluate(cases_dir: str | Path, engine=None, ask_json=bedrock.ask_json, ares=None) -> dict:
    cases = sorted(p for p in Path(cases_dir).iterdir() if (p / "truth.json").exists())
    m = {"cases": len(cases), "docs": 0, "doc_ok": 0, "attr": 0, "attr_ok": 0, "sig": 0, "sig_ok": 0, "rules": 0, "rules_ok": 0,
         "by_method": {}, "defect": {}, "sims": []}
    errors = []
    for cdir in cases:
        truth = json.loads((cdir / "truth.json").read_text(encoding="utf-8"))
        res = pipeline.process_case(cdir, engine, ask_json=ask_json, ares=ares)
        got = {d["filename"]: d for d in res["documents"]}
        for fname, dt in truth["files"].items():
            d = got.get(fname)
            m["docs"] += 1
            ok = bool(d and d["doc_type"] == dt)
            m["doc_ok"] += ok
            meth = (d or {}).get("label_method", "none")
            bm = m["by_method"].setdefault(meth, [0, 0])
            bm[0] += ok
            bm[1] += 1
            if d and d["neighbors"]:
                m["sims"].append((d["neighbors"][0]["similarity"], d["neighbors"][0]["doc_type"] == dt))
            if not ok:
                errors.append(f"{truth['case_id']}/{fname}: {dt} -> {d['doc_type'] if d else None}")
            if not d:
                continue
            for attr, tv in truth["attributes"][fname].items():
                m["attr"] += 1
                gv = d["attrs"].get(attr, {}).get("value")
                m["attr_ok"] += bool(gv is not None and equal(taxonomy.attributes()[attr]["compare"], tv, gv))
            m["sig"] += 1
            m["sig_ok"] += d["signatures"] == truth["signatures"][fname]
        out = {r["rule_id"]: r["outcome"] for r in res["rules"]}
        for rid, exp in truth["expected_rules"].items():
            m["rules"] += 1
            m["rules_ok"] += out.get(rid) == exp
            if out.get(rid) != exp:
                errors.append(f"{truth['case_id']}/{rid}: ocekavano {exp}, zjisteno {out.get(rid)}")
        for dfx in truth["defects"]:
            hit = all(out.get(rid) == "FAIL" for rid, exp in truth["expected_rules"].items() if exp == "FAIL")
            dd = m["defect"].setdefault(dfx, [0, 0])
            dd[0] += hit
            dd[1] += 1
    kb = m["by_method"].get("kb", [0, 0])
    m["summary"] = {"doc_class_pct": _pct(m["doc_ok"], m["docs"]), "attr_pct": _pct(m["attr_ok"], m["attr"]),
                    "sig_pct": _pct(m["sig_ok"], m["sig"]), "rules_pct": _pct(m["rules_ok"], m["rules"]),
                    "kb_fast_path_pct": _pct(kb[1], m["docs"]), "kb_accuracy_pct": _pct(kb[0], kb[1])}
    m["errors"] = errors
    # kalibrace prahu: pro kazdy prah podil dokumentu, ktere by kNN pokrylo, a jeho presnost
    m["sweep"] = []
    for th in (0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95):
        sel = [ok for sim, ok in m["sims"] if sim >= th]
        m["sweep"].append({"threshold": th, "coverage_pct": _pct(len(sel), len(m["sims"])), "accuracy_pct": _pct(sum(sel), len(sel))})
    return m


def format_table(name: str, m: dict) -> str:
    s = m["summary"]
    lines = [f"{name}: {m['cases']} případů, {m['docs']} dokumentů, {m['rules']} pravidel",
             f"  Klasifikace dokumentů {s['doc_class_pct']} %  | Atributy {s['attr_pct']} %  | Podpisy {s['sig_pct']} %  | Pravidla {s['rules_pct']} %",
             f"  Rychlá cesta z báze: {s['kb_fast_path_pct']} % dokumentů (přesnost {s['kb_accuracy_pct']} %)",
             "  Metoda klasifikace: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in m["by_method"].items())]
    if m["defect"]:
        lines.append("  Odhalení chyb: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(m["defect"].items())))
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for d in sys.argv[1:]:
        r = evaluate(d)
        print(format_table(Path(d).name, r))
        for e in r["errors"][:8]:
            print("   !", e)
