"""ARES (raw) -> vycistena typovana radka companies_clean."""
import re
import unicodedata
from datetime import date

from kb.clients.ares import valid_ico

# Neuplny ciselnik (pro ostra data doplnit z ciselniku ARES/CSU).
LEGAL_FORMS = {
    "101": "Fyzická osoba podnikající (nezapsaná v OR)", "111": "Veřejná obchodní společnost",
    "112": "Společnost s ručením omezeným", "121": "Akciová společnost", "141": "Obecně prospěšná společnost",
    "301": "Státní podnik", "325": "Organizační složka státu", "706": "Spolek",
}
NACE_DIVISIONS = {
    "35": "Energetika", "41": "Výstavba budov", "43": "Specializované stavební činnosti", "46": "Velkoobchod",
    "47": "Maloobchod", "49": "Pozemní doprava", "52": "Skladování a podpora dopravy", "56": "Stravování",
    "62": "Programování a IT", "64": "Finanční služby", "66": "Pomocné finanční činnosti",
    "68": "Činnosti v oblasti nemovitostí", "69": "Právní a účetní činnosti", "70": "Poradenství a řízení",
    "84": "Veřejná správa",
}
_SUFFIX = re.compile(
    r"(?<!\w)(?P<s>(?:spol\.?\s*)?s\.?\s*r\.?\s*o\.?|a\.?\s*s\.?|v\.?\s*o\.?\s*s\.?)\s*$", re.IGNORECASE
)
_CANON = {"spolsro": "spol. s r.o.", "sro": "s.r.o.", "as": "a.s.", "vos": "v.o.s."}


def _strip_diacritics(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_name(raw: str) -> tuple[str, str]:
    """Vrati (zobrazovany nazev, klic pro porovnani bez diakritiky a pravni formy)."""
    text = re.sub(r"\s+", " ", raw or "").strip().rstrip(",").strip()
    suffix = ""
    m = _SUFFIX.search(text)
    if m:
        letters = re.sub(r"[^a-z]", "", m.group("s").lower())
        suffix = _CANON.get(letters, m.group("s"))
        text = text[: m.start()].rstrip(" ,")
    if text.isupper() and len(text) > 4:
        text = text.title()
    display = f"{text} {suffix}".strip()
    key = re.sub(r"[^a-z0-9]+", " ", _strip_diacritics(text).casefold()).strip()
    return display, key


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _address(sidlo: dict) -> tuple[str | None, str | None, str | None]:
    text = (sidlo or {}).get("textovaAdresa") or ""
    street = text.split(",")[0].strip() if "," in text else None
    city = (sidlo or {}).get("nazevObce")
    if not city and text:
        m = re.search(r"\d{3}\s?\d{2}\s+(.+)$", text)
        city = re.sub(r"\s+\d+$", "", m.group(1)).strip() if m else None
    psc = (sidlo or {}).get("psc")
    if psc not in (None, ""):
        zip_ = str(psc).replace(" ", "").zfill(5)
    else:
        m = re.search(r"(\d{3})\s?(\d{2})", text)
        zip_ = m.group(1) + m.group(2) if m else None
    return street, city, zip_


def _age_band(age: float | None) -> str:
    if age is None:
        return "neznámé"
    return "do 1 roku" if age < 1 else "1-5 let" if age < 5 else "5-15 let" if age < 15 else "15+ let"


def clean_company(raw: dict, today: date | None = None) -> dict:
    today = today or date.today()
    ico = str(raw.get("ico", "")).strip().zfill(8)
    name, name_norm = normalize_name(raw.get("obchodniJmeno", ""))
    street, city, zip_ = _address(raw.get("sidlo") or {})
    founded, dissolved = _parse_date(raw.get("datumVzniku")), _parse_date(raw.get("datumZaniku"))
    end = dissolved or today
    age = round((end - founded).days / 365.25, 2) if founded else None
    dic = (raw.get("dic") or "").strip().upper() or None
    nace = [str(c) for c in (raw.get("czNace") or [])]
    division = nace[0][:2] if nace else None
    code = str(raw["pravniForma"]) if raw.get("pravniForma") else None

    issues = []
    if not valid_ico(ico):
        issues.append("invalid_ico_checksum")
    if not name:
        issues.append("missing_name")
    if not dic:
        issues.append("missing_dic")
    if founded is None:
        issues.append("missing_founded")
    if not city:
        issues.append("missing_address")
    if founded and dissolved and dissolved < founded:
        issues.append("dissolved_before_founded")

    return {
        "ico": ico, "name": name, "name_norm": name_norm,
        "legal_form_code": code, "legal_form": LEGAL_FORMS.get(code, f"Jiná (kód {code})") if code else None,
        "street": street, "city": city, "zip": zip_,
        "founded": founded, "dissolved": dissolved, "founded_year": founded.year if founded else None,
        "is_active": dissolved is None, "status": "aktivní" if dissolved is None else "zaniklá",
        "age_years": age, "age_band": _age_band(age),
        "dic": dic, "has_dic": dic is not None,
        "nace_main": nace[0] if nace else None, "nace_division": division,
        "nace_division_label": NACE_DIVISIONS.get(division, "Jiné") if division else None,
        "nace_codes": nace, "quality_issues": issues,
    }
