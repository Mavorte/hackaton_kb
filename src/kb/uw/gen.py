"""Generator syntetickych underwritingovych pripadu (PDF + truth.json se spravnymi odpovedmi).

    python -m kb.uw.gen --out data/uw            # seed (styl A), eval_a (styl A), eval_b (styl B)
Firmy bere z data/samples/ares (smyslene), takze KYC pravidla jdou overit v offline rezimu.
Zadna realna data. Cast pripadu ma zamerne chyby (defects) - v truth.json je jejich seznam
a ocekavane vysledky pravidel.
"""
import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

from kb.clean import clean_company
from kb.clients.ares import valid_ico
from kb.config import get_settings
from kb.uw import taxonomy

PRODUCTS = ["Kolový nakladač KN-250", "Obráběcí centrum OC-400", "Nákladní automobil NA-18", "Průmyslová pračka PP-90",
            "Vysokozdvižný vozík VV-35", "Laserová řezačka LR-60", "Zemědělský traktor ZT-140"]
SUPPLIERS = ["Strojírny Nordmann a.s.", "Techno Dodávky s.r.o.", "Kovo Prodej s.r.o.", "Mechanika Vítek s.r.o.", "Průmysl Servis a.s."]
NAMES = {
    "smlouva_o_uveru": ["Smlouva_o_uveru_{n}.pdf", "SmlouvaUver {n}.pdf", "uver-{n}-podepsana.pdf"],
    "objednavka": ["obj_{n}.pdf", "Objednavka {n}.pdf", "objednavka_zbozi_{n}.pdf"],
    "predavaci_protokol": ["predavaci protokol.pdf", "Protokol_{n}.pdf", "predani_{n}.pdf"],
    "platebni_kalendar": ["Platebni_kalendar.pdf", "kalendar {n}.pdf", "splatky_{n}.pdf"],
    "prohlaseni_o_rucenii": ["prohlaseni_ruceni.pdf", "Rucitel_{n}.pdf", "ruceni {n}.pdf"],
    "vop": ["VOP_2024.pdf", "vseobecne podminky.pdf", "VOP.pdf"],
}
TITLES = {
    "A": {"smlouva_o_uveru": "Smlouva o úvěru", "objednavka": "Objednávka", "predavaci_protokol": "Předávací protokol",
          "platebni_kalendar": "Informativní platební kalendář", "prohlaseni_o_rucenii": "Prohlášení o ručení",
          "vop": "Všeobecné obchodní podmínky"},
    "B": {"smlouva_o_uveru": "Leasingová smlouva", "objednavka": "Závazná objednávka", "predavaci_protokol": "Protokol o převzetí",
          "platebni_kalendar": "Splátkový kalendář", "prohlaseni_o_rucenii": "Ručitelské prohlášení",
          "vop": "Obecná ustanovení a podmínky"},
}
TITLES["C"] = {"smlouva_o_uveru": "Smlouva o poskytnutí financování", "objednavka": "Potvrzení o objednání zboží",
               "predavaci_protokol": "Zápis o předání a převzetí", "platebni_kalendar": "Přehled plánovaných plateb",
               "prohlaseni_o_rucenii": "Čestné prohlášení ručitele", "vop": "Podmínky spolupráce"}
