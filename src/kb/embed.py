"""Embeddingy: Bedrock Titan (EMBED_MODEL_ID) nebo deterministicky mock (LLM_PROVIDER=mock)."""
import hashlib
import json
import re
import unicodedata

import numpy as np

from kb.aws import bedrock
from kb.config import get_settings

MOCK_DIM = 256
MOCK_MODEL = f"mock-hash-{MOCK_DIM}"


def _mock() -> bool:
    s = get_settings()
    return (s.embed_provider or s.llm_provider) == "mock"


def model_name() -> str:
    return MOCK_MODEL if _mock() else get_settings().embed_model_id


def _mock_vec(text: str) -> np.ndarray:
    """Hashovane slova + znakove trigramy: ve stejnych slovech 'sedi' dotaz s dokumentem (bez AWS)."""
    t = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", t)
    feats = [f"w:{w}" for w in words] + [f"c:{w[i:i + 3]}" for w in words if len(w) > 3 for i in range(len(w) - 2)]
    v = np.zeros(MOCK_DIM, dtype=np.float32)
    for f in feats:
        h = int.from_bytes(hashlib.md5(f.encode()).digest()[:8], "big")
        v[h % MOCK_DIM] += 1.0 if f.startswith("w:") else 0.35
    n = np.linalg.norm(v)
    return v / n if n else v


def embed_texts(texts: list[str]) -> np.ndarray:
    """Vrati matici (n, dim) float32 s L2-normalizovanymi radky."""
    s = get_settings()
    if _mock():
        return np.vstack([_mock_vec(t) for t in texts]) if texts else np.zeros((0, MOCK_DIM), np.float32)
    client = bedrock.runtime_client()
    out = []
    for t in texts:
        body = json.dumps({"inputText": t, "dimensions": s.embed_dim, "normalize": True})
        r = client.invoke_model(modelId=s.embed_model_id, body=body, contentType="application/json")
        out.append(np.asarray(json.loads(r["body"].read())["embedding"], dtype=np.float32))
    m = np.vstack(out)
    return m / np.linalg.norm(m, axis=1, keepdims=True)
