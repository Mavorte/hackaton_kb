"""Benchmark cteni skenu: OCR (Amazon Textract) + LLM  vs.  multimodalni model primo z obrazku.

    aws sso login --profile hackathon
    python scripts/bench_extraction.py --cases 6 --hard 3            # ~ jednotky USD, par minut (limit 50 req/min)
    python scripts/bench_extraction.py --dry-run                      # jen overi, ze skript funguje (bez AWS)

Varianty: textract+regex (jen OCR), textract+LLM (Haiku), Haiku vision, Nova 2 Lite vision (+ Sonnet vision s --with-strong).
Mere presnost atributu proti truth.json (tolerantne = bez diakritiky/mezer; presne = jen mezery a velikost pismen, tedy drzi diakritiku),
cas a spotrebu tokenu. Typ dokumentu se bere z truth, meri se tedy jen extrakce.
"""
import argparse
import re
import tempfile
import time
from pathlib import Path

from kb.aws import bedrock
from kb.config import get_settings
from kb.uw import extract, gen, taxonomy
from kb.uw.compare import equal, parse_number
from kb.uw.docio import PdfDoc, read_pdf
from kb.uw.prices import estimate_cost

NOVA = "eu.amazon.nova-2-lite-v1:0"


def strict_equal(mode: str, a: str, b: str) -> bool:
    if mode == "number":
        x, y = parse_number(a), parse_number(b)
        return x is not None and x == y
    return re.sub(r"\s", "", a).casefold() == re.sub(r"\s", "", b).casefold()


class Textract:
    def __init__(self, dry: bool):
        self.dry, self.pages = dry, 0
        if not dry:
            self.client = bedrock.session().client("textract")

    def pages_text(self, doc: PdfDoc) -> list[str]:
        out = []
        for png in doc.page_images(150, max_pages=8):
            self.pages += 1
            if self.dry:
                out.append("")
                continue
            r = self.client.detect_document_text(Document={"Bytes": png})
            out.append("\n".join(b["Text"] for b in r["Blocks"] if b["BlockType"] == "LINE"))
        return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=6, help="pripady stylu A (skeny)")
    ap.add_argument("--hard", type=int, default=3, help="pripady stylu C (jina slovni zasoba, skeny)")
    ap.add_argument("--with-strong", action="store_true", help="pridat Sonnet 4.5 vision")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    s = get_settings()
    root = Path(tempfile.mkdtemp(prefix="bench_"))
    cases = gen.make_corpus(root / "a", a.cases, "A", 21, defect_rate=0, scan=True, prefix="A")
    cases += gen.make_corpus(root / "c", a.hard, "C", 22, defect_rate=0, scan=True, prefix="C")
    tex = Textract(a.dry_run)

    def ask(prompt, **kw):
        if a.dry_run:
            return {"attributes": {}, "signatures": 0}
        return bedrock.ask_json(prompt, **kw)

    variants = {
        "textract + regex (jen OCR)": lambda dt, doc, ocr: (extract.mock_extract(dt, ocr), None),
        f"textract + LLM ({s.uw_model_fast.split('.')[-1][:22]})": lambda dt, doc, ocr: extract.llm_extract(
            dt, PdfDoc(doc.path, ocr, [0] * len(ocr)), ask, s.uw_model_fast, None, False),
        f"vision {s.uw_model_fast.split('.')[-1][:22]}": lambda dt, doc, ocr: extract.llm_extract(dt, doc, ask, s.uw_model_fast, s.uw_scan_dpi, True),
        "vision Nova 2 Lite": lambda dt, doc, ocr: extract.llm_extract(dt, doc, ask, NOVA, s.uw_scan_dpi, True),
    }
    if a.with_strong:
        variants[f"vision {s.uw_model_strong.split('.')[-1][:22]}"] = lambda dt, doc, ocr: extract.llm_extract(dt, doc, ask, s.uw_model_strong, s.uw_scan_dpi, True)

    res = {name: {"A": [0, 0, 0], "C": [0, 0, 0], "secs": 0.0, "errors": 0, "usage": {}} for name in variants}
    for t in cases:
        style = t["style"]
        cdir = root / ("a" if style == "A" else "c") / t["case_id"]
        for fname, dt in t["files"].items():
            doc = read_pdf(cdir / fname)
            ocr = tex.pages_text(doc)  # OCR se pocita jednou pro dokument a sdili ho obe OCR varianty
            for name, fn in variants.items():
                before = bedrock.meter.snapshot()
                t0 = time.time()
                try:
                    attrs, _ = fn(dt, doc, ocr)
                except Exception as e:  # noqa: BLE001 - benchmark nesmi spadnout na jedne chybe
                    attrs = {}
                    res[name]["errors"] += 1
                    print(f"  ! {name} {t['case_id']}/{fname}: {str(e)[:90]}")
                res[name]["secs"] += time.time() - t0
                after = bedrock.meter.snapshot()
                for m, u in after.items():
                    d = res[name]["usage"].setdefault(m, {"input": 0, "output": 0, "calls": 0})
                    for k in d:
                        d[k] += u[k] - before.get(m, {}).get(k, 0)
                for attr, tv in t["attributes"][fname].items():
                    mode = taxonomy.attributes()[attr]["compare"]
                    got = attrs.get(attr, {}).get("value")
                    r = res[name][style]
                    r[1] += 1
                    r[0] += bool(got is not None and equal(mode, tv, got))
                    r[2] += bool(got is not None and strict_equal(mode, tv, got))
    n_docs = sum(len(t["files"]) for t in cases)
    print(f"\nSkeny: {len(cases)} pripadu, {n_docs} dokumentu; Textract stran: {tex.pages} "
          f"(≈ {tex.pages * 1.5 / 1000:.3f} USD pri 1,50 USD/1000 stran - cena z pameti, neoverena)\n")
    print(f"{'varianta':38s} | styl A tol/pres | styl C tol/pres | chyb | s/dok | tokeny (vstup+vystup) | naklad Claude")
    for name, r in res.items():
        fmt = lambda x: f"{100 * x[0] / x[1]:5.1f}/{100 * x[2] / x[1]:5.1f}" if x[1] else "   -   "
        tok = sum(u["input"] + u["output"] for u in r["usage"].values())
        cost = estimate_cost(r["usage"])
        c = f"{cost['usd']:.3f} USD" if not cost["unpriced_models"] else f"{cost['usd']:.3f} USD + nezname ceny"
        print(f"{name:38s} | {fmt(r['A']):>15s} | {fmt(r['C']):>15s} | {r['errors']:4d} | {r['secs'] / max(n_docs, 1):5.1f} | {tok:>21,} | {c}")
    print("\ntol = tolerantni shoda (bez diakritiky, mezer), pres = presna (drzi diakritiku). Styl C ma slovnik mimo taxonomii: regex ho nezna.")


if __name__ == "__main__":
    main()
