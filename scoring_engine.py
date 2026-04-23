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
    """P/E relative to sector average. Handles Crypto/Fixed Income (None sector_avg)."""
    if pe is None or pe <= 0:
        return "neutral"
    sector_avg = SECTOR_PE.get(sector)
    if sector_avg is None or sector_avg <= 0:
        return "neutral"  # Can't compare (Crypto, Fixed Income, unknown sector)
    ratio = pe / sector_avg
    # Check smallest thresholds first — order matters!
    if ratio < 0.7:
        return "bullish"   # cheap vs sector
    elif ratio < 1.0:
        return "bullish"   # slight discount — still positive
    elif ratio <= 1.2:
        return "neutral"   # fair value zone
    elif ratio <= 1.5:
        return "bearish"   # moderate premium (1.2-1.5)
    else:
        return "bearish"   # 50%+ premium


def _valuation_peg_signal(peg: float | None) -> str:
    """PEG ratio (Peter Lynch's key metric). Check smallest thresholds first."""
    if peg is None or peg <= 0:
        return "neutral"
    if peg < 1.0:
        return "bullish"   # growth at a discount
    elif peg <= 1.5:
        return "neutral"   # fairly priced for growth
    elif peg <= 2.0:
        return "neutral"   # slightly expensive but not egregious
    elif peg <= 2.5:
        return "bearish"   # expensive (2.0-2.5)
    else:
        return "bearish"   # very expensive (>2.5)


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
    pb = fundamentals.get("pb") or fundamentals.get("price_to_book")
    ps = fundamentals.get("ps") or fundamentals.get("price_to_sales")

    signals = [
        (_valuation_pe_signal(pe, sector), 30),
        (_valuation_peg_signal(peg), 25),
        (_valuation_target_signal(target, price), 25),
        (_valuation_price_ratios_signal(pe, pb, ps), 20),
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
    if not price or not ma200 or ma200 == 0:
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
    """Compute risk score (0-100). Higher = lower risk = safer.

    Neutral baseline = 50. Bonuses for defensive traits, penalties for risks.
    """
    score = 50  # true neutral baseline

    # Bonus for reasonable position sizing
    if 0 < portfolio_weight <= 10:
        score += 10  # well-sized position
    elif portfolio_weight > 20:
        score -= 25  # dangerous overweight
    elif portfolio_weight > 15:
        score -= 15  # overweight
    elif portfolio_weight > 10:
        score -= 5   # slightly large

    # Beta risk (safely handle None/0/negative)
    if beta is None or beta == 0:
        beta = 1.0  # default to market if no data
    if beta < 0:
        score -= 15  # inverse/short ETF — unusual risk profile
    elif beta > 2.0:
        score -= 20  # highly volatile
    elif beta > 1.5:
        score -= 10  # elevated volatility
    elif beta < 0.5:
        score += 10  # defensive (low volatility)

    # Sector concentration risk
    if sector_concentration > 40:
        score -= 20
    elif sector_concentration > 30:
        score -= 10
    elif sector_concentration > 20:
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
                    news_headlines: list | None = None,
                    social_sentiment: dict | None = None,
                    news_sentiment: dict | None = None) -> int:
    """Compute sentiment score (0-100) from analyst consensus + social + news.

    Blend (2026):
      - 55% analyst consensus (hard data, high confidence)
      - 25% social sentiment   (Twitter/X via Perplexity, if present)
      - 20% news sentiment     (finance-lexicon scorer over recent headlines)

    If only analyst data is available, weights collapse to 100% analyst.
    Requires MIN_COVERAGE=3 analysts to emit an analyst signal.
    """
    analyst_score = 50
    total = analyst_buy + analyst_hold + analyst_sell
    MIN_COVERAGE = 3

    if total >= MIN_COVERAGE:
        buy_pct = analyst_buy / total
        sell_pct = analyst_sell / total

        if buy_pct > 0.80:
            analyst_score += 25
        elif buy_pct > 0.60:
            analyst_score += 15
        elif buy_pct > 0.50:
            analyst_score += 5

        if sell_pct > 0.30:
            analyst_score -= 20
        elif sell_pct > 0.15:
            analyst_score -= 10

        coverage_factor = min(1.0, (total - MIN_COVERAGE) / 22.0 + 0.5)
        analyst_score = int(50 + (analyst_score - 50) * coverage_factor)

    analyst_score = max(0, min(100, analyst_score))

    # Collect the optional signals
    has_social = bool(social_sentiment and "sentiment_score" in social_sentiment)
    has_news = bool(news_sentiment and "score" in news_sentiment
                    and (news_sentiment.get("used_count") or 0) > 0)

    # Weighting: start with analyst at 100% and redistribute if other signals exist
    w_analyst, w_social, w_news = 1.0, 0.0, 0.0
    if has_social and has_news:
        w_analyst, w_social, w_news = 0.55, 0.25, 0.20
    elif has_social:
        w_analyst, w_social = 0.70, 0.30
    elif has_news:
        w_analyst, w_news = 0.80, 0.20

    blended = analyst_score * w_analyst
    if has_social:
        blended += int(social_sentiment["sentiment_score"]) * w_social
    if has_news:
        blended += int(news_sentiment["score"]) * w_news

    return max(0, min(100, int(round(blended))))


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
        elif asset_type == "crypto":
            # Crypto thrives on cheap money + risk-on sentiment
            if fed_rate < 3.0:
                signals.append(("bullish", 0.7))  # very favorable
            elif fed_rate > 5.5:
                signals.append(("bearish", 0.7))  # very unfavorable
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