LABELS = {
    "A": {
        "smlouva_o_uveru": {"NAJEMCE__Nazev": "Nájemce", "NAJEMCE__ICO": "IČO nájemce", "NAJEMCE__DIC": "DIČ nájemce",
                            "NAJEMCE__Adresa": "Sídlo nájemce", "DODAVATEL__Nazev": "Dodavatel",
                            "PREDMET__Nazev": "Předmět financování", "CENA": "Cena", "SPLATKA__Pocet": "Počet splátek"},
        "objednavka": {"NAJEMCE__Nazev": "Objednatel", "NAJEMCE__ICO": "IČO objednatele", "NAJEMCE__Adresa": "Adresa objednatele",
                       "DODAVATEL__Nazev": "Dodavatel", "DODAVATEL__ICO": "IČO dodavatele", "PREDMET__Nazev": "Předmět objednávky",
                       "CENA": "Cena"},
        "predavaci_protokol": {"NAJEMCE__Nazev": "Převzal", "PREDMET__Nazev": "Předmět"},
        "platebni_kalendar": {"NAJEMCE__Nazev": "Klient", "SPLATKA__Pocet": "Počet splátek"},
        "prohlaseni_o_rucenii": {"NAJEMCE__Nazev": "Dlužník", "NAJEMCE__ICO": "IČO dlužníka"},
    },
    "B": {
        "smlouva_o_uveru": {"NAJEMCE__Nazev": "Úvěrovaný", "NAJEMCE__ICO": "IČO úvěrovaného", "NAJEMCE__DIC": "DIČ úvěrovaného",
                            "NAJEMCE__Adresa": "Sídlo úvěrovaného", "DODAVATEL__Nazev": "Prodávající",
                            "PREDMET__Nazev": "Předmět plnění", "CENA": "Celková cena", "SPLATKA__Pocet": "Počet měsíčních splátek"},
        "objednavka": {"NAJEMCE__Nazev": "Klient", "NAJEMCE__ICO": "IČO klienta", "NAJEMCE__Adresa": "Adresa objednatele",
                       "DODAVATEL__Nazev": "Prodávající", "DODAVATEL__ICO": "IČO prodávajícího", "PREDMET__Nazev": "Předmět",
                       "CENA": "Kupní cena"},
        "predavaci_protokol": {"NAJEMCE__Nazev": "Převzal", "PREDMET__Nazev": "Předmět plnění"},
        "platebni_kalendar": {"NAJEMCE__Nazev": "Úvěrovaný", "SPLATKA__Pocet": "Počet měsíčních splátek"},
        "prohlaseni_o_rucenii": {"NAJEMCE__Nazev": "Klient", "NAJEMCE__ICO": "IČO klienta"},
    },
}
# Styl C: slovnik mimo taxonomii a hodnoty na dalsim radku (jako rozdeleny OCR vystup) - mock selze, LLM ma uspet
LABELS["C"] = {
    "smlouva_o_uveru": {"NAJEMCE__Nazev": "Název společnosti (klient)", "NAJEMCE__ICO": "Identifikační číslo", "NAJEMCE__DIC": "Daňové identifikační číslo",
                        "NAJEMCE__Adresa": "Místo podnikání", "DODAVATEL__Nazev": "Dodavatel zboží", "PREDMET__Nazev": "Financovaný předmět",
                        "CENA": "Celková částka financování", "SPLATKA__Pocet": "Doba splácení (měsíců)"},
    "objednavka": {"NAJEMCE__Nazev": "Odběratel", "NAJEMCE__ICO": "IČ odběratele", "NAJEMCE__Adresa": "Místo dodání", "DODAVATEL__Nazev": "Prodejce",
                   "DODAVATEL__ICO": "IČ prodejce", "PREDMET__Nazev": "Zboží", "CENA": "Cena celkem"},
    "predavaci_protokol": {"NAJEMCE__Nazev": "Přebírající", "PREDMET__Nazev": "Převzatý předmět"},
    "platebni_kalendar": {"NAJEMCE__Nazev": "Zákazník", "SPLATKA__Pocet": "Počet plateb"},
    "prohlaseni_o_rucenii": {"NAJEMCE__Nazev": "Hlavní dlužník", "NAJEMCE__ICO": "IČ dlužníka"},
}
BODY = {
    "smlouva_o_uveru": ["Věřitel poskytuje úvěrovanému financování předmětu uvedeného níže za podmínek této smlouvy.",
                        "Úvěrovaný se zavazuje hradit splátky řádně a včas. Vlastnické právo k předmětu zůstává věřiteli do úplného splacení."],
    "objednavka": ["Závazně objednáváme u dodavatele níže uvedený předmět za sjednanou cenu.", "Dodání proběhne do 30 dnů od potvrzení objednávky."],
    "predavaci_protokol": ["Potvrzujeme, že předmět byl řádně předán a převzat bez zjevných vad.", "Datum převzetí: {date}."],
    "platebni_kalendar": ["Tento přehled je pouze informativní a odpovídá podmínkám smlouvy o financování.",
                          "Splátky jsou splatné vždy k 15. dni měsíce."],
    "prohlaseni_o_rucenii": ["Ručitel prohlašuje, že ručí za veškeré závazky dlužníka vyplývající z úvěrové smlouvy.",
                             "Ručení se vztahuje na jistinu, úroky i příslušenství."],
    "vop": ["1. Tyto všeobecné podmínky upravují vztahy mezi věřitelem a klientem.", "2. Spory se řeší u věcně příslušného soudu.",
            "3. Změny podmínek jsou účinné po oznámení klientovi."],
}
SIGNERS = {"smlouva_o_uveru": 2, "objednavka": 1, "predavaci_protokol": 1, "prohlaseni_o_rucenii": 1}
DEFECTS = ["name_typo", "ico_swap", "price_mismatch", "missing_doc", "missing_signature", "splatky_mismatch", "dissolved", "unknown_ico"]
FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
                   "/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]


