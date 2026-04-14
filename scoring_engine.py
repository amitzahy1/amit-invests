"""
Scoring Engine — professional-grade algorithmic scores (0-100) per ticker.

No LLM calls.  Pure data + rules.  Methodology inspired by institutional
practice and open-source projects (virattt/ai-hedge-fund, FinRobot).

Scores:
  1. Valuation  — multi-method: DCF proxy, P/E vs sector, PEG, analyst target
  2. Technical  — trend (triple MA), momentum (RSI), mean reversion signals
  3. Risk       — portfolio concentration, beta, sector weight, crypto cap
  4. Sentiment  — analyst consensus distribution
  5. Macro      — rates, yield curve, VIX regime, inflation
  6. Quality    — profitability (ROE, margins), health (debt, current ratio), growth

Each sub-category produces a "bullish" / "neutral" / "bearish" signal.
Signals are aggregated into a 0-100 score.
"""

from __future__ import annotations


# ─── Constants ────────────────────────────────────────────────────────────────

# Sector average P/E ratios (approximate, updated periodically)
SECTOR_PE = {
    "Technology": 30,
    "Consumer Discretionary": 25,
    "Financials": 14,
    "Healthcare": 22,
    "Broad Market": 22,
    "Aerospace & Defense": 20,
    "Energy / Uranium": 18,
    "Energy / Nuclear": 20,
    "Crypto": None,  # N/A
    "Insurance (Israel)": 12,
    "Fixed Income (Israel)": None,  # N/A for bonds
}


# ─── Sub-score helpers ────────────────────────────────────────────────────────

def _signal_to_points(signal: str) -> int:
    """Convert bullish/neutral/bearish to numeric points."""
    return {"bullish": 1, "neutral": 0, "bearish": -1}.get(signal, 0)


def _points_to_score(total_points: int, max_points: int) -> int:
    """Convert point tally to 0-100 score. 0 points = 50 (neutral)."""
    if max_points == 0:
        return 50
    # Scale: -max_points → 0, 0 → 50, +max_points → 100
    normalized = (total_points / max_points + 1) / 2  # 0 to 1
    return max(0, min(100, int(normalized * 100)))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. VALUATION SCORE — multi-method, weighted
# ═══════════════════════════════════════════════════════════════════════════════

def _valuation_pe_signal(pe: float | None, sector: str = "") -> str:
    """P/E relative to sector average."""
    if pe is None or pe <= 0:
        return "neutral"
    sector_avg = SECTOR_PE.get(sector, 22) or 22
    ratio = pe / sector_avg
    if ratio < 0.7:
        return "bullish"   # cheap vs sector
    elif ratio < 1.0:
        return "bullish"   # slight discount — still positive
    elif ratio > 1.5:
        return "bearish"   # 50%+ premium
    elif ratio > 1.2:
        return "bearish"   # moderate premium
    return "neutral"


def _valuation_peg_signal(peg: float | None) -> str:
    """PEG ratio (Peter Lynch's key metric)."""
    if peg is None or peg <= 0:
        return "neutral"
    if peg < 1.0:
        return "bullish"   # growth at a discount
    elif peg < 1.5:
        return "neutral"   # fairly priced for growth
    elif peg > 2.5:
        return "bearish"   # expensive relative to growth
    elif peg > 2.0:
        return "bearish"
    return "neutral"


def _valuation_target_signal(analyst_target: float | None,
                              price: float | None) -> str:
    """Analyst target price vs current price."""
    if not analyst_target or not price or price <= 0:
        return "neutral"
    upside = (analyst_target - price) / price
    if upside > 0.20:
        return "bullish"   # 20%+ upside to consensus target
    elif upside > 0.10:
        return "bullish"
    elif upside < -0.10:
        return "bearish"   # below target = downside
    elif upside < 0:
        return "bearish"
    return "neutral"


