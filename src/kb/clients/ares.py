"""ARES (MF CR) - zdarma, bez klice. Zaklad pro KYB: IČO -> subjekt."""
from pydantic import BaseModel

import json
from pathlib import Path

import httpx

from kb.config import get_settings

BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"


class Company(BaseModel):
    ico: str
    name: str
    legal_form: str | None = None
    address: str | None = None
    founded: str | None = None
    dissolved: str | None = None
    dic: str | None = None
    nace: list[str] = []
    raw: dict = {}


class NotFound(Exception):
    pass


def valid_ico(ico: str) -> bool:
    ico = ico.strip().zfill(8)
    if not (ico.isdigit() and len(ico) == 8):
        return False
    total = sum(int(d) * w for d, w in zip(ico[:7], range(8, 1, -1)))
    r = total % 11
    check = 1 if r == 0 else 0 if r == 1 else 11 - r
    return check == int(ico[7])


def _to_company(d: dict) -> Company:
    return Company(
        ico=d["ico"],
        name=d.get("obchodniJmeno", ""),
        legal_form=d.get("pravniForma"),
        address=(d.get("sidlo") or {}).get("textovaAdresa"),
        founded=d.get("datumVzniku"),
        dissolved=d.get("datumZaniku"),
        dic=d.get("dic"),
        nace=d.get("czNace") or [],
        raw=d,
    )


class Ares:
    def __init__(self, http: httpx.Client | None = None):
        s = get_settings()
        self.offline = s.offline and http is None
        self.samples = Path(s.samples_dir)
        self.http = http or (None if self.offline else httpx.Client(timeout=s.http_timeout))

    def _sample(self, ico: str) -> dict:
        f = self.samples / f"{ico}.json"
        if not f.exists():
            raise NotFound(ico)
        return json.loads(f.read_text(encoding="utf-8"))

    def get(self, ico: str) -> Company:
        ico = ico.strip().zfill(8)
        if self.offline:
            return _to_company(self._sample(ico))
        r = self.http.get(f"{BASE}/ekonomicke-subjekty/{ico}")
        if r.status_code == 404:
            raise NotFound(ico)
        r.raise_for_status()
        return _to_company(r.json())

    def search(self, name: str, limit: int = 10) -> list[Company]:
        if self.offline:
            found = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(self.samples.glob("*.json"))]
            return [_to_company(d) for d in found if name.lower() in d.get("obchodniJmeno", "").lower()][:limit]
        r = self.http.post(
            f"{BASE}/ekonomicke-subjekty/vyhledat",
            json={"obchodniJmeno": name, "start": 0, "pocet": limit},
        )
        r.raise_for_status()
        return [_to_company(d) for d in r.json().get("ekonomickeSubjekty", [])]

    def get_vr(self, ico: str) -> dict:
        """Verejny rejstrik: statutari, spolecnici, kapital (raw JSON)."""
        ico = ico.strip().zfill(8)
        if self.offline:
            raise NotFound(ico)  # ukazkova data verejny rejstrik nemaji
        r = self.http.get(f"{BASE}/ekonomicke-subjekty-vr/{ico}")
        if r.status_code == 404:
            raise NotFound(ico)
        r.raise_for_status()
        return r.json()
