"""Agent: Bedrock Converse tool-use smycka, jejiz nastroje pochazeji z MCP serveru (kb.mcp_server).

    python -m kb.agent "Kolik je aktivnich firem podle mesta?"
Bez Bedrocku (LLM_PROVIDER=mock) rozhoduje jednoducha pravidla, aby slo vyzkouset cely tok.
"""
import asyncio
import json
import os
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kb.aws import bedrock
from kb.config import get_settings

SYSTEM = (
    "Jsi analytik KYB v bance. Odpovidej cesky a stručně. K datum o firmach pouzivej POUZE dodane nastroje. "
    "Pro agregace nejdriv zavolej describe_semantic_layer a pak query_metrics; pro hledani podle vyznamu "
    "search_companies; pro konkretni firmu get_company. Pro underwritingove pripady (slozky dokumentu) "
    "pouzij uw_get_case, uw_explain_rule, uw_rule_stats, uw_case_precedents a query_metrics s entity uw_rules / uw_docs. Uvadej ICO nebo id pripadu. Pokud data chybi, rekni to - nic si nevymyslej."
)


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "kb.mcp_server"], env=dict(os.environ))


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


def _mock_plan(q: str) -> tuple[str, dict]:
    ql = q.lower()
    case = re.search(r"\b(?:eval[A-C]|seed|case)_\d{3}\b", q)
    rule = re.search(r"\bR\d{2}_[a-z_]+\b", q)
    if case and rule:
        return "uw_explain_rule", {"case_id": case.group(), "rule_id": rule.group()}
    if case:
        return "uw_get_case", {"case_id": case.group()}
    if rule:
        return "uw_rule_stats", {"rule_id": rule.group()}
    if ("pravidl" in ql or "kontrol" in ql) and any(w in ql for w in ("selh", "chyb", "nejčast", "nejcast")):
        return "query_metrics", {"entity": "uw_rules", "metrics": ["fail_count", "rule_count", "fail_rate_pct"], "group_by": ["rule_id"], "filters": []}
    if "dokument" in ql and any(w in ql for w in ("kolik", "počet", "pocet", "typ")):
        return "query_metrics", {"entity": "uw_docs", "metrics": ["doc_count", "avg_confidence"], "group_by": ["doc_type"], "filters": []}
    m = re.search(r"\b\d{8}\b", q)
    if m:
        return "get_company", {"ico": m.group()}
    if any(w in ql for w in ("kolik", "počet", "pocet", "průměr", "prumer", "podíl", "podil")):
        group = next((d for kw, d in (("měst", "city"), ("mest", "city"), ("právní", "legal_form"), ("pravni", "legal_form"),
                                      ("obor", "nace_division_label"), ("rok", "founded_year"), ("stav", "status")) if kw in ql), None)
        metrics = ["company_count"]
        if "průměr" in ql or "prumer" in ql:
            metrics = ["avg_age_years"]
        if "podíl" in ql or "podil" in ql:
            metrics = ["dissolved_share_pct"]
        filters = ["active"] if "aktivn" in ql else ["dissolved"] if "zanikl" in ql else []
        return "query_metrics", {"metrics": metrics, "group_by": [group] if group else [], "filters": filters}
    return "search_companies", {"query": q, "limit": 5}


def _mock_answer(tool: str, data) -> str:
    if tool == "uw_get_case":
        return "[MOCK]\n\n```\n" + data["report"] + "\n```" if "report" in data else f"[MOCK] {data.get('error')}"
    if tool == "uw_explain_rule":
        if "error" in data:
            return f"[MOCK] {data['error']}"
        ev = "\n".join(f"- {e['filename']}: {e['attribute']} = {e['value']!r} (strana {e['page']})" for e in data["evidence"])
        return f"[MOCK] {data['rule_id']}: {data['outcome']}. {data['message']}\n\nDůkazy:\n{ev or '- žádné'}"
    if tool == "uw_rule_stats":
        return f"[MOCK] Pravidlo {data['rule_id']}: selhalo {data['fails']} z {data['cases']} případů ({data['fail_rate_pct']} %). " + "; ".join(data["example_messages"])
    if tool == "query_metrics":
        head = "| " + " | ".join(data["columns"]) + " |\n|" + "---|" * len(data["columns"])
        rows = ["| " + " | ".join(str(x) for x in row) + " |" for row in data["rows"]]
        return "[MOCK] Výsledek dotazu:\n\n" + head + "\n" + "\n".join(rows)
    if tool == "search_companies":
        return "[MOCK] Nejbližší firmy:\n" + "\n".join(f"- {r['name']} ({r['ico']}, {r['city']}, {r['status']}) skóre {r['score']}" for r in data)
    if "error" in data:
        return f"[MOCK] {data['error']}"
    flags = "; ".join(data["red_flags"]) or "žádné red flags"
    return f"[MOCK] {data['name']} (IČO {data['ico']}), {data['city']}, {data['status']}. {flags}."


async def _call(session: ClientSession, name: str, args: dict):
    """Vrati (data, text pro LLM, isError). Data bere ze strukturovaneho vystupu MCP (seznamy jsou v {'result': [...]})."""
    res = await session.call_tool(name, args)
    sc = res.structuredContent
    if sc is not None:
        data = sc["result"] if set(sc) == {"result"} else sc
        return data, json.dumps(data, ensure_ascii=False, default=str), res.isError
    text = _text(res)
    try:
        return json.loads(text), text, res.isError
    except json.JSONDecodeError:
        return text, text, res.isError


async def ask_agent(question: str, max_steps: int = 8) -> dict:
    steps: list[dict] = []
    async with stdio_client(_server_params()) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        if get_settings().llm_provider == "mock":
            tool, args = _mock_plan(question)
            data, text, _ = await _call(session, tool, args)
            steps.append({"tool": tool, "args": args, "result": text[:500]})
            return {"answer": _mock_answer(tool, data), "steps": steps}

        tools = (await session.list_tools()).tools
        tool_config = {"tools": [{"toolSpec": {"name": t.name, "description": t.description or t.name,
                                               "inputSchema": {"json": t.inputSchema}}} for t in tools]}
        client = bedrock.runtime_client()
        messages = [{"role": "user", "content": [{"text": question}]}]
        for _ in range(max_steps):
            out = await asyncio.to_thread(
                client.converse, modelId=get_settings().bedrock_model_id, messages=messages,
                system=[{"text": SYSTEM}], toolConfig=tool_config, inferenceConfig={"maxTokens": 2048})
            msg = out["output"]["message"]
            messages.append(msg)
            if out["stopReason"] != "tool_use":
                return {"answer": "".join(b.get("text", "") for b in msg["content"]), "steps": steps}
            results = []
            for block in msg["content"]:
                if "toolUse" not in block:
                    continue
                tu = block["toolUse"]
                _, text, is_err = await _call(session, tu["name"], tu["input"])
                steps.append({"tool": tu["name"], "args": tu["input"], "result": text[:500]})
                results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [{"text": text}],
                                               "status": "error" if is_err else "success"}})
            messages.append({"role": "user", "content": results})
        return {"answer": "Agent překročil maximální počet kroků.", "steps": steps}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    res = asyncio.run(ask_agent(" ".join(sys.argv[1:])))
    for s in res["steps"]:
        print(f"[tool] {s['tool']} {json.dumps(s['args'], ensure_ascii=False)}")
    print(res["answer"])