def _valuation_price_ratios_signal(pe: float | None, pb: float | None = None,
                                    ps: float | None = None) -> str:
    """Check if multiple price ratios are elevated (ai-hedge-fund method).
    P/E > 25, P/B > 3, P/S > 5 are considered elevated.
    """
    elevated = 0
    total = 0
    if pe is not None:
        total += 1
        if pe > 25:
            elevated += 1
    if pb is not None:
        total += 1
        if pb > 3:
            elevated += 1
    if ps is not None:
        total += 1
        if ps > 5:
            elevated += 1
    if total == 0:
        return "neutral"
    if elevated >= 2:
        return "bearish"  # multiple ratios elevated = expensive
    elif elevated == 0:
        return "bullish"  # no elevated ratios = cheap
    return "neutral"


def valuation_score(fundamentals: dict | None, price: float = 0,
                    sector: str = "") -> int:
    """Compute valuation score (0-100) using 4 sub-methods.

    Weights: P/E sector = 30%, PEG = 25%, Analyst target = 25%, Price ratios = 20%
    """
    if not fundamentals:
        return 50
    pe = fundamentals.get("pe")
    peg = fundamentals.get("peg")
    target = fundamentals.get("analyst_target")

    signals = [
        (_valuation_pe_signal(pe, sector), 30),
        (_valuation_peg_signal(peg), 25),
        (_valuation_target_signal(target, price), 25),
        (_valuation_price_ratios_signal(pe), 20),
    ]

    weighted_sum = sum(_signal_to_points(sig) * w for sig, w in signals)
    total_weight = sum(w for _, w in signals)
    # Scale from [-100, +100] to [0, 100]
    normalized = (weighted_sum / total_weight + 1) / 2 * 100
    return max(0, min(100, int(normalized)))


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TECHNICAL SCORE — multi-strategy
# ═══════════════════════════════════════════════════════════════════════════════

def _trend_signal(price: float, ma50: float, ma200: float) -> tuple[str, float]:
    """Triple MA trend analysis. Returns (signal, confidence 0-1)."""
    if not ma50 or not ma200 or not price:
        return "neutral", 0.3
    if price > ma50 > ma200:
        return "bullish", 0.8    # golden cross territory
    elif price > ma200 > ma50:
        return "bullish", 0.4    # recovering, not confirmed
    elif price < ma50 < ma200:
        return "bearish", 0.8    # death cross territory
    elif price < ma200:
        return "bearish", 0.5    # below long-term trend
    elif price > ma200 and price < ma50:
        return "neutral", 0.4    # short-term correction in uptrend
    return "neutral", 0.3


def _rsi_signal(rsi: float) -> tuple[str, float]:
    """RSI momentum + mean reversion signal."""
    if not rsi:
        return "neutral", 0.3
    if rsi < 25:
        return "bullish", 0.9   # deeply oversold = strong entry
    elif rsi < 30:
        return "bullish", 0.7   # oversold
    elif rsi < 40:
        return "bullish", 0.4   # approaching oversold
    elif rsi > 75:
        return "bearish", 0.9   # deeply overbought
    elif rsi > 70:
        return "bearish", 0.7   # overbought
    elif rsi > 60:
        return "bearish", 0.3   # approaching overbought
    return "neutral", 0.3


def _price_vs_ma_signal(price: float, ma200: float) -> tuple[str, float]:
    """How far price is from MA200 (mean reversion / trend strength)."""
    if not price or not ma200:
        return "neutral", 0.3
    deviation = (price - ma200) / ma200
    if deviation > 0.20:
        return "bearish", 0.5   # 20%+ above MA200 = stretched
    elif deviation > 0.10:
        return "neutral", 0.3   # healthy uptrend
    elif deviation < -0.20:
        return "bullish", 0.5   # 20%+ below MA200 = potential rebound
    elif deviation < -0.10:
        return "bullish", 0.3   # moderate discount
    return "neutral", 0.3