def _font() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).exists()), None)


def fmt_czk(v: int) -> str:
    return f"{v:,}".replace(",", " ") + " Kč"


def _typo(name: str, rng: random.Random) -> str:
    words = name.split()
    i = rng.randrange(len(words) - 1) if len(words) > 1 else 0
    w = words[i]
    words[i] = w[:-1] + ("a" if w[-1] != "a" else "o") if len(w) > 3 else w + "x"
    return " ".join(words)


def _swap_ico(ico: str) -> str:
    for i in range(6, 0, -1):
        cand = ico[:i] + ico[i + 1] + ico[i] + ico[i + 2:]
        if cand != ico and not valid_ico(cand):
            return cand
    return ico[:-1] + str((int(ico[-1]) + 1) % 10)


def load_companies() -> list[dict]:
    d = Path(get_settings().samples_dir)
    return [clean_company(json.loads(f.read_text(encoding="utf-8")), today=date(2026, 10, 7)) for f in sorted(d.glob("*.json"))]


def _unknown_company(rng: random.Random) -> dict:
    base = f"{rng.randint(5000000, 5999999)}"
    ico = next(base + str(c) for c in range(10) if valid_ico(base + str(c)))
    return {"ico": ico, "name": "Fiktivní Obchod s.r.o.", "dic": f"CZ{ico}", "city": "Praha", "zip": "11000", "street": "Neznámá 1",
            "is_active": True}


# --- vykreslovani PDF ---------------------------------------------------------------------------

def _render_pdf(path: Path, title: str, fields: list[tuple[str, str]], body: list[str], signatures: int, rng: random.Random,
                pages: int = 1, wrap: bool = False) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font = "Helvetica"
    if _font():
        pdfmetrics.registerFont(TTFont("CzFont", _font()))
        font = "CzFont"
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 70
    c.setFont(font, 18)
    c.drawString(60, y, title)
    y -= 34
    c.setFont(font, 11)
    for label, value in fields:
        if wrap:  # hodnota na dalsim radku
            c.drawString(60, y, f"{label}:")
            y -= 16
            c.drawString(80, y, value)
        else:
            c.drawString(60, y, f"{label}: {value}")
        y -= 20
    y -= 10
    for para in body:
        c.drawString(60, y, para[:105])
        if len(para) > 105:
            y -= 16
            c.drawString(60, y, para[105:210])
        y -= 22
    y = max(y - 30, 170)
    c.drawString(60, y, f"V Praze dne {date(2026, rng.randint(1, 9), rng.randint(1, 28)).strftime('%d. %m. %Y')}")
    for i in range(signatures):
        x0 = 60 + i * 230
        c.line(x0, 110, x0 + 170, 110)
        c.setFont(font, 9)
        c.drawString(x0, 96, "podpis" if signatures == 1 else f"podpis {i + 1}")
        p = c.beginPath()
        p.moveTo(x0 + 10, 118)
        for _ in range(3):
            p.curveTo(x0 + rng.randint(20, 150), 118 + rng.randint(10, 40), x0 + rng.randint(20, 150), 118 - rng.randint(0, 8),
                      x0 + rng.randint(30, 160), 118 + rng.randint(0, 30))
        c.setLineWidth(1.2)
        c.drawPath(p, stroke=1, fill=0)
        c.setLineWidth(1)
        c.setFont(font, 11)
    c.showPage()
    for n in range(2, pages + 1):
        c.setFont(font, 11)
        c.drawString(60, h - 70, f"{title} - strana {n}")
        yy = h - 100
        for para in body:
            c.drawString(60, yy, para[:105])
            yy -= 20
        c.showPage()
    c.save()


