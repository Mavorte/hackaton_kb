"""Rychly import CSV / JSON / JSONL do tabulky (typy se odhadnou).

    python -m kb.loader data/platby.csv            # tabulka 'platby'
    python -m kb.loader data/x.json transakce      # tabulka 'transakce'
"""
import csv
import json
import sys
from pathlib import Path

from sqlalchemy import Column, Float, Integer, MetaData, Table, Text
from sqlalchemy.engine import Engine

from kb.db import get_engine


def read_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        text = path.read_text(encoding="utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return list(csv.DictReader(text.splitlines(), dialect=dialect))
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):  # {"items": [...]} apod.
            data = next((v for v in data.values() if isinstance(v, list)), [data])
        return data
    raise ValueError(f"Nepodporovana pripona: {suffix}")


def _infer(values: list):
    vals = [v for v in values if v not in (None, "")]
    if vals and all(_is(int, v) for v in vals):
        return Integer
    if vals and all(_is(float, v) for v in vals):
        return Float
    return Text


def _is(t, v) -> bool:
    if isinstance(v, bool):
        return False
    try:
        t(v)
        return True
    except (TypeError, ValueError):
        return False


def _coerce(v, col_type):
    if v in (None, ""):
        return None
    if col_type is Integer:
        return int(v)
    if col_type is Float:
        return float(v)
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def load_file(path: str | Path, table: str | None = None, engine: Engine | None = None) -> tuple[str, int]:
    path = Path(path)
    engine = engine or get_engine()
    rows = read_rows(path)
    if not rows:
        raise ValueError("Zadne radky")
    name = table or path.stem.lower().replace("-", "_").replace(" ", "_")
    cols = list(dict.fromkeys(k for r in rows for k in r))
    types = {c: _infer([r.get(c) for r in rows]) for c in cols}

    md = MetaData()
    # umely PK jen pokud data vlastni sloupec 'id' nemaji
    pk = [] if "id" in cols else [Column("id", Integer, primary_key=True, autoincrement=True)]
    t = Table(name, md, *pk, *[Column(c, types[c]) for c in cols])
    t.drop(engine, checkfirst=True)
    t.create(engine)
    with engine.begin() as conn:
        conn.execute(t.insert(), [{c: _coerce(r.get(c), types[c]) for c in cols} for r in rows])
    return name, len(rows)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    n, count = load_file(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(f"Nacteno {count} radku do tabulky '{n}'")