def _crypto_valuation_score(technicals: dict, news: list, macro: dict) -> int:
    """Crypto doesn't have earnings. Use momentum + market context as proxy."""
    score = 50
    # Crypto is a risk-on asset — favors low VIX, low rates
    vix = macro.get("vix")
    if vix is not None:
        if vix < 15:
            score += 10
        elif vix > 25:
            score -= 10
    # Price vs MA200 momentum
    rsi = technicals.get("rsi14", 50)
    if rsi < 30:
        score += 15  # oversold = opportunity
    elif rsi > 75:
        score -= 15  # overbought = caution
    return max(0, min(100, score))


def _bond_valuation_score(fed_rate: float | None, ten_year: float | None) -> int:
    """Bonds valued by yield environment. Higher yields > 4% = attractive entry."""
    score = 50
    if ten_year is not None:
        if ten_year > 5:
            score += 20  # high yields — lock them in
        elif ten_year > 4:
            score += 10
        elif ten_year < 2:
            score -= 15  # low yields = poor entry
    if fed_rate is not None and ten_year is not None:
        spread = ten_year - fed_rate
        if spread < 0:
            score += 10  # inverted curve = flight to safety = bond tailwind
    return max(0, min(100, score))


def _crypto_quality_score(price: float, ma200: float) -> int:
    """Crypto quality = long-term trend strength (no balance sheet to analyze)."""
    if not ma200 or ma200 == 0:
        return 50
    deviation = (price - ma200) / ma200
    if deviation > 0.10:
        return 70  # solid uptrend
    elif deviation > 0:
        return 60
    elif deviation > -0.10:
        return 50
    elif deviation > -0.25:
        return 40
    else:
        return 30  # far below MA200 = broken trend


def _bond_quality_score(ticker: str) -> int:
    """Government bonds = high quality by default (sovereign credit)."""
    # KSM-F34 = Israeli Gov Bond — high quality
    # F77 also gov, 5108.TA is insurance index (different)
    if "KSM" in ticker or "government" in ticker.lower():
        return 85  # government bonds = very high quality
    return 70  # other fixed income = decent


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
    social_sentiment: dict | None = None,
    insider_activity: dict | None = None,
    smart_money_info: dict | None = None,
    news_sentiment: dict | None = None,
) -> dict[str, int]:
    """Compute all 8 scores for a ticker — asset-class-aware.

    Six analytical factors (stocks / ETFs / crypto / bond branches):
        Stocks/ETFs: P/E, PEG, ROE, margins (classic fundamentals)
        Crypto: momentum + macro (no earnings to analyze)
        Bonds: yield environment + sovereign credit (not P/E-based)

    Two 2026 market-signal factors — skipped for crypto/bonds/TASE where the
    underlying data (SEC Form 4 / 13F) does not apply:
        insider — Form 4 aggregates: cluster buys, exec purchases, net $ flow
        smart_money — 13F deltas from the top-10 watched funds (Berkshire,
            Scion, Pershing, Bridgewater, Renaissance, …)
    """
    price = quote.get("price", 0) or 0
    is_bond = "Fixed Income" in (asset_type or "")
    is_crypto = "Crypto" in (asset_type or "")
    is_tase = ticker.endswith(".TA")

    # Extract sector for valuation comparison
    sector = asset_type or ""

    # Valuation — asset-class-specific
    if is_crypto:
        val_score = _crypto_valuation_score(technicals, news or [], macro_data)
    elif is_bond:
        val_score = _bond_valuation_score(
            macro_data.get("fed_rate"),
            macro_data.get("ten_year_yield"))
    else:
        val_score = valuation_score(fundamentals, price, sector)

    # Quality — asset-class-specific
    if is_crypto:
        qual_score = _crypto_quality_score(price, technicals.get("ma200", 0))
    elif is_bond:
        qual_score = _bond_quality_score(ticker)
    else:
        qual_score = quality_score(fundamentals)

    out = {
        "valuation": val_score,
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
            news,
            social_sentiment,
            news_sentiment=news_sentiment),
        "macro": macro_score(
            macro_data.get("fed_rate"),
            macro_data.get("ten_year_yield"),
            macro_data.get("vix"),
            macro_data.get("cpi_yoy"),
            "bond" if is_bond else "crypto" if is_crypto else "stock"),
        "quality": qual_score,
    }

    # Insider + smart-money only apply to US equities (and US-listed ETFs are
    # tracked too — 13F data includes SPY/VOO/etc.). Skip for crypto/bonds/TASE.
    if not is_crypto and not is_bond and not is_tase:
        try:
            from data_loader_insider import score_insider
            out["insider"] = score_insider(insider_activity)
        except ImportError:
            pass
        try:
            from data_loader_smart_money import score_smart_money
            out["smart_money"] = score_smart_money(smart_money_info)
        except ImportError:
            pass

    return out