def technical_score(price: float = 0, ma50: float = 0, ma200: float = 0,
                    rsi: float = 50, volume_trend: float = 0) -> int:
    """Compute technical score (0-100) using 3 strategies.

    Weights: Trend = 40%, RSI = 35%, Price vs MA200 = 25%
    """
    strategies = [
        (*_trend_signal(price, ma50, ma200), 40),
        (*_rsi_signal(rsi), 35),
        (*_price_vs_ma_signal(price, ma200), 25),
    ]

    weighted_sum = 0
    total_weight = 0
    for signal, confidence, weight in strategies:
        pts = _signal_to_points(signal)
        weighted_sum += pts * confidence * weight
        total_weight += confidence * weight

    if total_weight == 0:
        return 50
    final = weighted_sum / total_weight  # -1 to +1
    score = int((final + 1) / 2 * 100)
    return max(0, min(100, score))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. RISK SCORE — portfolio-aware
# ═══════════════════════════════════════════════════════════════════════════════

def risk_score(portfolio_weight: float = 0, beta: float = 1.0,
               sector_concentration: float = 0,
               is_crypto: bool = False, crypto_cap: float = 10) -> int:
    """Compute risk score (0-100). Higher = lower risk = more favourable."""
    score = 70  # start positive (existing holding = accepted)

    # Concentration risk
    if portfolio_weight > 20:
        score -= 25
    elif portfolio_weight > 15:
        score -= 15
    elif portfolio_weight > 10:
        score -= 5

    # Beta risk
    if beta is None:
        beta = 1.0
    if beta > 2.0:
        score -= 20
    elif beta > 1.5:
        score -= 10
    elif beta < 0.5:
        score += 10  # defensive

    # Sector concentration
    if sector_concentration > 35:
        score -= 15
    elif sector_concentration > 25:
        score -= 5

    # Crypto policy violation
    if is_crypto and portfolio_weight > crypto_cap:
        score -= 25

    return max(0, min(100, score))


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SENTIMENT SCORE — analyst consensus
# ═══════════════════════════════════════════════════════════════════════════════

def sentiment_score(analyst_buy: int = 0, analyst_hold: int = 0,
                    analyst_sell: int = 0,
                    news_headlines: list | None = None) -> int:
    """Compute sentiment score (0-100) from analyst consensus."""
    score = 50
    total = analyst_buy + analyst_hold + analyst_sell
    if total > 0:
        buy_pct = analyst_buy / total
        sell_pct = analyst_sell / total
        # Strong consensus thresholds (from ai-hedge-fund)
        if buy_pct > 0.80:
            score += 25       # overwhelming buy consensus
        elif buy_pct > 0.60:
            score += 15
        elif buy_pct > 0.50:
            score += 5
        if sell_pct > 0.30:
            score -= 20       # significant sell pressure
        elif sell_pct > 0.15:
            score -= 10
        # Coverage breadth bonus
        if total >= 20:
            score += 5        # well-covered stock = more reliable
    return max(0, min(100, score))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MACRO SCORE — economic environment
# ═══════════════════════════════════════════════════════════════════════════════

