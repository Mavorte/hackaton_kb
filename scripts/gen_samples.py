"""Vygeneruje smyslene firmy ve tvaru ARES do data/samples/ares (python scripts/gen_samples.py).
Cast zaznamu je zamerne 'spinava' (mezery, VELKA PISMENA, ruzne zapisy s.r.o., psc jako cislo)."""
import json
import random
from datetime import date, timedelta
from pathlib import Path

from kb.clients.ares import valid_ico

OUT = Path("data/samples/ares")
random.seed(7)
FIRST = ["Nordic", "Kvantum", "Zelený", "Modrý", "Hanácká", "Šumavská", "Polabské", "Slezská", "Orlická", "Vltavská"]
INDUSTRY = [
    ("Software", ["62010", "62020"]), ("Logistika", ["49410", "52290"]), ("Stavby", ["41200", "43210"]),
    ("Gastro", ["56101"]), ("Reality", ["68201", "68310"]), ("Konzulting", ["70220"]),
    ("Finance", ["64190", "66190"]), ("Obchod", ["46900", "47110"]), ("Energie", ["35110"]),
    ("Právní služby", ["69101"]),
]
CITIES = [("Praha", 11000), ("Brno", 60200), ("Ostrava", 70200), ("Plzeň", 30100), ("Olomouc", 77900),
          ("Liberec", 46001), ("České Budějovice", 37001), ("Hradec Králové", 50002)]
STREETS = ["Nádražní", "Lipová", "Školní", "Zahradní", "Pražská", "Dlouhá", "Krátká", "Polní", "Vinohradská"]
FORMS = [("112", "s.r.o."), ("112", "s.r.o."), ("112", "s.r.o."), ("121", "a.s."), ("111", "v.o.s.")]
TODAY = date(2026, 10, 7)


def make_ico(i: int) -> str:
    base = f"{2900000 + i * 7:07d}"
    return next(base + str(c) for c in range(10) if valid_ico(base + str(c)))


def dirty_name(name: str, suffix: str) -> str:
    r = random.random()
    if r < 0.12:
        return f"  {name.upper()}   {suffix.replace('.', '. ').strip()}  "
    if r < 0.24:
        return f"{name}  {suffix.replace('.', '. ').strip()}"
    if r < 0.30 and suffix == "s.r.o.":
        return f"{name}, spol. s r.o."
    return f"{name} {suffix}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for i in range(37):
        first, (ind, nace) = random.choice(FIRST), random.choice(INDUSTRY)
        city, psc = random.choice(CITIES)
        code, suffix = random.choice(FORMS)
        young = random.random() < 0.15
        founded = TODAY - timedelta(days=random.randint(30, 330)) if young else date(random.randint(1995, 2024), random.randint(1, 12), random.randint(1, 28))
        dissolved = None
        if not young and random.random() < 0.14:
            dissolved = founded + timedelta(days=random.randint(400, 3000))
            dissolved = min(dissolved, TODAY - timedelta(days=60))
        ico = make_ico(i)
        street = f"{random.choice(STREETS)} {random.randint(1, 120)}"
        rec = {
            "ico": ico,
            "obchodniJmeno": dirty_name(f"{first} {ind}", suffix),
            "sidlo": {"kodStatu": "CZ", "nazevObce": city, "psc": psc if random.random() < 0.3 else str(psc),
                      "textovaAdresa": f"{street}, {psc} {city}"},
            "pravniForma": code,
            "datumVzniku": founded.isoformat(),
            "czNace": nace,
        }
        if dissolved:
            rec["datumZaniku"] = dissolved.isoformat()
        if not (random.random() < 0.12 or (young and random.random() < 0.5)):
            rec["dic"] = f"CZ{ico}"
        (OUT / f"{ico}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"vygenerovano 37 firem do {OUT}")


if __name__ == "__main__":
    main()
