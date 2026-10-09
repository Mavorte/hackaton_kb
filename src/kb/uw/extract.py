"""Extrakce atributu: mock (text PDF + stitky z taxonomie) nebo vizualni LLM s dukazem (strana + citace)."""
import re

from kb.aws import bedrock
from kb.config import get_settings
from kb.uw import taxonomy
from kb.uw.docio import PdfDoc


def mock_extract(doc_type: str, pages: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for attr in taxonomy.doc_types()[doc_type]["attributes"]:
        for label in taxonomy.attributes()[attr]["labels"]:
            pat = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", re.I | re.M)
            for i, text in enumerate(pages, 1):
                m = pat.search(text)
                if m:
                    out[attr] = {"value": m.group(1), "page": i, "quote": m.group(0).strip(), "confidence": 0.9}
                    break
            if attr in out:
                break
    return out


def llm_extract(doc_type: str, doc: PdfDoc, ask_json=bedrock.ask_json) -> tuple[dict[str, dict], int | None]:
    """Vizualni extrakce: modelu se posilaji obrazky stranek (zadne OCR) a definice jen atributu pro dany typ dokumentu."""
    defs = "\n".join(f"- {a}: {taxonomy.attributes()[a]['description']} (na dokumentu tisteno napr. jako: {', '.join(taxonomy.attributes()[a]['labels'][:3])})"
                     for a in taxonomy.doc_types()[doc_type]["attributes"])
    prompt = (f"Z přiložených stran dokumentu typu '{doc_type}' vytáhni tyto atributy:\n{defs}\n\n"
              'Odpověz JSON: {"attributes": {"<ATRIBUT>": {"value": "<přesně jak je v dokumentu>", "page": <číslo strany od 1>, '
              '"quote": "<doslovná citace>", "confidence": 0-1}}, "signatures": <počet podpisů na dokumentu>}. '
              "Nenalezený atribut vynech. Nic si nevymýšlej.")
    out = ask_json(prompt, system="Jsi extraktor údajů z úvěrových dokumentů. Odpovídej pouze daty z dokumentu.",
                   images=doc.page_images(), max_tokens=2000)
    attrs = {k: v for k, v in (out.get("attributes") or {}).items() if k in taxonomy.doc_types()[doc_type]["attributes"] and v.get("value")}
    sigs = out.get("signatures")
    return attrs, int(sigs) if isinstance(sigs, (int, float)) else None


def extract(doc_type: str, doc: PdfDoc, ask_json=bedrock.ask_json) -> tuple[dict[str, dict], int]:
    """Vrati (atributy, pocet podpisu)."""
    if get_settings().llm_provider == "mock" or ask_json is None:
        return mock_extract(doc_type, doc.page_texts), doc.signatures
    attrs, sigs = llm_extract(doc_type, doc, ask_json)
    return attrs, sigs if sigs is not None else doc.signatures