def scanify(path: Path, rng: random.Random, dpi: int = 110) -> None:
    """Zmeni PDF na 'sken': rastr se sumem, otocenim a rozmazanim, bez textove vrstvy."""
    import io

    import pymupdf
    from PIL import Image, ImageFilter

    src = pymupdf.open(path)
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
        img = img.rotate(rng.uniform(-1.2, 1.2), expand=False, fillcolor=255).filter(ImageFilter.GaussianBlur(0.8))
        px = img.load()
        for _ in range(img.width * img.height // 60):
            px[rng.randrange(img.width), rng.randrange(img.height)] = rng.randint(120, 255)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=55)
        pg = out.new_page(width=page.rect.width, height=page.rect.height)
        pg.insert_image(pg.rect, stream=buf.getvalue())
    src.close()
    out.save(path)
    out.close()


# --- pripad --------------------------------------------------------------------------------------

def expected_rules(defects: list[str], present: set[str]) -> dict[str, str]:
    r = {x["id"]: "PASS" for x in taxonomy.rules()}
    d = set(defects)
    if "name_typo" in d:
        r["R01_nazev_najemce"] = r["R08_nazev_ares"] = "FAIL"
    if "ico_swap" in d:
        r["R02_ico_najemce"] = r["R07_ico_existuje"] = "FAIL"
    if "price_mismatch" in d:
        r["R04_cena"] = "FAIL"
    if "splatky_mismatch" in d:
        r["R05_pocet_splatek"] = "FAIL"
    if "missing_doc" in d:
        r["R06_pritomnost"] = "FAIL"
        r["R11_podpis_protokol"] = "NA"
    if "missing_signature" in d:
        r["R10_podpisy_smlouva"] = "FAIL"
    if "dissolved" in d:
        r["R09_aktivni"] = "FAIL"
    if "unknown_ico" in d:
        r["R07_ico_existuje"] = "FAIL"
        r["R08_nazev_ares"] = r["R09_aktivni"] = "NA"
    return r