def macro_score(fed_rate: float | None = None,
                ten_year: float | None = None,
                vix: float | None = None,
                cpi_yoy: float | None = None,
                asset_type: str = "stock") -> int:
    """Compute macro score (0-100) based on economic environment."""
    signals = []

    # VIX regime
    if vix is not None:
        if vix < 15:
            signals.append(("bullish", 0.6))   # calm markets
        elif vix < 20:
            signals.append(("neutral", 0.4))
        elif vix < 30:
            signals.append(("bearish", 0.6))   # elevated fear
        else:
            signals.append(("bearish", 0.9))   # panic

    # Yield curve (inverted = recession signal)
    if ten_year is not None and fed_rate is not None:
        spread = ten_year - fed_rate
        if spread > 1.0:
            signals.append(("bullish", 0.5))   # normal, steep curve
        elif spread > 0:
            signals.append(("neutral", 0.3))   # flat
        elif spread > -0.5:
            signals.append(("bearish", 0.6))   # slightly inverted
        else:
            signals.append(("bearish", 0.8))   # deeply inverted

    # Rate environment impact on asset type
    if fed_rate is not None:
        if asset_type == "bond":
            if fed_rate > 5.5:
                signals.append(("bearish", 0.6))  # high rates hurt long bonds
            elif fed_rate < 3.0:
                signals.append(("bullish", 0.5))
            else:
                signals.append(("neutral", 0.3))
        else:
            if fed_rate < 3.0:
                signals.append(("bullish", 0.5))  # cheap money
            elif fed_rate > 5.5:
                signals.append(("bearish", 0.5))  # tight money
            else:
                signals.append(("neutral", 0.3))

    # Inflation pressure
    if cpi_yoy is not None:
        if cpi_yoy > 5:
            signals.append(("bearish", 0.6))
        elif cpi_yoy > 3:
            signals.append(("neutral", 0.3))
        elif cpi_yoy < 1:
            signals.append(("bearish", 0.4))   # deflation risk
        else:
            signals.append(("bullish", 0.3))    # goldilocks

    if not signals:
        return 50

    weighted_sum = sum(_signal_to_points(s) * c for s, c in signals)
    total_conf = sum(c for _, c in signals)
    final = weighted_sum / total_conf  # -1 to +1
    return max(0, min(100, int((final + 1) / 2 * 100)))


# ═══════════════════════════════════════════════════════════════════════════════
# 6. QUALITY SCORE — profitability + health + growth (ai-hedge-fund style)
# ═══════════════════════════════════════════════════════════════════════════════

def _profitability_signal(roe: float | None, net_margin: float | None,
                           op_margin: float | None = None) -> str:
    """ai-hedge-fund method: count metrics exceeding thresholds.
    ROE > 15%, Net Margin > 20%, Operating Margin > 15%.
    """
    hits = 0
    if roe is not None and roe > 15:
        hits += 1
    if net_margin is not None and net_margin > 20:
        hits += 1
    if op_margin is not None and op_margin > 15:
        hits += 1
    elif net_margin is not None and net_margin > 15:
        hits += 1  # use net margin as proxy if no op margin
    if hits >= 2:
        return "bullish"
    elif hits == 0:
        return "bearish"
    return "neutral"


def _health_signal(debt_equity: float | None,
                    current_ratio: float | None = None) -> str:
    """Financial health: debt levels + liquidity.
    Debt/Equity < 0.5 = healthy. Current Ratio > 1.5 = liquid.
    """
    hits = 0
    checks = 0
    if debt_equity is not None:
        checks += 1
        if debt_equity < 0.5:
            hits += 1
        elif debt_equity > 2.0:
            hits -= 1  # penalise high debt
    if current_ratio is not None:
        checks += 1
        if current_ratio > 1.5:
            hits += 1
    if checks == 0:
        return "neutral"
    if hits >= 1:
        return "bullish"
    elif hits < 0:
        return "bearish"
    return "neutral"


def _growth_signal(revenue_growth: float | None = None,
                    eps_growth: float | None = None) -> str:
    """Growth metrics: Revenue Growth > 10%, EPS Growth > 10%."""
    hits = 0
    checks = 0
    if revenue_growth is not None:
        checks += 1
        if revenue_growth > 10:
            hits += 1
    if eps_growth is not None:
        checks += 1
        if eps_growth > 10:
            hits += 1
    if checks == 0:
        return "neutral"
    if hits >= 1:
        return "bullish"
    elif hits == 0 and checks >= 2:
        return "bearish"
    return "neutral"


