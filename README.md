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

## Offline rezim (bez AWS a bez site)

    LLM_PROVIDER=mock OFFLINE=1 uvicorn kb.api:app --reload

- `LLM_PROVIDER=mock`: `ask()` / `ask_json()` nevolaji Bedrock; `/kyb/{ico}` pouzije jednoduche pravidla
  (zaniklý subjekt, mlady subjekt, chybejici DIC).
- `OFFLINE=1`: ARES a VIES ctou smyslena data z `data/samples/ares/` (IČO 28100018, 28100026, 28100034).
  Jine IČO vraci 404. Data jsou syntetická, tvar odpovida dokumentaci ARES.

## Streamlit UI (demo)

    pip install -e ".[ui]"
    streamlit run src/kb/ui.py                                   # ARES + Bedrock
    LLM_PROVIDER=mock OFFLINE=1 streamlit run src/kb/ui.py       # bez AWS a bez site

Zadej IČO (např. 28100034) nebo část názvu (např. Demo). Zobrazí kartu firmy, KYB posouzení
(riziko, red flags) a kontrolu plátce DPH ve VIES. V postranním panelu je historie hledání.
Záložka **Agent (MCP)**: chat s agentem, který volá nástroje MCP serveru; u každé odpovědi je vidět použitý nástroj,
jeho argumenty a výsledek. Tlačítko „Načíst vzorová data“ spustí ingest vzorku.

## Data pipeline, semantická vrstva, MCP a agent

    python scripts/gen_samples.py                 # (jednou) 37 vygenerovaných firem k 3 ručním; už je v repu
    OFFLINE=1 LLM_PROVIDER=mock python -m kb.ingest --samples
    python -m kb.ingest 00006947                  # živý ARES (nebo vzorek při OFFLINE=1)

Tok: `ares_raw` (surová odpověď + hash) → `companies_clean` (typy, normalizace názvů a adres, právní forma,
CZ-NACE, věk, `quality_issues`) → `company_vectors` (embedding, klíč ico+model). Ingest je idempotentní,
přepočítá jen změněné záznamy.

- **Sémantická vrstva:** `src/kb/semantic_model.yaml` (dimenze, metriky, pojmenované filtry). `kb.semantic.query()`
  z nich skládá SQL jen z povolených výrazů, hodnoty jdou jako parametry.
- **Vektory:** `EMBED_MODEL_ID` (Titan v2) nebo mock (`LLM_PROVIDER=mock`, hash slov, jen lexikální podobnost).
  Hledání = kosinová podobnost v numpy; pro řádově víc firem pgvector nebo OpenSearch.
- **MCP server:** `python -m kb.mcp_server` (stdio). Nástroje: `describe_semantic_layer`, `query_metrics`,
  `search_companies`, `get_company`, `ingest_company`. Jde připojit i do Claude Code nebo Desktopu.
- **Agent:** `python -m kb.agent "Kolik je aktivních firem podle města?"`. Bedrock Converse tool-use smyčka, jejíž
  nástroje jsou načtené z MCP serveru. S `LLM_PROVIDER=mock` rozhodují jednoduchá pravidla.

## Underwriting: dokumenty -> pravidla -> znalostní báze (back-office, track 06)

Inspirováno PoV „AI for Customer Care“ (kontrola podkladů k úvěru). Všechna data jsou syntetická.

    python -m kb.uw.gen --out data/uw                      # seed (styl A) + eval_a (A) + eval_b (B) + eval_c (těžká, styl C) + eval_d (skeny, část sloučená)
    OFFLINE=1 LLM_PROVIDER=mock python -m kb.uw.evaluate data/uw/eval_a
    python -m kb.uw.pipeline data/uw/eval_a/evalA_001      # report jednoho případu

- **Konfigurace:** `src/kb/uw/taxonomy.yaml` (typy dokumentů, atributy, 11 pravidel: consistency, presence, kyc, signatures).
- **Pipeline:** PDF → klasifikace (rychlá cesta kNN ze znalostní báze; jinak model s příklady z báze) → extrakce atributů
  s důkazem (strana a citace; vizuální model čte obrázky stran, mock čte textovou vrstvu) → rules engine → report.
- **Znalostní báze:** tabulky `uw_*` + vektory prvních stran. Hlasují jen ověřené štítky (seed/human); strojové štítky
  (auto) čekají na potvrzení. Potvrzení v UI nebo `uw_record_feedback` přidá dokument mezi příklady.
- **KYC pravidla:** IČO, název a stav firmy proti ARES (při OFFLINE=1 proti vzorku).
- **Sémantická vrstva:** entity `uw_rules` a `uw_docs` (`semantic_model_uw_*.yaml`) pro `query_metrics`.
- **MCP nástroje:** `uw_process_case`, `uw_get_case`, `uw_explain_rule`, `uw_find_similar_documents`, `uw_get_exemplars`,
  `uw_case_precedents`, `uw_rule_stats`, `uw_record_feedback`. Zpracovat jde jen složky pod `UW_CASES_DIR`.
- **Evaluace** proti `truth.json`: klasifikace, atributy, podpisy, pravidla, podíl rychlé cesty a kalibrace prahu (`UW_KB_MIN_SIM`).
  Sada `eval_c` je záměrně těžká (jiná slovní zásoba, hodnoty na dalším řádku): mock tam selže, měří se na ní vizuální model.
- **Skeny (vizuální cesta, bez OCR):** `gen.make_case(..., scan=True, merge=True)` vyrobí PDF bez textové vrstvy (volitelně se dvěma
  dokumenty v jednom souboru). Tok: levný model přepíše hlavičku (z ní se počítá vektor pro kNN) → segmentace stran na dokumenty →
  klasifikace (kNN z báze, jinak model s obrázky) → extrakce z obrázků s **eskalací modelu** (Haiku → Sonnet → Opus při nízké
  jistotě, neplatném IČO, nečitelné ceně, chybějících atributech). Modely a rozlišení jsou v `UW_MODEL_FAST/STRONG/HARD`,
  `UW_SCAN_DPI`, `UW_ESCALATE_DPI`. Náklady (tokeny a orientační USD) hlásí `process_case()["cost"]` a `evaluate`.
  Sada `eval_d` jsou skeny se sloučenými dokumenty. Mock skeny nečte, tam rozhoduje jen skutečný model.

Pozn.: vizuální cesta je ověřená jen na falešném modelu (`tests/test_uw_visual.py`), účet zatím blokuje volání modelů. Jedno technické
shrnutí na stránku: `docs/technicka-flow.html` (PDF: `docs/technicka-flow.pdf`).

## Testy

    pytest
