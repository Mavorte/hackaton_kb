"""MCP server (stdio) nad vycistenymi firmami, vektory a semantickou vrstvou.

    python -m kb.mcp_server
Claude Code / Desktop: pridej jako MCP server s command "python" a args ["-m", "kb.mcp_server"].
"""
from mcp.server.fastmcp import FastMCP

from kb import ingest, search, semantic
from kb.clients.ares import valid_ico

mcp = FastMCP("kb-companies")


@mcp.tool()
def describe_semantic_layer() -> dict:
    """Vrati dostupne dimenze, metriky a pojmenovane filtry. Zavolej PRED query_metrics."""
    return semantic.describe()


@mcp.tool()
def query_metrics(metrics: list[str], group_by: list[str] | None = None, filters: list[str] | None = None,
                  where: dict | None = None, limit: int = 50) -> dict:
    """Agregace pres semantickou vrstvu (pocty, podily, prumery). Nazvy metrik, dimenzi a filtru viz
    describe_semantic_layer. where = {"city": "Praha"} nebo {"city": ["Praha", "Brno"]}."""
    return semantic.query(metrics, group_by, filters, where, limit)


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


if __name__ == "__main__":
    mcp.run()
