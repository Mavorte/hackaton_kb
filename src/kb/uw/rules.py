"""Rules engine: pravidla z taxonomy.yaml (consistency, presence, kyc, signatures). Zadna logika pro konkretni pripad v kodu."""
from collections import Counter
from dataclasses import dataclass, field

from kb.clients.ares import Ares, Company, NotFound, valid_ico
from kb.uw import taxonomy
from kb.uw.compare import equal, norm_text


@dataclass
class RuleResult:
    rule_id: str
    rule_type: str
    outcome: str  # PASS | FAIL | NA
    message: str = ""
    details: dict = field(default_factory=dict)
    description: str = ""


def _values(docs: list[dict], attr: str) -> list[tuple[str, str]]:
    return [(d["filename"], d["attrs"][attr]["value"]) for d in docs if attr in d["attrs"] and d["attrs"][attr]["value"]]


def _consistency(rule: dict, docs: list[dict], judge) -> RuleResult:
    attr = rule["attribute"]
    vals = _values(docs, attr)
    mode = taxonomy.attributes()[attr]["compare"]
    if len(vals) < 2:
        return RuleResult(rule["id"], "consistency", "NA", f"Hodnota {attr} nalezena jen v {len(vals)} dokumentu.", {"values": dict(vals)})
    ref_file, ref = Counter(v for _, v in vals).most_common(1)[0][0], None
    ref = Counter(v for _, v in vals).most_common(1)[0][0]
    bad = [(f, v) for f, v in vals if not equal(mode, ref, v, judge)]
    if bad:
        return RuleResult(rule["id"], "consistency", "FAIL", f"{attr}: neshoda ({ref!r} vs. " + "; ".join(f"{f}: {v!r}" for f, v in bad) + ")",
                          {"values": dict(vals), "reference": ref})
    return RuleResult(rule["id"], "consistency", "PASS", f"{attr} je shodné ve {len(vals)} dokumentech.", {"values": dict(vals)})


def _presence(rule: dict, docs: list[dict]) -> RuleResult:
    types = {d["doc_type"] for d in docs if d["doc_type"]}
    missing = [t for t in rule["required"] if t not in types]
    extra = sorted(types - set(rule["required"]) - set(rule.get("optional", [])))
    details = {"present": sorted(types), "missing": missing, "extra": extra}
    if missing:
        return RuleResult(rule["id"], "presence", "FAIL", "Chybí dokumenty: " + ", ".join(missing), details)
    return RuleResult(rule["id"], "presence", "PASS", "Všechny povinné dokumenty jsou přítomny." + (f" Navíc: {', '.join(extra)}." if extra else ""), details)


def _ares_lookup(docs: list[dict], ares: Ares) -> tuple[str | None, Company | None, list[str]]:
    icos = [v for _, v in _values(docs, "NAJEMCE__ICO")]
    if not icos:
        return None, None, []
    for ico, _ in Counter(icos).most_common():
        if valid_ico(ico):
            try:
                return ico, ares.get(ico), icos
            except NotFound:
                continue
            except Exception as e:  # ARES nedostupny
                raise RuntimeError(str(e))
    return Counter(icos).most_common(1)[0][0], None, icos


def _kyc(rule: dict, docs: list[dict], ares: Ares, judge) -> RuleResult:
    rid, check = rule["id"], rule["check"]
    try:
        ico, company, icos = _ares_lookup(docs, ares)
    except RuntimeError as e:
        return RuleResult(rid, "kyc", "NA", f"ARES není dostupný: {e}")
    if not icos:
        return RuleResult(rid, "kyc", "NA", "V dokumentech nebylo nalezeno IČO nájemce.")
    if check == "ico_exists":
        bad = []
        for v in sorted(set(icos)):
            if not valid_ico(v):
                bad.append(f"{v} (neplatné IČO)")
                continue
            try:
                ares.get(v)
            except NotFound:
                bad.append(f"{v} (není v ARES)")
        if bad:
            return RuleResult(rid, "kyc", "FAIL", "IČO: " + ", ".join(bad), {"icos": sorted(set(icos))})
        return RuleResult(rid, "kyc", "PASS", f"IČO {ico} existuje v ARES.", {"ico": ico})
    if company is None:
        return RuleResult(rid, "kyc", "NA", "Firma nebyla v ARES nalezena.", {"ico": ico})
    if check == "name_matches":
        names = _values(docs, "NAJEMCE__Nazev")
        bad = [(f, v) for f, v in names if not equal("text", company.name, v, judge)]
        if bad:
            return RuleResult(rid, "kyc", "FAIL", f"ARES: {company.name!r}; v dokumentech: " + "; ".join(f"{f}: {v!r}" for f, v in bad),
                              {"ares_name": company.name})
        return RuleResult(rid, "kyc", "PASS", f"Název odpovídá ARES ({company.name}).", {"ares_name": company.name})
    if check == "active":
        if company.dissolved:
            return RuleResult(rid, "kyc", "FAIL", f"Subjekt zanikl {company.dissolved}.", {"dissolved": company.dissolved})
        return RuleResult(rid, "kyc", "PASS", "Subjekt je aktivní.")
    return RuleResult(rid, "kyc", "NA", f"Neznámá kontrola {check}.")


def _signatures(rule: dict, docs: list[dict]) -> RuleResult:
    found = [d for d in docs if d["doc_type"] == rule["doc_type"]]
    if not found:
        return RuleResult(rule["id"], "signatures", "NA", f"Dokument {rule['doc_type']} není v případu.")
    n = max(d["signatures"] for d in found)
    ok = n >= rule["min"]
    return RuleResult(rule["id"], "signatures", "PASS" if ok else "FAIL", f"Podpisů: {n} (požadováno alespoň {rule['min']}).", {"count": n})


def run_rules(docs: list[dict], ares: Ares | None = None, judge=None) -> list[RuleResult]:
    ares = ares or Ares()
    out = []
    for r in taxonomy.rules():
        t = r["type"]
        res = (_consistency(r, docs, judge) if t == "consistency" else _presence(r, docs) if t == "presence"
               else _kyc(r, docs, ares, judge) if t == "kyc" else _signatures(r, docs))
        res.description = r.get("description", "")
        out.append(res)
    return out
