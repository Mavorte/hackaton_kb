# Postup zpracování dokumentů: štítkování, vektory, úložiště

Platí pro underwritingový případ (složka PDF) na hackathonovém AWS účtu (Bedrock, `eu-central-1`). Značky: **[ověřeno]** = zkoušeno
na živém účtu nebo v testech, **[navrženo]** = jen návrh, **[neověřeno]** = tvrzení z obecné znalosti, nutno změřit.

## 1. Čím dokument přečíst: OCR, multimodální model, nebo obojí?

| Varianta | AWS služba | Výhody | Nevýhody | Verdikt |
|---|---|---|---|---|
| Textová vrstva PDF | PyMuPDF (lokálně) | zdarma, přesný text, podpisy z vektorových kreseb | jen born-digital PDF | **výchozí pro PDF s textem** [ověřeno] |
| **Multimodální model přímo na obrázku strany** | Bedrock Converse (Claude 4.5, Nova) | bez OCR kroku, vidí rozložení, razítka, podpisy, ruční písmo; jeden krok čtení + porozumění | platí se obrazové tokeny, limit 50 req/min u Claude | **výchozí pro skeny** [ověřeno: sken se sloučenými dokumenty 5/5 správně] |
| OCR + LLM | Amazon Textract + Bedrock | levné čtení textu, vhodné pro velmi dlouhé dokumenty | chyby OCR se přenášejí dál (v PoV: hodnoty rozdělené na dva řádky, špatná kvalita skenu); Textract oficiálně nepodporuje češtinu [neověřeno] | **jen záloha**, nejdřív změřit |
| Spravované zpracování dokumentů | Bedrock Data Automation | hotové schéma výstupu | dostupnost v `eu-central-1` a na našem účtu [neověřeno] | neplánujeme |

Doporučení: **hybrid podle typu souboru.** PDF s textovou vrstvou čteme bez OCR i bez obrázků (text jde do levného modelu).
Skeny čte multimodální model přímo z obrázků. OCR (Textract) zařadíme, jen pokud měření (kap. 9) ukáže přínos u dlouhých
nebo hustých dokumentů. Důvod: PoV z února měl chyby právě z OCR (rozdělené řádky, špatná kvalita), které vizuální čtení odstraní.

## 2. Celý tok jednoho případu

1. **Příjem:** složka PDF, `case_id` z názvu složky, každý soubor dostane otisk SHA-256 (duplicity) a uloží se originál. [navrženo: otisk]
2. **Předzpracování:** `PyMuPDF` zjistí počet stran a zda je textová vrstva. Bez textu se strany vyrenderují na PNG (110 dpi, autocontrast),
   pro eskalaci 160 dpi. S textem se vezme text po stranách a počet podpisů z vektorových kreseb. [ověřeno]
3. **Segmentace souboru na dokumenty:** jeden soubor může obsahovat více dokumentů (PoV: dva v jednom skenu). Sken: levný model
   (Haiku 4.5) dostane všechny strany (70 dpi) a vrátí typ každé strany a začátky nových dokumentů. PDF s textem: nadpisy stran.
   Výsledek jsou **segmenty** `soubor.pdf[s2-3]`. [ověřeno]
4. **Klasifikace (štítek typu dokumentu):** viz kap. 3.
5. **Extrakce atributů:** viz kap. 4.
6. **Pravidla:** rules engine z `taxonomy.yaml` (konzistence mezi dokumenty, přítomnost, KYC proti ARES a VIES, podpisy). Výsledek
   PASS / FAIL / NA se zprávou a důkazy. [ověřeno]
7. **Uložení a vektory:** kap. 5 a 6.
8. **Lidská kontrola:** nálezy a nejisté štítky jdou k člověku; potvrzení nebo oprava vrací dokument do znalostní báze (kap. 3). [ověřeno v UI a testech]

## 3. Štítkování dokumentů (jak vzniká typ dokumentu)

Štítek má tři vlastnosti: `doc_type` (typ z taxonomie), `label_source` (kdo ho potvrdil) a `label_method` (jak vznikl).

1. **Vektor hlavičky.** Text: nadpis a prvních asi 12 řádků první strany. U PDF s textem se bere přímo, u skenu ho přepíše levný model.
   Vektor: Titan Text Embeddings V2, 512 dimenzí, normalizovaný. [ověřeno: Titan na účtu funguje]
