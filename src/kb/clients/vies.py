"""EU VIES - overeni platce DPH, bez klice."""
import httpx

from kb.config import get_settings

URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{cc}/vat/{number}"


def check_vat(country: str, number: str, http: httpx.Client | None = None) -> dict:
    http = http or httpx.Client(timeout=get_settings().http_timeout)
    r = http.get(URL.format(cc=country.upper(), number=number))
    r.raise_for_status()
    return r.json()