def make_case(out_dir: Path, case_id: str, company: dict, rng: random.Random, style: str = "A", defects: list[str] | None = None,
              scan: bool = False) -> dict:
    defects = defects or []
    if "unknown_ico" in defects:
        company = _unknown_company(rng)
    case_dir = out_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    product, supplier = rng.choice(PRODUCTS), rng.choice(SUPPLIERS)
    price, n_pay = rng.randrange(150_000, 4_000_000, 5_000), rng.choice([12, 24, 36, 48, 60])
    sup_ico = f"{rng.randint(6000000, 6999999)}"
    sup_ico = next(sup_ico + str(c) for c in range(10) if valid_ico(sup_ico + str(c)))
    addr = f"{company.get('street') or 'Neuvedena 1'}, {company.get('zip') or ''} {company.get('city') or ''}".strip()
    handover = (date(2026, 8, 1) + timedelta(days=rng.randint(0, 40))).strftime("%d. %m. %Y")

    values = {"NAJEMCE__Nazev": company["name"], "NAJEMCE__ICO": company["ico"], "NAJEMCE__DIC": company.get("dic") or "",
              "NAJEMCE__Adresa": addr, "DODAVATEL__Nazev": supplier, "DODAVATEL__ICO": sup_ico, "PREDMET__Nazev": product,
              "CENA": fmt_czk(price), "SPLATKA__Pocet": str(n_pay)}
    doc_types = ["smlouva_o_uveru", "objednavka", "predavaci_protokol", "platebni_kalendar", "prohlaseni_o_rucenii", "vop"]
    if "missing_doc" in defects:
        doc_types.remove("predavaci_protokol")
    if rng.random() < 0.3 and "vop" in doc_types:
        doc_types.remove("vop")

    truth_files, truth_attrs, truth_sigs = {}, {}, {}
    for dt in doc_types:
        vals = dict(values)
        if dt == "objednavka":
            if "name_typo" in defects:
                vals["NAJEMCE__Nazev"] = _typo(company["name"], rng)
            if "price_mismatch" in defects:
                vals["CENA"] = fmt_czk(price + rng.choice([-50_000, 25_000, 100_000]))
        if dt == "prohlaseni_o_rucenii" and "ico_swap" in defects:
            vals["NAJEMCE__ICO"] = _swap_ico(company["ico"])
        if dt == "platebni_kalendar" and "splatky_mismatch" in defects:
            vals["SPLATKA__Pocet"] = str(n_pay + rng.choice([-12, 12]))
        labels = LABELS[style].get(dt, {})
        fields = [(lbl, vals[attr]) for attr, lbl in labels.items() if vals.get(attr)]
        attrs = {attr: vals[attr] for attr in labels if vals.get(attr)}
        sigs = SIGNERS.get(dt, 0)
        if dt == "smlouva_o_uveru" and "missing_signature" in defects:
            sigs = 1
        fname = rng.choice(NAMES[dt]).format(n=rng.randint(100, 999))
        while fname in truth_files:
            fname = f"{rng.randint(1, 9)}_{fname}"
        body = [b.format(date=handover) for b in BODY[dt]]
        _render_pdf(case_dir / fname, TITLES[style][dt], fields, body, sigs, rng, pages=2 if dt == "vop" else 1, wrap=style == "C")
        if scan:
            scanify(case_dir / fname, rng)
        truth_files[fname], truth_attrs[fname], truth_sigs[fname] = dt, attrs, sigs

    truth = {"case_id": case_id, "style": style, "scan": scan, "product": product, "company_ico": company["ico"],
             "defects": defects, "files": truth_files, "attributes": truth_attrs, "signatures": truth_sigs,
             "expected_rules": expected_rules(defects, set(doc_types))}
    (case_dir / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return truth


def make_corpus(out_dir: str | Path, n: int, style: str = "A", seed: int = 1, defect_rate: float = 0.6, scan: bool = False,
                prefix: str = "case") -> list[dict]:
    rng = random.Random(seed)
    out_dir = Path(out_dir)
    companies = load_companies()
    active = [c for c in companies if c["is_active"]]
    dissolved = [c for c in companies if not c["is_active"]]
    cases = []
    for i in range(n):
        defects = []
        if rng.random() < defect_rate:
            defects = [DEFECTS[(i + rng.randrange(len(DEFECTS))) % len(DEFECTS)]]
            if rng.random() < 0.15:
                defects.append(rng.choice([d for d in DEFECTS if d not in defects and d not in ("missing_doc", "unknown_ico", "dissolved")]))
        pool = dissolved if "dissolved" in defects else active
        cases.append(make_case(out_dir, f"{prefix}_{i + 1:03d}", rng.choice(pool), rng, style, defects, scan))
    return cases


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/uw")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--n-seed", type=int, default=12)
    ap.add_argument("--n-eval", type=int, default=20)
    ap.add_argument("--n-hard", type=int, default=10)
    a = ap.parse_args()
    out = Path(a.out)
    make_corpus(out / "seed", a.n_seed, "A", a.seed, defect_rate=0.3, prefix="seed")
    make_corpus(out / "eval_a", a.n_eval, "A", a.seed + 1, prefix="evalA")
    make_corpus(out / "eval_b", a.n_eval, "B", a.seed + 2, prefix="evalB")
    make_corpus(out / "eval_c", a.n_hard, "C", a.seed + 3, prefix="evalC")
    print(f"Vygenerovano v {out}: seed {a.n_seed} (styl A), eval_a {a.n_eval} (A), eval_b {a.n_eval} (B), eval_c {a.n_hard} (C, těžká)")


if __name__ == "__main__":
    main()
