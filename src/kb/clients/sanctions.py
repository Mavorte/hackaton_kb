"""OpenSanctions match API (vyzaduje OPENSANCTIONS_API_KEY) - sankce, PEP."""
import httpx

from kb.config import get_settings

URL = "https://api.opensanctions.org/match/default"


def match_company(name: str, country: str = "cz", http: httpx.Client | None = None) -> list[dict]:
    s = get_settings()
    if not s.opensanctions_api_key:
        raise RuntimeError("OPENSANCTIONS_API_KEY neni nastaven")
    http = http or httpx.Client(timeout=s.http_timeout)
    body = {
        "queries": {
            "q": {"schema": "Company", "properties": {"name": [name], "country": [country]}}
        }
    }
    r = http.post(URL, json=body, headers={"Authorization": f"ApiKey {s.opensanctions_api_key}"})
    r.raise_for_status()
    return r.json()["responses"]["q"]["results"]
