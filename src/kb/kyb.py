"""Mini KYB posouzeni firmy: LLM (Bedrock) nebo pravidla (LLM_PROVIDER=mock)."""
from datetime import date

from kb.aws import bedrock
from kb.clients.ares import Company
from kb.config import get_settings


def _rule_based(c: Company, today: date | None = None) -> dict:
    today = today or date.today()
    flags = []
    if c.dissolved:
        flags.append(f"Subjekt zanikl {c.dissolved}")
    if c.founded and (today - date.fromisoformat(c.founded)).days < 365:
        flags.append("Mlady subjekt (zalozen pred mene nez rokem)")
    if not c.dic:
        flags.append("Chybi DIC")
    risk = "high" if c.dissolved else "medium" if flags else "low"
    return {
        "summary": f"[MOCK] {c.name} (ICO {c.ico}), sidlo {c.address or 'neuvedeno'}.",
        "red_flags": flags,
        "risk": risk,
        "mock": True,
    }


def assess(c: Company) -> dict:
    if get_settings().llm_provider == "mock":
        return _rule_based(c)
    return bedrock.ask_json(
        "Jsi KYB analytik banky. Z techto dat z ARES vytvor strucne shrnuti a seznam red flags "
        '(napr. zaniklý subjekt, mlady subjekt, chybejici DIC). Format: {"summary": str, "red_flags": [str], "risk": "low|medium|high"}\n\n'
        + c.model_dump_json(),
        system="Odpovidej cesky. Vychazej jen z dodanych dat, nic si nevymyslej.",
    )
