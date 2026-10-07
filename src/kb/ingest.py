"""ARES -> ares_raw -> companies_clean -> company_vectors.  Idempotentni (zmena se pozna podle hashe).

    python -m kb.ingest --samples            # vsechny soubory z data/samples/ares
    python -m kb.ingest 00006947 27074358    # konkretni ICO (ARES, nebo vzorek pri OFFLINE=1)
"""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from kb import embed
from kb.clean import clean_company
from kb.clients.ares import Ares
from kb.config import get_settings
from kb.db import AresRaw, CompanyClean, CompanyVector, get_engine


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def embedding_text(c: dict) -> str:
    """Text, ze ktereho se pocita vektor: co chceme umet hledat 'podle vyznamu'."""
    return (
        f"{c['name']}. {c['legal_form'] or ''}. Sídlo: {c['city'] or 'neuvedeno'}. "
        f"Obor: {c['nace_division_label'] or 'neuvedeno'}. Stav: {c['status']}."
    )


def ingest_raw(raws: list[dict], engine: Engine | None = None, today: date | None = None) -> dict:
    """Ulozi surova data, vycisti a dopocita vektory jen pro zmenene zaznamy."""
    engine = engine or get_engine()
    stats = {"received": len(raws), "new": 0, "updated": 0, "unchanged": 0, "with_issues": 0}
    changed: list[dict] = []
    with Session(engine) as s:
        for raw in raws:
            ico = str(raw["ico"]).strip().zfill(8)
            h = _hash(raw)
            old = s.get(AresRaw, ico)
            if old and old.payload_hash == h:
                stats["unchanged"] += 1
                continue
            stats["updated" if old else "new"] += 1
            s.merge(AresRaw(ico=ico, payload=raw, payload_hash=h))
            c = clean_company(raw, today)
            stats["with_issues"] += bool(c["quality_issues"])
            s.merge(CompanyClean(**c, source_hash=h))
            changed.append(c)
        s.commit()

        if changed:
            model = embed.model_name()
            texts = [embedding_text(c) for c in changed]
            for c, text, vec in zip(changed, texts, embed.embed_texts(texts)):
                s.merge(CompanyVector(ico=c["ico"], model=model, dim=len(vec),
                                      vector=np.asarray(vec, dtype=np.float32).tobytes(), text=text))
            s.commit()
    return stats


def ingest_icos(icos: list[str], engine: Engine | None = None) -> dict:
    ares = Ares()
    raws = []
    for ico in icos:
        raws.append(ares.get(ico).raw)
    return ingest_raw(raws, engine)


def ingest_samples(engine: Engine | None = None) -> dict:
    d = Path(get_settings().samples_dir)
    raws = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob("*.json"))]
    return ingest_raw(raws, engine)


def counts(engine: Engine | None = None) -> dict:
    with Session(engine or get_engine()) as s:
        return {t.__tablename__: len(s.scalars(select(t)).all()) for t in (AresRaw, CompanyClean, CompanyVector)}


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    print(ingest_samples() if args == ["--samples"] else ingest_icos(args))
    print(counts())
