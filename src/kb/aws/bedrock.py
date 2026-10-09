"""Tenky wrapper nad Bedrock Converse API."""
import json
import re

import boto3

from kb.config import get_settings


class UsageMeter:
    """Pocita tokeny podle modelu (ze zpetne vazby Converse), aby slo vycislit naklady na pripad."""

    def __init__(self) -> None:
        self.by_model: dict[str, dict[str, int]] = {}

    def reset(self) -> None:
        self.by_model = {}

    def add(self, model: str, usage: dict) -> None:
        m = self.by_model.setdefault(model, {"input": 0, "output": 0, "calls": 0})
        m["input"] += int(usage.get("inputTokens", 0))
        m["output"] += int(usage.get("outputTokens", 0))
        m["calls"] += 1

    def snapshot(self) -> dict:
        return {k: dict(v) for k, v in self.by_model.items()}


meter = UsageMeter()


def session() -> boto3.Session:
    s = get_settings()
    # prazdny AWS_PROFILE z .env nesmi prebit default chain
    return boto3.Session(profile_name=s.aws_profile or None, region_name=s.aws_region)


def runtime_client():
    return session().client("bedrock-runtime")


def ask(
    prompt: str,
    system: str | None = None,
    model_id: str | None = None,
    max_tokens: int = 1024,
    temperature: float | None = None,
    client=None,
    images: list[bytes] | None = None,
) -> str:
    if get_settings().llm_provider == "mock" and client is None:
        return f"[MOCK] Odpoved bez volani modelu. Dotaz: {prompt[:80]}"
    client = client or runtime_client()
    # temperature se posila jen na vyzadani: novejsi modely (Opus 5.5) nestandardni hodnotu odmitnou (400)
    inference = {"maxTokens": max_tokens}
    if temperature is not None:
        inference["temperature"] = temperature
    kwargs = {
        "modelId": model_id or get_settings().bedrock_model_id,
        "messages": [{"role": "user", "content": [
            *({"image": {"format": "png", "source": {"bytes": b}}} for b in (images or [])),
            {"text": prompt},
        ]}],
        "inferenceConfig": inference,
    }
    if system:
        kwargs["system"] = [{"text": system}]
    out = client.converse(**kwargs)
    meter.add(kwargs["modelId"], out.get("usage", {}))
    blocks = out["output"]["message"]["content"]
    return "".join(b["text"] for b in blocks if "text" in b)


def ask_json(prompt: str, system: str | None = None, **kw) -> dict:
    """Pozada model o JSON a vrati ho naparsovany (oreze ```json ohraniceni)."""
    if get_settings().llm_provider == "mock" and kw.get("client") is None:
        return {"mock": True}
    system = (system or "") + "\nOdpovez pouze validnim JSON, bez dalsiho textu."
    text = ask(prompt, system=system.strip(), **kw).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)