def quality_score(fundamentals: dict | None) -> int:
    """Compute quality score (0-100) from 3 sub-signals.

    Profitability (40%) + Health (30%) + Growth (30%)
    """
    if not fundamentals:
        return 50

    prof = _profitability_signal(
        fundamentals.get("roe"),
        fundamentals.get("profit_margin"),
    )
    health = _health_signal(
        fundamentals.get("debt_equity"),
    )
    growth = _growth_signal(
        fundamentals.get("revenue_growth"),
        fundamentals.get("eps_growth"),
    )

    signals = [
        (prof, 40),
        (health, 30),
        (growth, 30),
    ]

    weighted_sum = sum(_signal_to_points(sig) * w for sig, w in signals)
    total_weight = sum(w for _, w in signals)
    normalized = (weighted_sum / total_weight + 1) / 2 * 100
    return max(0, min(100, int(normalized)))


# ═══════════════════════════════════════════════════════════════════════════════
# AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_all_scores(
    ticker: str,
    quote: dict,
    technicals: dict,
    fundamentals: dict | None,
    macro_data: dict,
    news: list[str] | None,
    portfolio_weight: float,
    sector_weight: float,
    asset_type: str,
    crypto_cap: float = 10,
) -> dict[str, int]:
    """Compute all 6 scores for a ticker. Returns {score_name: 0-100}."""
    price = quote.get("price", 0) or 0
    is_bond = "Fixed Income" in (asset_type or "")
    is_crypto = "Crypto" in (asset_type or "")

    # Extract sector for valuation comparison
    sector = asset_type or ""

    return {
        "valuation": valuation_score(fundamentals, price, sector),
        "technical": technical_score(
            price,
            technicals.get("ma50", 0),
            technicals.get("ma200", 0),
            technicals.get("rsi14", 50)),
        "risk": risk_score(
            portfolio_weight,
            (fundamentals or {}).get("beta", 1.0) or 1.0,
            sector_weight,
            is_crypto,
            crypto_cap),
        "sentiment": sentiment_score(
            (fundamentals or {}).get("analyst_buy", 0),
            (fundamentals or {}).get("analyst_hold", 0),
            (fundamentals or {}).get("analyst_sell", 0),
            news),
        "macro": macro_score(
            macro_data.get("fed_rate"),
            macro_data.get("ten_year_yield"),
            macro_data.get("vix"),
            macro_data.get("cpi_yoy"),
            "bond" if is_bond else "stock"),
        "quality": quality_score(fundamentals),
    }


DEFAULT_WEIGHTS = {
    "quality": 30, "valuation": 25, "risk": 20,
    "macro": 15, "sentiment": 5, "technical": 5,
}


def scores_to_verdict(scores: dict[str, int],
                      weights: dict[str, int] | None = None) -> tuple[str, int]:
    """Convert 6 scores to a single verdict + conviction using strategy weights.

    The weights come from settings.json (user-configurable strategy presets).
    A long-term conservative investor will weight Quality and Valuation high,
    while a growth investor will weight Technical and Sentiment high.
    """
    if not scores:
        return "hold", 50

    w = weights or DEFAULT_WEIGHTS
    total_weight = sum(w.get(k, 0) for k in scores)
    if total_weight == 0:
        total_weight = len(scores)
        w = {k: 1 for k in scores}

    # Weighted average score
    weighted_avg = sum(scores[k] * w.get(k, 0) for k in scores) / total_weight

    # Signal counting (weighted)
    bullish_w = sum(w.get(k, 0) for k, v in scores.items() if v > 60)
    bearish_w = sum(w.get(k, 0) for k, v in scores.items() if v < 40)

    if weighted_avg >= 62 and bullish_w > bearish_w:
        verdict = "buy"
    elif weighted_avg <= 38 and bearish_w > bullish_w:
        verdict = "sell"
    else:
        verdict = "hold"

    # Conviction: distance from neutral, scaled by consensus
    distance = abs(weighted_avg - 50)
    consensus = max(bullish_w, bearish_w) / total_weight
    conviction = int(min(100, distance * 1.5 + consensus * 30 + 30))

    return verdict, conviction


def score_color(val: int) -> str:
    """Return CSS color for a score value."""
    if val >= 65:
        return "#047857"
    elif val >= 40:
        return "#b45309"
    else:
        return "#b91c1c"


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE EXPLANATIONS — human-readable reasons for each score
# ═══════════════════════════════════════════════════════════════════════════════

