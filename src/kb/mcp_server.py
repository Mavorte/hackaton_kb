"""MCP server (stdio) nad vycistenymi firmami, vektory a semantickou vrstvou.

    python -m kb.mcp_server
Claude Code / Desktop: pridej jako MCP server s command "python" a args ["-m", "kb.mcp_server"].
"""
from mcp.server.fastmcp import FastMCP

from kb import ingest, search, semantic
from kb.uw import kbase as uw_kb
from kb.uw import pipeline as uw_pipeline
from kb.uw import queries as uw_queries
from kb import embed
from kb.clients.ares import valid_ico

mcp = FastMCP("kb-companies")


@mcp.tool()
def describe_semantic_layer(entity: str = "company") -> dict:
    """Vrati dostupne dimenze, metriky a pojmenovane filtry. Zavolej PRED query_metrics.
    entity: company (firmy z ARES), uw_rules (vysledky pravidel underwritingu), uw_docs (zpracovane dokumenty)."""
    return semantic.describe(entity)


@mcp.tool()
def query_metrics(metrics: list[str], group_by: list[str] | None = None, filters: list[str] | None = None,
                  where: dict | None = None, limit: int = 50, entity: str = "company") -> dict:
    """Agregace pres semantickou vrstvu (pocty, podily, prumery). Nazvy metrik, dimenzi a filtru viz
    describe_semantic_layer(entity). where = {"city": "Praha"} nebo {"city": ["Praha", "Brno"]}."""
    return semantic.query(metrics, group_by, filters, where, limit, entity=entity)


@mcp.tool()
def search_companies(query: str, limit: int = 5, only_active: bool = False) -> list[dict]:
    """Vektorove hledani firem podle vyznamu (nazev, obor, mesto, pravni forma). Pro dotazy typu
    'IT firmy v Praze'. Vraci ico, nazev a skore podobnosti."""
    return search.search_companies(query, limit, only_active)


@mcp.tool()
def get_company(ico: str) -> dict:
    """Detail jedne firmy podle ICO: vycistena data, kvalita dat a red flags (zanikla, mlada, bez DIC)."""
    if not valid_ico(ico):
        return {"error": "Neplatné IČO (nesedí kontrolní součet)."}
    return search.get_company(ico) or {"error": "IČO není v databázi. Zavolej ingest_company."}


@mcp.tool()
def ingest_company(ico: str) -> dict:
    """Nacte firmu z ARES (v OFFLINE modu ze vzorku), vycisti ji a spocita vektor."""
    if not valid_ico(ico):
        return {"error": "Neplatné IČO (nesedí kontrolní součet)."}
    try:
        return ingest.ingest_icos([ico])
    except Exception as e:  # nenalezeno / ARES nedostupny
        return {"error": f"{type(e).__name__}: {e}"}


# --- underwriting: znalostni baze dokumentu a pripady ---------------------------------------------

@mcp.tool()
def uw_process_case(case_dir: str) -> dict:
    """Zpracuje slozku pripadu (PDF): klasifikace, extrakce, pravidla, report. Cesta musi byt pod UW_CASES_DIR."""
    try:
        res = uw_pipeline.process_case(uw_queries.safe_case_dir(case_dir))
    except ValueError as e:
        return {"error": str(e)}
    return {"case_id": res["case_id"], "failed_rules": res["failed_rules"], "report": res["report"]}


@mcp.tool()
def uw_list_cases() -> list[dict]:
    """Seznam zpracovanych pripadu a selhanych pravidel."""
    return uw_queries.list_cases()


@mcp.tool()
def uw_get_case(case_id: str) -> dict:
    """Detail pripadu: dokumenty (typ, jak urcen), atributy s citaci a stranou, vysledky pravidel."""
    return uw_queries.get_case(case_id) or {"error": f"Případ {case_id} neexistuje."}


@mcp.tool()
def uw_explain_rule(case_id: str, rule_id: str) -> dict:
    """Vysvetli vysledek pravidla: popis, zprava a dukazy (hodnoty z jednotlivych dokumentu s citaci)."""
    return uw_queries.explain_rule(case_id, rule_id)


@mcp.tool()
def uw_find_similar_documents(text: str, k: int = 5, verified_only: bool = True) -> list[dict]:
    """Nejpodobnejsi dokumenty ve znalostni bazi podle textu (vektorove hledani); vraci typ, podobnost a zdroj stitku."""
    return uw_kb.neighbors(embed.embed_texts([text[:2000]])[0], max(1, min(k, 20)), verified_only)


@mcp.tool()
def uw_get_exemplars(doc_type: str, k: int = 3) -> list[dict]:
    """Overene priklady daneho typu dokumentu (pro few-shot klasifikaci)."""
    return uw_kb.exemplars(doc_type, max(1, min(k, 10)))


@mcp.tool()
def uw_case_precedents(case_id: str, k: int = 3) -> list[dict]:
    """Podobne drivejsi pripady a pravidla, ktera v nich selhala (predpoved rizika noveho pripadu)."""
    return uw_kb.case_precedents(case_id, max(1, min(k, 10)))


@mcp.tool()
def uw_rule_stats(rule_id: str) -> dict:
    """Statistika pravidla pres vsechny pripady: kolikrat selhalo a typicke zpravy."""
    return uw_kb.rule_stats(rule_id)


@mcp.tool()
def uw_record_feedback(doc_id: int, doc_type: str) -> dict:
    """Potvrzeni/oprava typu dokumentu clovekem. Dokument se stane overenym prikladem znalostni baze."""
    from kb.uw import taxonomy
    if doc_type not in taxonomy.doc_types():
        return {"error": f"Neznámý typ. Povolené: {list(taxonomy.doc_types())}"}
    return uw_kb.confirm_label(doc_id, doc_type)


if __name__ == "__main__":
    mcp.run()
