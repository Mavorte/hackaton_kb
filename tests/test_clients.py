import httpx
import pytest

from kb.clients.ares import Ares, NotFound, valid_ico

SUBJECT = {
    "ico": "00006947",
    "obchodniJmeno": "Ministerstvo financí",
    "sidlo": {"textovaAdresa": "Letenská 525/15, Malá Strana, 11800 Praha 1"},
    "datumVzniku": "1993-01-01",
    "czNace": ["84110"],
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/vyhledat"):
        return httpx.Response(200, json={"pocetCelkem": 1, "ekonomickeSubjekty": [SUBJECT]})
    if request.url.path.endswith("/00006947"):
        return httpx.Response(200, json=SUBJECT)
    return httpx.Response(404)


def ares():
    return Ares(httpx.Client(transport=httpx.MockTransport(handler)))


def test_valid_ico():
    assert valid_ico("00006947")
    assert valid_ico("6947")  # zfill
    assert not valid_ico("00006948")
    assert not valid_ico("abc")


def test_get_and_search():
    c = ares().get("6947")
    assert c.name == "Ministerstvo financí" and c.nace == ["84110"]
    assert ares().search("financi")[0].ico == "00006947"


def test_not_found():
    with pytest.raises(NotFound):
        ares().get("12345678")
