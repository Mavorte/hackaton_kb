# KB hackathon - priprava (Python)

Toolkit na zkouseni pred hackathonem (19.-20.10.). Pravidla hackathonu rikaji, ze
se na akci zacina s prazdnym repem, takze tohle je na ucení a nastaveni prostredi,
ne na kopirovani.

## Start

    python -m venv .venv && source .venv/bin/activate
    pip install -e ".[dev]"
    cp .env.example .env        # vyplnit AWS

## 1) AWS / Bedrock

    python scripts/check_aws.py

Vypise identitu (STS), dostupne inference profily a zkusi Converse call.
Pokud selze s AccessDenied: v Bedrock console povolit Model access v danem
regionu. Spatne ID modelu: vzit ID ze seznamu, ktery skript vypise.

V kodu: `from kb.aws.bedrock import ask, ask_json`.

## 2) Domenova API (KYB)

    python scripts/check_apis.py 00006947

- ARES (zdarma): `kb.clients.ares.Ares` - get(ico), search(jmeno), get_vr(ico)
- VIES (zdarma): `kb.clients.vies.check_vat("CZ", "00006947")`
- OpenSanctions (klic): `kb.clients.sanctions.match_company(jmeno)`
- `valid_ico()` kontroluje kontrolni soucet ICO

Pozn.: ARES jsem psal podle dokumentace, ale z vyvojoveho prostredi nebyl
dostupny, takze live odpoved zatim neoverena - spust check_apis.py u sebe.

## 3) Rychla DB + cteni pres API

    python -m kb.loader data/platby.csv        # CSV/JSON/JSONL -> tabulka (SQLite)
    uvicorn kb.api:app --reload                # http://localhost:8000/docs

- `GET /tables`, `GET /tables/{tabulka}?mena=CZK&limit=50` - generic cteni
- `GET /ares/{ico}` - zivy dotaz do ARES a ulozeni do tabulky `companies`
- `GET /companies`, `GET /vies/CZ/{dic}`, `GET /sanctions?name=...`
- `POST /llm` - dotaz na Bedrock
- `GET /kyb/{ico}` - ARES + LLM shrnuti a red flags (kostra pro demo tracku 4.1)

Postgres: stacilo by zmenit `DATABASE_URL` a doinstalovat driver (psycopg).

## Testy

    pytest
