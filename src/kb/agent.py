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
    "search_companies; pro konkretni firmu get_company. Uvadej ICO. Pokud data chybi, rekni to - nic si nevymyslej."
)


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "kb.mcp_server"], env=dict(os.environ))


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


def _mock_plan(q: str) -> tuple[str, dict]:
    ql = q.lower()
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
    if tool == "query_metrics":
        lines = [" | ".join(str(x) for x in row) for row in data["rows"]]
        return "[MOCK] " + " | ".join(data["columns"]) + "\n" + "\n".join(lines)
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