DEFAULT_WEIGHTS = {
    # 6 analytical factors (was 100% total, now redistributed to 85%)
    "quality": 25, "valuation": 22, "risk": 18,
    "macro": 12, "sentiment": 4, "technical": 4,
    # 2 market-signal factors added 2026 — free via SEC EDGAR (see
    # data_loader_insider.py + data_loader_smart_money.py)
    "insider": 8, "smart_money": 7,
}


def scores_to_verdict(scores: dict[str, int],
                      weights: dict[str, int] | None = None) -> tuple[str, int]:
    """Convert all available scores to a single verdict + conviction.

    Supports the 6 analytical factors + the 2 market-signal factors added 2026
    (`insider`, `smart_money`). Missing factors simply drop out of the weighted
    average — we require at least 3 of the 6 analytical factors to be present
    for a confident call.
    """
    if not scores or len(scores) < 3:
        # Insufficient score coverage — cannot make a confident call
        return "hold", 30

    w = weights or DEFAULT_WEIGHTS
    total_weight = sum(w.get(k, 0) for k in scores)
    if total_weight == 0:
        total_weight = len(scores)
        w = {k: 1 for k in scores}

    # Weighted average score — this IS the fundamental signal
    weighted_avg = sum(scores[k] * w.get(k, 0) for k in scores) / total_weight

    # Industry-aligned thresholds:
    #   weighted_avg >= 70 → BUY  (Fidelity, Trade-Ideas use ~70+ as BUY line)
    #   weighted_avg >= 80 → Strong BUY (IBD considers 80+ as "good")
    #   weighted_avg <= 30 → SELL
    #   weighted_avg <= 20 → Strong SELL
    #   30-70 → HOLD (most stocks should be HOLD — BUY is a high bar)
    if weighted_avg >= 70:
        verdict = "buy"
        # Conviction tracks signal strength: 70 → 70%, 90 → 90%
        conviction = int(min(100, weighted_avg))
    elif weighted_avg <= 30:
        verdict = "sell"
        # Conviction = how far below neutral: 30 → 70%, 10 → 90%
        conviction = int(min(100, 100 - weighted_avg))
    else:
        verdict = "hold"
        # HOLD conviction is LOW by design — we're not making a strong call
        # Closer to 50 = more confidently neutral
        conviction = int(max(30, 60 - abs(weighted_avg - 50)))

    return verdict, conviction


def score_color(val: int) -> str:
    """Return CSS color for a score value. Aligned with verdict thresholds.

    - Green (BUY zone): ≥70
    - Amber (HOLD zone): 30-69
    - Red (SELL zone): <30
    """
    if val >= 70:
        return "#047857"  # Green = BUY
    elif val >= 30:
        return "#b45309"  # Amber = HOLD
    else:
        return "#b91c1c"  # Red = SELL


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
    insider_activity: dict | None = None,
    smart_money_info: dict | None = None,
) -> dict[str, list[str]]:
    """Generate bullet-point explanations for each score.

    Returns {score_name: [reason1, reason2, ...]}.
    """
    f = fundamentals or {}
    explanations: dict[str, list[str]] = {}

    # ── Insider (delegates to data_loader_insider.explain_insider) ───────
    if "insider" in scores:
        try:
            from data_loader_insider import explain_insider
            explanations["insider"] = explain_insider(insider_activity)
        except ImportError:
            explanations["insider"] = ["insider module unavailable"]

    # ── Smart-money (delegates to data_loader_smart_money.explain_smart_money) ──
    if "smart_money" in scores:
        try:
            from data_loader_smart_money import explain_smart_money
            explanations["smart_money"] = explain_smart_money(smart_money_info)
        except ImportError:
            explanations["smart_money"] = ["smart_money module unavailable"]

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
