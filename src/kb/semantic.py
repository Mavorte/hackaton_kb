"""Semanticka vrstva: nazvy z YAML -> bezpecne SQL (jen whitelistovane vyrazy, hodnoty jako parametry)."""
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Engine

from kb.db import get_engine


ENTITIES = {"company": "semantic_model.yaml", "uw_rules": "semantic_model_uw_rules.yaml", "uw_docs": "semantic_model_uw_docs.yaml"}


@lru_cache
def model(entity: str = "company") -> dict:
    if entity not in ENTITIES:
        raise ValueError(f"Neznámá entita {entity!r}. Povolené: {list(ENTITIES)}")
    return yaml.safe_load((Path(__file__).parent / ENTITIES[entity]).read_text(encoding="utf-8"))


def describe(entity: str = "company") -> dict:
    m = model(entity)
    pick = lambda section: {k: {"description": v["description"], **({"synonyms": v["synonyms"]} if "synonyms" in v else {})}
                            for k, v in m[section].items()}
    return {"entity": m["entity"], "description": m["description"], "dimensions": pick("dimensions"),
            "metrics": pick("metrics"), "filters": pick("filters")}


def _check(names: list[str], section: str, entity: str) -> None:
    unknown = [n for n in names if n not in model(entity)[section]]
    if unknown:
        raise ValueError(f"Neznámé {section}: {unknown}. Povolené: {list(model(entity)[section])}")


def build_query(metrics: list[str], group_by: list[str] | None = None, filters: list[str] | None = None,
                where: dict | None = None, limit: int = 50, entity: str = "company") -> tuple[str, dict]:
    m, group_by, filters, where = model(entity), group_by or [], filters or [], where or {}
    if not metrics:
        raise ValueError("Zadej aspoň jednu metriku.")
    _check(metrics, "metrics", entity); _check(group_by, "dimensions", entity); _check(filters, "filters", entity); _check(list(where), "dimensions", entity)
    dims = [f"{m['dimensions'][d]['sql']} AS {d}" for d in group_by]
    mets = [f"{m['metrics'][x]['sql']} AS {x}" for x in metrics]
    sql = f"SELECT {', '.join(dims + mets)} FROM {m['table']}"
    clauses, params = [f"({m['filters'][f]['sql']})" for f in filters], {}
    for i, (dim, val) in enumerate(where.items()):
        col = m["dimensions"][dim]["sql"]
        vals = val if isinstance(val, list) else [val]
        names = []
        for j, v in enumerate(vals):
            params[f"w{i}_{j}"] = v
            names.append(f":w{i}_{j}")
        clauses.append(f"{col} IN ({', '.join(names)})")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    if group_by:
        sql += " GROUP BY " + ", ".join(m["dimensions"][d]["sql"] for d in group_by)
        sql += f" ORDER BY {metrics[0]} DESC"
    return sql + f" LIMIT {max(1, min(int(limit), 500))}", params


def query(metrics: list[str], group_by: list[str] | None = None, filters: list[str] | None = None,
          where: dict | None = None, limit: int = 50, engine: Engine | None = None, entity: str = "company") -> dict:
    sql, params = build_query(metrics, group_by, filters, where, limit, entity)
    with (engine or get_engine()).connect() as conn:
        res = conn.execute(text(sql), params)
        return {"sql": sql, "columns": list(res.keys()), "rows": [list(r) for r in res.fetchall()]}
