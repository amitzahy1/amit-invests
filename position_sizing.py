"""
Position Sizing — recommends HOW MUCH to allocate based on score + risk.

Uses a modified Kelly-lite approach:
- Base allocation from strategy (conservative = smaller, growth = larger)
- Scaled by conviction (higher score = larger position, capped)
- Scaled down by risk score (high risk = smaller position)
- Respects sector concentration limits
- Respects crypto cap for crypto tickers
"""

from __future__ import annotations


# Max position size per asset (% of portfolio) by strategy
MAX_POSITION_BY_STRATEGY = {
    "conservative_longterm": 12,   # no single position >12%
    "value": 15,                    # concentrated value plays OK
    "growth": 10,                   # diversify high-conviction growth
    "income": 10,
    "balanced": 12,
}

# Target new-position sizes (baseline) by strategy
TARGET_POSITION_BY_STRATEGY = {
    "conservative_longterm": 5,
    "value": 7,
    "growth": 4,
    "income": 6,
    "balanced": 5,
}


def compute_position_size(
    scores: dict,
    weighted_avg: float,
    risk_score_val: int,
    current_weight: float = 0,
    sector_weight: float = 0,
    is_crypto: bool = False,
    crypto_cap: float = 10,
    strategy: str = "conservative_longterm",
    is_new_position: bool = False,
) -> dict:
    """Recommend position size based on scores, risk, and portfolio context.

    Returns:
    {
        "target_pct": float,        # recommended % of portfolio
        "action": str,              # "add" / "reduce" / "hold" / "exit"
        "delta_pct": float,         # how much to change from current
        "reason": str,              # 1-line explanation
        "max_allowed": float,       # hard cap for this asset
    }
    """
    max_pos = MAX_POSITION_BY_STRATEGY.get(strategy, 10)
    target_pos = TARGET_POSITION_BY_STRATEGY.get(strategy, 5)

    # Base target = baseline for strategy
    if is_new_position:
        # New positions start at the baseline
        raw_target = target_pos
    else:
        # Existing — use current as anchor
        raw_target = current_weight

    # Scale by conviction (weighted_avg 0-100)
    # wavg >= 80 → 1.4x, wavg 70 → 1.2x, wavg 50 → 1.0x, wavg 30 → 0.5x, wavg <=20 → 0.2x
    if weighted_avg >= 80:
        conviction_mult = 1.4
    elif weighted_avg >= 70:
        conviction_mult = 1.2
    elif weighted_avg >= 60:
        conviction_mult = 1.0
    elif weighted_avg >= 40:
        conviction_mult = 0.8
    elif weighted_avg >= 30:
        conviction_mult = 0.5
    else:
        conviction_mult = 0.2  # very bearish → trim down aggressively

    # Scale by risk score (higher risk score = safer = larger allowed)
    # risk > 70 → 1.0x, risk 40 → 0.8x, risk < 30 → 0.6x
    if risk_score_val >= 70:
        risk_mult = 1.0
    elif risk_score_val >= 50:
        risk_mult = 0.9
    elif risk_score_val >= 30:
        risk_mult = 0.7
    else:
        risk_mult = 0.5  # high risk = shrink position

    target = raw_target * conviction_mult * risk_mult

    # Apply caps
    # Crypto cap
    if is_crypto and target > crypto_cap:
        target = crypto_cap
    # Sector concentration cap (no sector > 35%)
    if sector_weight + (target - current_weight) > 35:
        target = max(0, 35 - sector_weight + current_weight)
    # Hard max per strategy
    target = min(target, max_pos)
    target = max(0, target)

    delta = target - current_weight
    abs_delta = abs(delta)

    # Determine action
    if target < 0.5:
        action = "exit"
        reason = "Scores too low to justify any position"
    elif abs_delta < 0.5:
        action = "hold"
        reason = "Position size is appropriate — no change needed"
    elif delta > 2:
        action = "add"
        reason = f"Strong scores + room to grow (current {current_weight:.1f}% → target {target:.1f}%)"
    elif delta > 0:
        action = "add_small"
        reason = f"Slight increase warranted ({current_weight:.1f}% → {target:.1f}%)"
    elif delta < -2:
        action = "reduce"
        reason = f"Scores suggest trimming ({current_weight:.1f}% → {target:.1f}%)"
    else:
        action = "reduce_small"
        reason = f"Small trim recommended ({current_weight:.1f}% → {target:.1f}%)"

    return {
        "target_pct": round(target, 1),
        "action": action,
        "delta_pct": round(delta, 1),
        "reason": reason,
        "max_allowed": max_pos,
    }


# ── Stop-Loss / Take-Profit Triggers ────────────────────────────────────────

def compute_exit_triggers(
    verdict: str,
    weighted_avg: float,
    current_price: float,
    ma200: float | None = None,
    fundamentals: dict | None = None,
    strategy: str = "conservative_longterm",
) -> dict:
    """Compute stop-loss and take-profit levels.

    Returns:
    {
        "stop_loss_price": float,
        "stop_loss_pct": float,      # e.g. -15% from current
        "take_profit_price": float,
        "take_profit_pct": float,
        "trailing_enabled": bool,
        "re_evaluate_if": str,       # qualitative trigger
    }
    """
    if not current_price or current_price <= 0:
        return {}

    # Stop-loss by strategy (% below current)
    # Conservative = wider stops (less whipsaw), Growth = tighter (protect gains)
    stop_pct_by_strategy = {
        "conservative_longterm": 20,  # -20% triggers reevaluation
        "value": 25,                  # value plays need more patience
        "growth": 15,                 # growth must prove momentum
        "income": 18,
        "balanced": 18,
    }
    take_pct_by_strategy = {
        "conservative_longterm": 40,  # +40% = meaningful re-evaluation
        "value": 50,
        "growth": 30,                 # take profits faster on growth
        "income": 35,
        "balanced": 40,
    }

    stop_pct = stop_pct_by_strategy.get(strategy, 20)
    take_pct = take_pct_by_strategy.get(strategy, 40)

    # Adjust stop based on MA200 if available (don't set stop above MA200)
    stop_price = current_price * (1 - stop_pct / 100)
    if ma200 and ma200 > stop_price and ma200 < current_price:
        # Use MA200 as a natural support if it's between stop and price
        stop_price = ma200
        stop_pct = ((current_price - ma200) / current_price) * 100

    take_price = current_price * (1 + take_pct / 100)

    # Qualitative re-evaluation trigger
    if verdict == "buy":
        re_eval = "If weighted score drops below 50, or if quality score breaks below 40"
    elif verdict == "sell":
        re_eval = "If weighted score rises above 60, reconsider"
    else:
        re_eval = "If weighted score moves out of 40-60 range"

    return {
        "stop_loss_price": round(stop_price, 2),
        "stop_loss_pct": round(-stop_pct, 1),
        "take_profit_price": round(take_price, 2),
        "take_profit_pct": round(take_pct, 1),
        "trailing_enabled": verdict == "buy" and weighted_avg > 70,
        "re_evaluate_if": re_eval,
    }