2. **Rychlá cesta (kNN):** najde 5 nejbližších dokumentů s ověřeným štítkem (`seed` nebo `human`). Pokud nejbližší má podobnost aspoň
   0,75 a aspoň 80 % váhy hlasů má stejný typ, štítek se přiřadí **bez volání LLM** (`label_method = kb`). Prahy jsou v
   `UW_KB_MIN_SIM`, `UW_KB_MIN_AGREE` a kalibrují se na evaluační sadě (`kb.uw.evaluate` vypíše pokrytí a přesnost podle prahu).
3. **Záloha:** pokud báze není jistá (málo příkladů, dva typy blízko sebe), klasifikuje model (Sonnet 4.5) s typy z taxonomie a odkazy na
   nejbližší příklady (`label_method = llm`). U skenu dostane i obrázky stran.
4. **Stav štítku:** strojové štítky mají `label_source = auto` a **nehlasují** do dalších rozhodnutí, dokud je člověk nepotvrdí.
   Tím se chyby nesnášejí. Potvrzení nebo oprava nastaví `human`, zapíše řádek do `uw_feedback` a dokument se stane příkladem.
5. **Studený start:** báze se naplní seed případy (`seed_case` s pravdivými štítky z `truth.json` u syntetických dat).
   Na hackathonu stačí asi 5 ověřených příkladů na typ. [ověřeno]

Další štítky na úrovni stran a atributů: typ každé strany (segmentace), `scan` ano/ne, počet podpisů, jistota klasifikace, a
`quality_issues` u firem z ARES (neplatné IČO, chybějící DIČ).

## 4. Extrakce atributů a důkazy

- Atributy se berou **jen pro daný typ dokumentu** (`taxonomy.yaml`), takže prompt je krátký a nehalucinuje cizí pole.
- Výstup modelu: `atribut -> {hodnota, strana, doslovná citace, jistota}`. Hodnota bez citace se nepřijme. [navrženo jako tvrdé pravidlo,
  teď se kontroluje jen přítomnost hodnoty]
- **Eskalace modelů:** Haiku 4.5 (110 dpi) → Sonnet 4.5 (160 dpi) → Opus 4.5, když chybí víc než 40 % atributů, jistota je pod 0,75,
  IČO nesedí kontrolní součet, nebo je cena nečitelná. [ověřeno testem s falešným modelem]
- Porovnání hodnot v pravidlech: tolerantní (bez diakritiky, mezer, velikosti písmen, právní formy), u čísel numericky; jedno písmeno
  v názvu = jiný název. Hraniční shodu rozhodne model jako rozhodce. [ověřeno]

## 5. Co ukládáme (a proč)

**Úložiště souborů** (lokálně nebo S3 v `eu-central-1`) [navrženo pro S3, lokálně hotovo]:
- originály PDF (nezměnitelné), odvozené PNG stran (volitelně, s expirací),
- artefakty po dokumentech jako JSON: přepis hlavičky, surová odpověď modelu, výstup extrakce (jako „artifact cache“ v PoV),
- vygenerovaný report.

**Databáze** (SQLite, v ostré verzi PostgreSQL s pgvector) [ověřeno, kromě položek označených jako chybějící]:

| Tabulka | Obsah |
|---|---|
| `uw_cases` | `case_id`, produkt, IČO klienta, textové shrnutí případu |
| `uw_documents` | soubor/segment, počet stran, `doc_type`, `label_source`, `label_method`, `label_confidence`, text hlavičky, počet podpisů |
| `uw_attributes` | `case_id`, `doc_id`, atribut, hodnota, **strana**, **citace**, jistota |
| `uw_rule_results` | pravidlo, typ, výsledek, zpráva, detail (JSON), příznak selhání, produkt |
| `uw_vectors` | vektor hlavičky dokumentu: klíč `(doc_id, model)`, rozměr, bajty float32 |
| `uw_case_vectors` | vektor shrnutí případu pro hledání podobných případů |
| `uw_feedback` | historie oprav štítků (kdo/co/z čeho na co) |
| `ares_raw`, `companies_clean`, `company_vectors` | surová a vyčištěná data firem z ARES a jejich vektory |

**Chybí a doporučuji doplnit** [navrženo]: otisk souboru, verze modelu a promptu u každé extrakce (provenance), tokeny a náklad
po dokumentu, tabulka stran (`uw_pages`) s typem strany, surová odpověď modelu, stav fronty ke kontrole.

## 6. Vektory: co, čím, jak se používají

| Co se vektorizuje | Model | K čemu |
|---|---|---|
| Hlavička první strany dokumentu | Titan Embed V2 (512) | klasifikace kNN, hledání podobných dokumentů |
| Textové shrnutí případu (produkt, typy dokumentů, selhaná pravidla) | totéž | precedenty: podobné dřívější případy a jejich selhání |
| Firma z ARES (název, forma, obor, město, stav) | totéž | hledání firem podle významu |
| [volitelně] obraz strany | Cohere Embed v4 nebo Nova Multimodal Embeddings, pokud na účtu jsou [neověřeno] | podobnost rozložení, razítek, vzhledu |

