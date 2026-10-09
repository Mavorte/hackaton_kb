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


def llm_extract(doc_type: str, doc: PdfDoc, ask_json=bedrock.ask_json, model_id: str | None = None, dpi: int | None = None,
                use_images: bool = True) -> tuple[dict[str, dict], int | None]:
    """Extrakce s dukazem. Sken: obrazky stran (zadne OCR). Born-digital PDF: staci text (levnejsi), podpisy z vektoru."""
    s = get_settings()
    defs = "\n".join(f"- {a}: {taxonomy.attributes()[a]['description']} (tisteno napr. jako: {', '.join(taxonomy.attributes()[a]['labels'][:3])})"
                     for a in taxonomy.doc_types()[doc_type]["attributes"])
    text = "" if use_images else "\n\n".join(f"[strana {i}]\n{t}" for i, t in enumerate(doc.page_texts, 1))
    sig_q = ', "signatures": <počet vlastnoručních podpisů na dokumentu; razítko není podpis>' if use_images else ""
    prompt = (f"Z dokumentu typu '{doc_type}' vytáhni tyto atributy:\n{defs}\n\n{text}\n"
              'Odpověz JSON: {"attributes": {"<ATRIBUT>": {"value": "<přesně jak je v dokumentu>", "page": <strana od 1>, '
              f'"quote": "<doslovná citace>", "confidence": 0-1}}}}{sig_q}. Nenalezený atribut vynech. Nic si nevymýšlej.')
    kw = {"images": doc.page_images(dpi or s.uw_scan_dpi, autocontrast=True)} if use_images else {}
    if model_id:
        kw["model_id"] = model_id
    out = ask_json(prompt, system="Jsi extraktor údajů z úvěrových dokumentů. Odpovídej pouze daty z dokumentu.", max_tokens=2000, **kw)
    valid = taxonomy.doc_types()[doc_type]["attributes"]
    attrs = {k: v for k, v in (out.get("attributes") or {}).items() if k in valid and isinstance(v, dict) and v.get("value")}
    sigs = out.get("signatures")
    return attrs, int(sigs) if isinstance(sigs, (int, float)) else None


def extract_ex(doc_type: str, doc: PdfDoc, ask_json=bedrock.ask_json) -> tuple[dict[str, dict], int, list[str]]:
    """Vrati (atributy, pocet podpisu, log eskalaci modelu)."""
    if get_settings().llm_provider == "mock" or ask_json is None:
        return mock_extract(doc_type, doc.page_texts), doc.signatures, []
    from kb.uw import visual

    attrs, sigs, log = visual.extract_with_escalation(doc_type, doc, llm_extract, ask_json)
    return attrs, sigs if sigs is not None else doc.signatures, log


def extract(doc_type: str, doc: PdfDoc, ask_json=bedrock.ask_json) -> tuple[dict[str, dict], int]:
    attrs, sigs, _ = extract_ex(doc_type, doc, ask_json)
    return attrs, sigs
