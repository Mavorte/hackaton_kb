"""EU VIES - overeni platce DPH, bez klice."""
import httpx

from kb.config import get_settings

URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{cc}/vat/{number}"


def check_vat(country: str, number: str, http: httpx.Client | None = None) -> dict:
    if get_settings().offline and http is None:
        from kb.clients.ares import Ares, NotFound

        try:
            c = Ares().get(number)
            return {"isValid": c.dic is not None and not c.dissolved, "name": c.name, "address": c.address, "mock": True}
        except NotFound:
            return {"isValid": False, "name": "---", "address": "---", "mock": True}
    http = http or httpx.Client(timeout=get_settings().http_timeout)
    r = http.get(URL.format(cc=country.upper(), number=number))
    r.raise_for_status()
    return r.json()
