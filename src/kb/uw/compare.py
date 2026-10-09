"""Porovnani hodnot: presna shoda vs. tolerantni (bez diakritiky, velikosti pismen, mezer a pravni formy)."""
import re
import unicodedata
from difflib import SequenceMatcher

from kb.clean import normalize_name


def parse_number(s) -> float | None:
    digits = re.sub(r"[^\d,.\-]", "", str(s).replace("\xa0", " ").replace(" ", "")).replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def norm_text(s: str) -> str:
    """Bez diakritiky, malymi pismeny, bez mezer a interpunkce a bez pravni formy (s.r.o., a.s.)."""
    key = normalize_name(s)[1] or s
    key = "".join(c for c in unicodedata.normalize("NFKD", key.casefold()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", key)


def equal(mode: str, a: str, b: str, judge=None) -> bool:
    """mode: exact (jen mezery a velikost pismen), number, text (tolerantni).
    U textu je 1 pismeno rozdil = jiny nazev (vyznam). Hranicni podobnost (>= 0.8) muze rozhodnout `judge` (LLM)."""
    if mode == "number":
        x, y = parse_number(a), parse_number(b)
        return x is not None and x == y
    if mode == "exact":
        return re.sub(r"\s", "", a).casefold() == re.sub(r"\s", "", b).casefold()
    x, y = norm_text(a), norm_text(b)
    if x == y:
        return True
    if judge and SequenceMatcher(None, x, y).ratio() >= 0.8:
        return bool(judge(a, b))
    return False
