"""
Factor Exposure Decomposition — analyzes which investment factors the portfolio
is exposed to.

Classic academic factors (Fama-French + extensions):
- Quality      — high ROE, stable earnings, low debt
- Value        — low P/E, P/B vs sector
- Momentum     — rising price, MA200 positive
- Size         — small-cap vs large-cap (SMB factor)
- Low Vol      — beta < 1.0 (defensive)
- Yield        — high dividend yield
- Growth       — high revenue/EPS growth
- Quality vs Speculation — profit margin, cash flow stability

For each factor we compute:
- Exposure score (0-100) — how heavily the portfolio tilts toward this factor
- Top contributors (which holdings drive this exposure)
- Comparison to market benchmark (S&P 500 averages)
"""

from __future__ import annotations


# Market benchmark averages (S&P 500 rough values as of 2026)
MARKET_BENCHMARK = {
    "pe_avg": 22,
    "roe_avg": 17,
    "profit_margin_avg": 12,
    "revenue_growth_avg": 6,
    "dividend_yield_avg": 1.5,
    "beta_avg": 1.0,
    "debt_equity_avg": 1.0,
}


def _quality_exposure(fundamentals: dict) -> int:
    """How much does this holding tilt toward Quality factor? (0-100)"""
    if not fundamentals:
        return 50
    score = 50
    roe = fundamentals.get("roe")
    margin = fundamentals.get("profit_margin")
    debt_eq = fundamentals.get("debt_equity")
    if roe is not None:
        if roe > 25: score += 20
        elif roe > 15: score += 10
        elif roe < 8: score -= 10
    if margin is not None:
        if margin > 25: score += 15
        elif margin > 15: score += 5
        elif margin < 5: score -= 10
    if debt_eq is not None:
        if debt_eq < 0.3: score += 10
        elif debt_eq > 2.0: score -= 15
    return max(0, min(100, score))


def _value_exposure(fundamentals: dict) -> int:
    """How much does this holding tilt toward Value factor? (0-100)"""
    if not fundamentals:
        return 50
    score = 50
    pe = fundamentals.get("pe")
    if pe is not None and pe > 0:
        if pe < 12: score += 25   # deep value
        elif pe < 18: score += 10
        elif pe > 30: score -= 15
        elif pe > 25: score -= 5
    peg = fundamentals.get("peg")
    if peg is not None and peg > 0:
        if peg < 1.0: score += 10
        elif peg > 2.5: score -= 10
    return max(0, min(100, score))


def _momentum_exposure(price: float, ma50: float, ma200: float, rsi: float) -> int:
    """How much does this holding tilt toward Momentum factor? (0-100)"""
    if not price or not ma50 or not ma200:
        return 50
    score = 50
    if price > ma50 > ma200:
        score += 25  # confirmed uptrend
    elif price > ma200:
        score += 10
    elif price < ma50 < ma200:
        score -= 25  # downtrend
    if rsi:
        if 50 < rsi <= 70: score += 10
        elif rsi > 70: score += 5   # strong but risky
        elif rsi < 40: score -= 10
    return max(0, min(100, score))


def _size_exposure(market_cap: float | None) -> int:
    """Size factor: small-cap = high exposure, mega-cap = low exposure."""
    if not market_cap:
        return 50
    if market_cap > 500_000_000_000:
        return 20  # mega-cap (>$500B) — low Size factor
    elif market_cap > 100_000_000_000:
        return 35  # large-cap
    elif market_cap > 10_000_000_000:
        return 55  # mid-cap
    elif market_cap > 2_000_000_000:
        return 75  # small-cap
    else:
        return 90  # micro-cap — high size premium


def _low_vol_exposure(beta: float | None) -> int:
    """Low-volatility factor: low beta = high exposure."""
    if beta is None:
        return 50
    if beta < 0.6:
        return 90
    elif beta < 0.8:
        return 75
    elif beta < 1.0:
        return 60
    elif beta < 1.3:
        return 40
    elif beta < 1.8:
        return 25
    else:
        return 10


def _yield_exposure(dividend_yield: float | None) -> int:
    """Yield factor: high dividend = high exposure."""
    if dividend_yield is None:
        return 50
    if dividend_yield > 4:
        return 90
    elif dividend_yield > 3:
        return 75
    elif dividend_yield > 2:
        return 65
    elif dividend_yield > 1:
        return 55
    elif dividend_yield > 0:
        return 45
    else:
        return 30  # no dividend


