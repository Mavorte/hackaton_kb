"""Textovy report pripadu (podobny strukture z PoV: kategorizace, pritomnost, podpisy, vysledky kontrol)."""
from kb.uw import taxonomy

ICON = {"PASS": "OK  ", "FAIL": "CHYBA", "NA": "n/a "}


def build_report(res: dict) -> str:
    lines = [f"REPORT PŘÍPADU {res['case_id']}", f"Produkt: {res.get('product') or '-'}    IČO nájemce: {res.get('company_ico') or '-'}", ""]
    lines.append("Kategorizace dokumentů:")
    for d in res["documents"]:
        t = taxonomy.doc_types().get(d["doc_type"] or "", {}).get("title", "NEROZPOZNÁNO")
        lines.append(f"  {d['filename']:<34} -> {t:<30} [{d['label_method']}, {d['confidence']:.2f}]  podpisů: {d['signatures']}")
    lines += ["", "Výsledky kontrol:"]
    for r in res["rules"]:
        lines.append(f"  [{ICON[r['outcome']]}] {r['rule_id']:<22} {r['message']}")
    by = {}
    for r in res["rules"]:
        by.setdefault(r["rule_type"], [0, 0])
        by[r["rule_type"]][1] += 1
        by[r["rule_type"]][0] += r["outcome"] == "PASS"
    ok = sum(v[0] for v in by.values())
    tot = sum(v[1] for v in by.values())
    lines += ["", "Úspěšnost kontrol: " + ", ".join(f"{k}: {v[0]}/{v[1]}" for k, v in by.items()) + f"  | celkem {ok}/{tot}"]
    lines.append("Závěr: " + ("bez nálezů" if not res["failed_rules"] else "k ručnímu posouzení - " + ", ".join(res["failed_rules"])))
    return "\n".join(lines)