Pravidla: vektory jsou normalizované, podobnost je skalární součin (kosinová); vektory různých modelů se nikdy nemíchají (klíč obsahuje
model); při změně modelu se přepočítají všechny; hledání je v `numpy` (stačí pro tisíce záznamů), pro víc se přejde na pgvector.
Studený start a zkreslení: vyhodnocovat vždy na dokumentech, které v bázi nejsou (evaluace neučí).

## 7. Jak to používá aplikace

- **MCP server** (14 nástrojů): zpracování případu, detail, vysvětlení pravidla s důkazy, podobné dokumenty, příklady typu, precedenty,
  statistika pravidla, potvrzení štítku, dotazy přes sémantickou vrstvu (`company`, `uw_rules`, `uw_docs`).
- **Agent** (Bedrock Converse + nástroje MCP) odpovídá na otázky nad daty a vysvětluje selhání. [ověřeno na živém modelu]
- **UI** (Streamlit): semafor pravidel, matice atributů, potvrzení štítku.

## 8. Bezpečnost a soulad

- Jen syntetická data, žádné reálné osobní údaje; region `eu-central-1` a EU profily; žádné přístupové klíče (SSO a role).
- MCP smí číst jen složky pod `UW_CASES_DIR`; dotazy sémantické vrstvy skládají SQL jen z povolených výrazů (parametry místo textu).
- Modely vracejí důkaz (strana a citace); rozhodnutí o nálezu činí člověk.
- Volitelně Bedrock Guardrails a maskování osobních údajů před voláním modelu. [navrženo]

## 9. Co je ještě potřeba změřit (neověřeno)

1. **OCR vs. multimodální čtení** [změřeno 16. 10. 2026, 9 skenovaných případů, 50 dokumentů, hackathonový účet]:

   | Varianta | styl A tol./přesně | styl C tol./přesně | s/dok | tokeny | náklad |
   |---|---|---|---|---|---|
   | Textract + regex | 70,4 / 59,2 | 0 / 0 | 0 | 0 | jen Textract |
   | Textract + Haiku 4.5 | 96,8 / 85,6 | 98,4 / 83,9 | 3,9 | 41 508 | 0,113 USD + Textract |
   | Vision Haiku 4.5 | 97,6 / 92,0 | 95,2 / 91,9 | 5,1 | 117 871 | 0,196 USD |
   | Vision Nova 2 Lite | 90,4 / 81,6 | 66,1 / 64,5 | 4,4 | 43 249 | cena na účtu nezměřena |

   Závěr: Textract na účtu funguje a češtinu čte, ale při přesné shodě (s diakritikou) je o 6 až 8 b. b. horší než vision Haiku,
   protože OCR ztrácí háčky a čárky. Tolerantní shoda je u obou vyrovnaná (rozdíl v rámci šumu při 50 dokumentech). OCR+LLM je asi
   o 40 % levnější na tokenech, ale přičte se cena Textractu (55 stran, asi 0,08 USD podle ceny z paměti, neověřeno). Regex nad OCR
   nestačí. Nova 2 Lite je na jiné slovní zásobě (styl C) výrazně horší. Jeden případ OCR+LLM spadl na neplatné JSON (chyba
   parsování odpovědi). Doporučení: ponechat vision Haiku jako výchozí, OCR+LLM jen pro velmi dlouhé dokumenty. Vzorek je malý,
   rozdíly pod asi 3 b. b. nejsou prokazatelné.
2. **Obrazové tokeny na stranu** a skutečná cena případu na dokumentech velikosti PoV (131 tis. vstupních tokenů).
3. **Práh kNN** na Titanu (kalibrace přes `kb.uw.evaluate`).
4. **Dostupnost** Bedrock Data Automation (Textract ověřen) a obrazových embeddingů na hackathonovém účtu.

## 10. Limity a náklady (hackathonový účet)

Claude 50 požadavků za minutu (Opus 25), sdílené v týmu; skenový případ dělá asi 15 až 20 volání. Ceny za milion tokenů vstup/výstup:
Haiku 4.5 1,10/5,50, Sonnet 4.5 3,30/16,50, Opus 4.5 5,50/27,50. Naměřeno na malých syntetických případech: textový případ asi 0,04 USD
(12 volání), skenový se sloučenými dokumenty asi 0,10 USD (20 volání). Reálné dokumenty jsou řádově větší, počítej s 0,20 až 0,40 USD na případ.
