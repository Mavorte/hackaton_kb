"""Orientacni cenik (USD za 1M tokenu, vstup/vystup).

Hackathonovy ucet (Bedrock, EU profily, overeno organizatory): Haiku 4.5 1.10/5.50, Sonnet 4.5 3.30/16.50, Opus 4.5 5.50/27.50;
cachovany vstup je 10x levnejsi (Sonnet 4.5: 0.33). Radka Claude 5 tam neni k dispozici - ceny 5.x jsou jen listove (Anthropic prvni strany)
pro srovnani. Modely mimo Claude (Nova, Qwen, GPT-OSS...) nemaji v podkladech cenu -> hlasi se jako 'unpriced'.
"""

PRICES = {
    "haiku-4-5": (1.10, 5.50),
    "sonnet-4-5": (3.30, 16.50),
    "opus-4-5": (5.50, 27.50),
    # jen srovnani (neni na hackathonovem ucte):
    "haiku-5-5": (0.10, 0.50),
    "sonnet-5-5": (2.0, 10.0),
    "opus-5-5": (4.0, 20.0),
    "fable-5-1": (10.0, 50.0),
}


def estimate_cost(usage: dict) -> dict:
    total, unknown = 0.0, []
    per = {}
    for model, u in usage.items():
        price = next((p for k, p in PRICES.items() if k in model), None)
        if price is None:
            unknown.append(model)
            continue
        c = u["input"] / 1e6 * price[0] + u["output"] / 1e6 * price[1]
        per[model] = round(c, 5)
        total += c
    return {"usd": round(total, 5), "by_model": per, "unpriced_models": unknown}
