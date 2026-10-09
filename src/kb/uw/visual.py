"""Vizualni cesta pro skeny (bez OCR): hlavicka -> segmentace vice dokumentu v jednom souboru -> klasifikace -> extrakce s eskalaci modelu.

Princip uspor: levny model (fast) na hlavicku, segmentaci a prvni pokus o extrakci; silnejsi (strong) jen kdyz je vysledek podezrely
(chybi atributy, nizka jistota, neplatne ICO, necitelna cena); nejsilnejsi (hard) az po druhem selhani.
"""
import re

from kb.aws import bedrock
from kb.clients.ares import valid_ico
from kb.config import get_settings
from kb.uw import taxonomy
from kb.uw.compare import parse_number
from kb.uw.docio import PdfDoc

SYSTEM = "Jsi pečlivý čtenář naskenovaných úvěrových dokumentů. Vycházej jen z toho, co je na stranách vidět; nic si nevymýšlej."


def transcribe_header(doc: PdfDoc, ask_json=bedrock.ask_json) -> dict:
    """TRANSKRIPCE_HLAVICKY: nazev dokumentu a prvnich radku z prvni strany (z toho se pocita vektor pro kNN)."""
    s = get_settings()
    out = ask_json("TRANSKRIPCE_HLAVICKY: Přepiš nadpis dokumentu a prvních asi 12 řádků z první strany doslova. "
                   'JSON: {"title": "<nadpis>", "header_text": "<doslovný přepis>"}',
                   system=SYSTEM, images=doc.page_images(s.uw_scan_dpi, max_pages=1, autocontrast=True),
                   model_id=s.uw_model_fast, max_tokens=800)
    return {"title": str(out.get("title", "")), "header_text": str(out.get("header_text", ""))}


def segment_pages(doc: PdfDoc, ask_json=bedrock.ask_json) -> list[tuple[int, int]]:
    """SEGMENTACE_STRAN: jeden soubor muze obsahovat vic dokumentu (PoV: dva dokumenty v jednom skenu).
    Vrati rozsahy stran (start, end) relativne k dokumentu."""
    if doc.page_count < 2:
        return [(0, doc.page_count)]
    s = get_settings()
    types = ", ".join(taxonomy.doc_types())
    out = ask_json(f"SEGMENTACE_STRAN: Pro každou přiloženou stranu (v pořadí) urči typ dokumentu z: {types}. "
                   'JSON: {"pages": [{"page": 1, "doc_type": "<typ>", "starts_new_document": true|false}]}. '
                   "Pokračování téhož dokumentu (např. 'strana 2') nezačíná nový dokument.",
                   system=SYSTEM, images=doc.page_images(70, max_pages=12, autocontrast=True), model_id=s.uw_model_fast, max_tokens=1200)
    return ranges_from_pages(out.get("pages") or [], doc.page_count)


def ranges_from_pages(pages: list[dict], n: int) -> list[tuple[int, int]]:
    starts = [0]
    prev = None
    for i, p in enumerate(sorted(pages, key=lambda x: x.get("page", 0))[:n]):
        dt = p.get("doc_type")
        if i > 0 and (p.get("starts_new_document") or (prev and dt != prev)):
            starts.append(i)
        prev = dt
    starts = sorted(set(starts))
    return [(a, starts[k + 1] if k + 1 < len(starts) else n) for k, a in enumerate(starts)]


def text_segments(doc: PdfDoc) -> list[tuple[int, int]]:
    """Mock/textova vrstva: nova strana zacina novy dokument, pokud je v jejim nadpisu jiny typ nez v predchozi strane."""
    from kb.uw.classify import _norm, keyword_classify

    starts, cur = [0], None
    for i, t in enumerate(doc.page_texts):
        head = _norm(t[:100])
        hit = next((n for n, spec in taxonomy.doc_types().items() if _norm(spec["title"]) in head), None)
        if hit is None and i > 0:
            continue
        if i > 0 and hit != cur:
            starts.append(i)
        cur = hit or keyword_classify(t)[0]
    return [(a, starts[k + 1] if k + 1 < len(starts) else doc.page_count) for k, a in enumerate(starts)]


def needs_escalation(doc_type: str, attrs: dict[str, dict]) -> str | None:
    """Duvod k eskalaci na silnejsi model (nebo None)."""
    expected = taxonomy.doc_types()[doc_type]["attributes"]
    if expected and len(attrs) < 0.6 * len(expected):
        return f"nalezeno jen {len(attrs)}/{len(expected)} atributů"
    low = [a for a, v in attrs.items() if float(v.get("confidence") or 0) < get_settings().uw_min_conf]
    if low:
        return f"nízká jistota: {', '.join(low)}"
    for a, v in attrs.items():
        val = str(v.get("value", ""))
        if a.endswith("__ICO") and not valid_ico(re.sub(r"\s", "", val)):
            return f"neplatné IČO v {a}: {val!r}"
        if a == "CENA" and parse_number(val) is None:
            return f"nečitelná cena: {val!r}"
    return None


def extract_with_escalation(doc_type: str, doc: PdfDoc, extract_fn, ask_json=bedrock.ask_json) -> tuple[dict, int | None, list[str]]:
    """extract_fn(doc_type, doc, ask_json, model_id, dpi, use_images) -> (attrs, signatures). Vraci (attrs, podpisy, log eskalaci)."""
    s = get_settings()
    use_images = not doc.has_text_layer
    log: list[str] = []
    attrs, sigs = extract_fn(doc_type, doc, ask_json, s.uw_model_fast, s.uw_scan_dpi, use_images)
    for model, dpi in ((s.uw_model_strong, s.uw_escalate_dpi), (s.uw_model_hard, s.uw_escalate_dpi)):
        why = needs_escalation(doc_type, attrs)
        if not why:
            break
        log.append(f"{why} -> {model}")
        better, bsigs = extract_fn(doc_type, doc, ask_json, model, dpi, use_images)
        # nova hodnota prebije jen kdyz ji model udal; u nenalezenych ponechame puvodni
        attrs = {**attrs, **better}
        sigs = bsigs if bsigs is not None else sigs
    return attrs, sigs, log
