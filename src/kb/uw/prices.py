"""Orientacni cenik (USD za 1M tokenu, vstup/vystup): LISTOVE CENY Anthropic prvni strany.
Ceny na Bedrocku jsou samostatne a mohou se lisit - pred rozhodnutim je over na strance cen Bedrocku."""

PRICES = {"haiku-5-5": (0.10, 0.50), "sonnet-5-5": (2.0, 10.0), "opus-5-5": (4.0, 20.0), "fable-5-1": (10.0, 50.0)}


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
