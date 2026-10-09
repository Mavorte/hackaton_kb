"""Nacteni konfigurace underwritingu z taxonomy.yaml."""
from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache
def taxonomy() -> dict:
    return yaml.safe_load((Path(__file__).parent / "taxonomy.yaml").read_text(encoding="utf-8"))


def doc_types() -> dict:
    return taxonomy()["doc_types"]


def attributes() -> dict:
    return taxonomy()["attributes"]


def rules() -> list[dict]:
    return taxonomy()["rules"]