def _growth_exposure(fundamentals: dict) -> int:
    """Growth factor: high revenue/EPS growth = high exposure."""
    if not fundamentals:
        return 50
    score = 50
    rev_growth = fundamentals.get("revenue_growth")
    eps_growth = fundamentals.get("eps_growth")
    if rev_growth is not None:
        if rev_growth > 30: score += 25
        elif rev_growth > 15: score += 15
        elif rev_growth > 10: score += 5
        elif rev_growth < 0: score -= 15
    if eps_growth is not None:
        if eps_growth > 25: score += 15
        elif eps_growth > 10: score += 5
        elif eps_growth < 0: score -= 10
    return max(0, min(100, score))


def compute_factor_exposure(holding: dict, fundamentals: dict | None,
                              quote: dict, technicals: dict) -> dict:
    """Compute factor exposures for a single holding."""
    price = quote.get("price", 0) or 0
    ma50 = technicals.get("ma50", 0) or 0
    ma200 = technicals.get("ma200", 0) or 0
    rsi = technicals.get("rsi14", 50) or 50

    f = fundamentals or {}

    return {
        "quality":   _quality_exposure(f),
        "value":     _value_exposure(f),
        "momentum":  _momentum_exposure(price, ma50, ma200, rsi),
        "size":      _size_exposure(f.get("market_cap")),
        "low_vol":   _low_vol_exposure(f.get("beta")),
        "yield":     _yield_exposure(f.get("dividend_yield")),
        "growth":    _growth_exposure(f),
    }


def compute_portfolio_factors(holdings_with_data: list[dict]) -> dict:
    """Aggregate factor exposures across the portfolio (weighted by position size).

    Args:
        holdings_with_data: list of dicts with:
            ticker, weight_pct, fundamentals, quote, technicals
    """
    total_weight = sum(h.get("weight_pct", 0) for h in holdings_with_data)
    if total_weight == 0:
        return {}

    factor_names = ["quality", "value", "momentum", "size", "low_vol", "yield", "growth"]
    portfolio_factors = {f: 0.0 for f in factor_names}
    contributors = {f: [] for f in factor_names}

    for h in holdings_with_data:
        w = h.get("weight_pct", 0) / total_weight
        if w == 0:
            continue
        exposures = compute_factor_exposure(
            h, h.get("fundamentals"), h.get("quote", {}), h.get("technicals", {}),
        )
        for f in factor_names:
            contribution = exposures[f] * w
            portfolio_factors[f] += contribution
            contributors[f].append({
                "ticker": h.get("ticker"),
                "exposure": exposures[f],
                "weight": round(h.get("weight_pct", 0), 1),
                "contribution": round(contribution, 1),
            })

    # Sort contributors by contribution
    for f in factor_names:
        contributors[f].sort(key=lambda c: -c["contribution"])

    return {
        "factors": {f: round(v, 1) for f, v in portfolio_factors.items()},
        "top_contributors": {
            f: contributors[f][:3] for f in factor_names
        },
        "interpretation": _interpret_factors(portfolio_factors),
    }


def _interpret_factors(factors: dict) -> list[str]:
    """Generate human-readable interpretation of the factor profile."""
    insights = []
    q = factors.get("quality", 50)
    v = factors.get("value", 50)
    m = factors.get("momentum", 50)
    g = factors.get("growth", 50)
    lv = factors.get("low_vol", 50)
    sz = factors.get("size", 50)
    yd = factors.get("yield", 50)

    # Style classification
    if q > 65 and v > 65:
        insights.append("🏛️ **Quality Value** tilt — like Buffett. Strong businesses at fair prices.")
    elif q > 65 and g > 65:
        insights.append("🚀 **Quality Growth** tilt — like ARKK but pickier. Best-in-class growth companies.")
    elif v > 65 and g < 45:
        insights.append("💰 **Deep Value** tilt — like Graham. Cheap, but may be value traps.")
    elif g > 70 and v < 45:
        insights.append("⚡ **Pure Growth** tilt — like Cathie Wood. Expensive but high ceiling.")
    elif m > 65:
        insights.append("📈 **Momentum-heavy** — riding trends. Watch for reversals.")
    elif lv > 65:
        insights.append("🛡️ **Defensive** tilt — low volatility, survives downturns but may underperform bull markets.")

    # Warnings
    if sz < 30:
        insights.append("⚠️ **Mega-cap concentrated** — limited small-cap upside. S&P 500 correlation will be high.")
    if yd < 35:
        insights.append("💸 **Low yield** — minimal dividend income. OK for growth-focused strategy.")
    if lv < 35:
        insights.append("⚠️ **High volatility** — expect larger drawdowns than market in corrections.")

    if not insights:
        insights.append("⚖️ **Balanced factor profile** — no single factor dominates.")

    return insights
