"""Klasifikace dokumentu: 1) kNN ze znalostni baze (bez LLM), 2) LLM s priklady z baze, 3) mock podle klicovych slov."""
import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from kb.aws import bedrock
from kb.config import get_settings
from kb.uw import kbase, taxonomy


@dataclass
class Classification:
    doc_type: str | None
    confidence: float
    method: str  # kb | llm | mock | none
    neighbors: list[dict] = field(default_factory=list)
    reason: str = ""


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t.casefold()) if not unicodedata.combining(c))


def keyword_classify(text: str) -> tuple[str | None, float]:
    """Mock: skore podle poctu klicovych slov z taxonomie (titulek ma vyssi vahu)."""
    t, head = _norm(text), _norm(text[:120])
    best, best_score = None, 0.0
    for name, spec in taxonomy.doc_types().items():
        score = 0.0
        for kw in spec["keywords"]:
            k = _norm(kw)
            score += (2.0 if k in head else 1.0) if k in t else 0.0
        if score > best_score:
            best, best_score = name, score
    return (best, min(0.95, 0.4 + 0.1 * best_score)) if best else (None, 0.0)


def _llm_classify(text: str, images: list[bytes] | None, nb: list[dict], ask_json=bedrock.ask_json, model_id: str | None = None) -> Classification:
    types = "\n".join(f"- {k}: {v['description']}" for k, v in taxonomy.doc_types().items())
    shots = ""
    for n in nb[:3]:
        shots += f"\nPodobný ověřený dokument ({n['doc_type']}, podobnost {n['similarity']}): {n['filename']}"
    prompt = (f"Zařaď dokument do jednoho z typů.\nTypy:\n{types}\n{shots}\n\nText první strany:\n{text[:2500] or '(bez textové vrstvy, viz obrázek)'}\n\n"
              'Odpověz JSON: {"doc_type": "<klíč typu>", "confidence": 0-1, "reason": "<krátce>"}')
    kw = {"images": images} if images else {}
    if model_id:
        kw["model_id"] = model_id
    out = ask_json(prompt, system="Jsi klasifikátor dokumentů banky. Vyber jen z uvedených typů.", **kw)
    dt = out.get("doc_type")
    if dt not in taxonomy.doc_types():
        return Classification(None, 0.0, "llm", nb, "neplatný typ z LLM")
    return Classification(dt, float(out.get("confidence", 0.5)), "llm", nb, out.get("reason", ""))


def classify(text: str, vec: np.ndarray, images=None, exclude_case: str | None = None, engine=None, ask_json=bedrock.ask_json) -> Classification:
    kb_hit, nb = kbase.classify_by_kb(vec, exclude_case, engine)
    if kb_hit:
        return Classification(kb_hit["doc_type"], kb_hit["confidence"], "kb", nb, "shoda s ověřenými dokumenty v bázi")
    if get_settings().llm_provider == "mock":
        dt, conf = keyword_classify(text)
        return Classification(dt, conf, "mock" if dt else "none", nb, "klíčová slova z taxonomie")
    s = get_settings()
    c = _llm_classify(text, images, nb, ask_json, s.uw_model_strong if images else None)
    return c
