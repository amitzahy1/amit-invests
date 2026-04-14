"""
Rebalancing Engine — compares current allocation vs target, suggests trades.

Target allocations are derived from sector preferences in settings.json.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent


# Default target allocations by sector (total = 100%)
DEFAULT_TARGETS = {
    "Broad Market": 35,
    "Technology": 12,
    "Fixed Income (Israel)": 20,
    "Aerospace & Defense": 5,
    "Energy / Uranium": 4,
    "Energy / Nuclear": 4,
    "Healthcare": 5,
    "Financials": 5,
    "Consumer Discretionary": 3,
    "Crypto": 3,
    "Insurance (Israel)": 3,
    "Other": 1,
}


def compute_drift(current_weights: dict[str, float],
                  targets: dict[str, float] | None = None) -> list[dict]:
    """Compute allocation drift per sector.

    Returns sorted list of {sector, current, target, drift, action}.
    Positive drift = overweight, negative = underweight.
    """
    if targets is None:
        targets = DEFAULT_TARGETS

    all_sectors = set(list(current_weights.keys()) + list(targets.keys()))
    result = []
    for sec in all_sectors:
        current = current_weights.get(sec, 0)
        target = targets.get(sec, 0)
        drift = current - target
        if abs(drift) < 0.5:
            action = "on target"
        elif drift > 5:
            action = "reduce"
        elif drift > 2:
            action = "slightly overweight"
        elif drift < -5:
            action = "increase"
        elif drift < -2:
            action = "slightly underweight"
        else:
            action = "ok"
        result.append({
            "sector": sec,
            "current_pct": round(current, 1),
            "target_pct": round(target, 1),
            "drift_pct": round(drift, 1),
            "action": action,
        })

    return sorted(result, key=lambda x: -abs(x["drift_pct"]))


def suggest_trades(drift: list[dict], portfolio_value_usd: float = 0) -> list[dict]:
    """Generate specific trade suggestions from drift data.

    Returns list of {sector, action, amount_usd, description}.
    """
    suggestions = []
    for d in drift:
        if d["action"] in ("on target", "ok"):
            continue
        amount = abs(d["drift_pct"]) / 100 * portfolio_value_usd if portfolio_value_usd else 0
        if d["drift_pct"] > 2:
            desc_he = f"הפחת חשיפה ל-{d['sector']} ({d['current_pct']:.0f}% → {d['target_pct']:.0f}%)"
            suggestions.append({
                "sector": d["sector"],
                "action": "SELL",
                "drift_pct": d["drift_pct"],
                "amount_usd": round(amount),
                "description_he": desc_he,
            })
        elif d["drift_pct"] < -2:
            desc_he = f"הגדל חשיפה ל-{d['sector']} ({d['current_pct']:.0f}% → {d['target_pct']:.0f}%)"
            suggestions.append({
                "sector": d["sector"],
                "action": "BUY",
                "drift_pct": d["drift_pct"],
                "amount_usd": round(amount),
                "description_he": desc_he,
            })

    return sorted(suggestions, key=lambda x: -abs(x["drift_pct"]))