def explain_scores(
    scores: dict[str, int],
    quote: dict,
    technicals: dict,
    fundamentals: dict | None,
    macro_data: dict,
    portfolio_weight: float = 0,
    sector_weight: float = 0,
) -> dict[str, list[str]]:
    """Generate bullet-point explanations for each score.

    Returns {score_name: [reason1, reason2, ...]}.
    """
    f = fundamentals or {}
    explanations: dict[str, list[str]] = {}

    # ── Quality ──────────────────────────────────────────────────────────
    q_reasons = []
    roe = f.get("roe")
    if roe is not None:
        if roe > 20:
            q_reasons.append(f"ROE {roe:.1f}% — excellent profitability (>20%)")
        elif roe > 15:
            q_reasons.append(f"ROE {roe:.1f}% — strong (>15%)")
        elif roe > 0:
            q_reasons.append(f"ROE {roe:.1f}% — below institutional threshold of 15%")
        else:
            q_reasons.append(f"ROE {roe:.1f}% — negative, burning equity")
    margin = f.get("profit_margin")
    if margin is not None:
        if margin > 25:
            q_reasons.append(f"Profit margin {margin:.1f}% — wide moat indicator (>25%)")
        elif margin > 15:
            q_reasons.append(f"Profit margin {margin:.1f}% — healthy")
        elif margin > 0:
            q_reasons.append(f"Profit margin {margin:.1f}% — thin, competitive pressure")
        else:
            q_reasons.append(f"Profit margin {margin:.1f}% — unprofitable")
    de = f.get("debt_equity")
    if de is not None:
        if de < 0.3:
            q_reasons.append(f"Debt/Equity {de:.2f} — very low debt, strong balance sheet")
        elif de < 1.0:
            q_reasons.append(f"Debt/Equity {de:.2f} — manageable")
        elif de < 2.0:
            q_reasons.append(f"Debt/Equity {de:.2f} — elevated leverage")
        else:
            q_reasons.append(f"Debt/Equity {de:.2f} — high debt risk")
    if not q_reasons:
        q_reasons.append("No fundamental data available for this ticker")
    explanations["quality"] = q_reasons

    # ── Valuation ────────────────────────────────────────────────────────
    v_reasons = []
    pe = f.get("pe")
    if pe is not None and pe > 0:
        if pe < 15:
            v_reasons.append(f"P/E {pe:.1f} — cheap by most standards (<15)")
        elif pe < 22:
            v_reasons.append(f"P/E {pe:.1f} — near market average (~22)")
        elif pe < 30:
            v_reasons.append(f"P/E {pe:.1f} — moderately expensive")
        else:
            v_reasons.append(f"P/E {pe:.1f} — expensive, needs high growth to justify")
    peg = f.get("peg")
    if peg is not None and peg > 0:
        if peg < 1.0:
            v_reasons.append(f"PEG {peg:.2f} — growth at a discount (Peter Lynch target <1)")
        elif peg < 2.0:
            v_reasons.append(f"PEG {peg:.2f} — fairly priced for growth")
        else:
            v_reasons.append(f"PEG {peg:.2f} — expensive relative to growth rate")
    target = f.get("analyst_target")
    price = quote.get("price")
    if target and price and price > 0:
        upside = ((target / price) - 1) * 100
        v_reasons.append(f"Analyst target ${target:.0f} vs price ${price:.0f} → {upside:+.0f}% {'upside' if upside > 0 else 'downside'}")
    if not v_reasons:
        v_reasons.append("No valuation data — scored neutral (50)")
    explanations["valuation"] = v_reasons

    # ── Risk ─────────────────────────────────────────────────────────────
    r_reasons = []
    if portfolio_weight > 15:
        r_reasons.append(f"Portfolio weight {portfolio_weight:.1f}% — overweight (>15%)")
    elif portfolio_weight > 10:
        r_reasons.append(f"Portfolio weight {portfolio_weight:.1f}% — moderate position")
    else:
        r_reasons.append(f"Portfolio weight {portfolio_weight:.1f}% — well-sized")
    beta = f.get("beta")
    if beta is not None:
        if beta > 1.5:
            r_reasons.append(f"Beta {beta:.2f} — significantly more volatile than S&P 500")
        elif beta > 1.0:
            r_reasons.append(f"Beta {beta:.2f} — slightly above market volatility")
        elif beta > 0:
            r_reasons.append(f"Beta {beta:.2f} — defensive, less volatile than market")
    if sector_weight > 30:
        r_reasons.append(f"Sector weight {sector_weight:.0f}% — high concentration risk")
    explanations["risk"] = r_reasons

    # ── Macro ────────────────────────────────────────────────────────────
    m_reasons = []
    vix = macro_data.get("vix")
    if vix is not None:
        if vix < 15:
            m_reasons.append(f"VIX {vix:.0f} — low fear, calm market")
        elif vix < 25:
            m_reasons.append(f"VIX {vix:.0f} — normal volatility")
        else:
            m_reasons.append(f"VIX {vix:.0f} — elevated fear in market")
    fed = macro_data.get("fed_rate")
    if fed is not None:
        m_reasons.append(f"Fed rate {fed:.2f}%")
    ty = macro_data.get("ten_year_yield")
    if ty is not None and fed is not None:
        spread = ty - fed
        if spread < 0:
            m_reasons.append(f"Yield curve inverted ({spread:+.2f}%) — recession indicator")
        else:
            m_reasons.append(f"Yield curve normal (10Y-Fed = +{spread:.2f}%)")
    if not m_reasons:
        m_reasons.append("No macro data available")
    explanations["macro"] = m_reasons

    # ── Sentiment ────────────────────────────────────────────────────────
    s_reasons = []
    ab = f.get("analyst_buy", 0)
    ah = f.get("analyst_hold", 0)
    asl = f.get("analyst_sell", 0)
    total_a = ab + ah + asl
    if total_a > 0:
        buy_pct = ab / total_a * 100
        s_reasons.append(f"Analyst consensus: {ab} Buy / {ah} Hold / {asl} Sell ({buy_pct:.0f}% bullish)")
        if total_a >= 20:
            s_reasons.append(f"Well-covered stock ({total_a} analysts)")
        elif total_a < 5:
            s_reasons.append(f"Low coverage ({total_a} analysts) — less reliable")
    else:
        s_reasons.append("No analyst coverage data available")
    explanations["sentiment"] = s_reasons

    # ── Technical ────────────────────────────────────────────────────────
    t_reasons = []
    p = quote.get("price", 0)
    ma50 = technicals.get("ma50")
    ma200 = technicals.get("ma200")
    rsi = technicals.get("rsi14")
    if p and ma50 and ma200:
        if p > ma50 > ma200:
            t_reasons.append(f"Price ${p:.0f} > MA50 ${ma50:.0f} > MA200 ${ma200:.0f} — strong uptrend (golden cross zone)")
        elif p < ma50 < ma200:
            t_reasons.append(f"Price ${p:.0f} < MA50 ${ma50:.0f} < MA200 ${ma200:.0f} — downtrend (death cross zone)")
        elif p > ma200:
            t_reasons.append(f"Price above MA200 but below MA50 — short-term pullback in uptrend")
        else:
            t_reasons.append(f"Price below MA200 — long-term trend is bearish")
    if rsi is not None:
        if rsi < 30:
            t_reasons.append(f"RSI {rsi:.0f} — oversold, potential bounce opportunity")
        elif rsi > 70:
            t_reasons.append(f"RSI {rsi:.0f} — overbought, potential pullback ahead")
        else:
            t_reasons.append(f"RSI {rsi:.0f} — neutral zone")
    if not t_reasons:
        t_reasons.append("Insufficient price history for technical analysis")
    explanations["technical"] = t_reasons

    return explanations
