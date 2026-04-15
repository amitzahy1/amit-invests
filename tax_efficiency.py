"""
Tax Efficiency — computes tax implications for each holding.

For the Israeli investor:
- Capital gains tax = 25% flat (no long/short distinction in Israel)
- BUT: if held <1 year, US broker may withhold more; may be offset via form 1116

For US investors (future):
- Short-term (<365 days): taxed at marginal income rate (up to ~37%)
- Long-term (>365 days): 0/15/20% depending on income bracket
- "Wash sale" rule: don't buy same ticker within 30 days of selling at a loss

This module computes:
1. Time held (days since purchase)
2. Unrealized gain (USD + ILS)
3. Tax cost if sold today (Israeli 25% flat)
4. Days until qualifying for long-term treatment (US)
5. Advice: "Wait X days to save ~$Y in taxes" (US)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

ISRAELI_CAPITAL_GAINS_RATE = 0.25  # 25% flat for Israeli residents
US_SHORT_TERM_RATE = 0.32           # marginal (assumed)
US_LONG_TERM_RATE = 0.15            # long-term capital gains (middle bracket)


def compute_tax_info(
    ticker: str,
    quantity: float,
    cost_price: float,
    current_price: float,
    purchase_date: str | None = None,
    jurisdiction: str = "israel",
) -> dict:
    """Compute tax implications for a single holding.

    Args:
        ticker: symbol
        quantity: shares held
        cost_price: avg cost price per share (same currency as current_price)
        current_price: current price per share
        purchase_date: ISO date "YYYY-MM-DD" or None
        jurisdiction: "israel" or "us"

    Returns dict with:
        unrealized_gain, unrealized_gain_pct, days_held, is_long_term,
        tax_cost_if_sold_now, days_until_long_term, potential_savings
    """
    if not cost_price or cost_price <= 0 or not current_price:
        return {}

    # Unrealized gain
    gain_per_share = current_price - cost_price
    total_gain = gain_per_share * quantity
    gain_pct = (gain_per_share / cost_price) * 100

    # Time held
    days_held = None
    is_long_term = False
    days_until_long_term = None
    if purchase_date:
        try:
            pd = datetime.fromisoformat(purchase_date.replace("Z", "+00:00"))
            if pd.tzinfo is None:
                pd = pd.replace(tzinfo=timezone.utc)
            days_held = (datetime.now(timezone.utc) - pd).days
            is_long_term = days_held >= 365
            if not is_long_term:
                days_until_long_term = 365 - days_held
        except Exception:
            pass

    # Tax cost if sold now
    if total_gain <= 0:
        # Loss — no tax, potential loss harvest opportunity
        tax_cost_if_sold_now = 0
        tax_rate_current = 0
    elif jurisdiction == "us":
        tax_rate_current = US_LONG_TERM_RATE if is_long_term else US_SHORT_TERM_RATE
        tax_cost_if_sold_now = total_gain * tax_rate_current
    else:  # Israel — flat 25%
        tax_rate_current = ISRAELI_CAPITAL_GAINS_RATE
        tax_cost_if_sold_now = total_gain * tax_rate_current

    # Potential savings if waiting (US only)
    potential_savings = None
    if jurisdiction == "us" and total_gain > 0 and not is_long_term and days_until_long_term:
        tax_if_sold_now = total_gain * US_SHORT_TERM_RATE
        tax_if_sold_lt = total_gain * US_LONG_TERM_RATE
        potential_savings = tax_if_sold_now - tax_if_sold_lt

    # Advice string
    advice = ""
    if total_gain < 0:
        advice = "Unrealized loss — potential tax-loss harvesting opportunity"
    elif jurisdiction == "israel":
        advice = f"If sold: {tax_cost_if_sold_now:,.0f} tax (25% flat). "
        if gain_pct > 30:
            advice += "Significant gain — consider the tax hit."
    elif is_long_term:
        advice = "Long-term holding — qualifies for lower tax rate."
    elif days_until_long_term and days_until_long_term < 60:
        advice = (f"⏳ {days_until_long_term} days until long-term. "
                  f"Waiting saves ~${potential_savings:,.0f} in taxes.")
    elif days_until_long_term:
        advice = (f"Short-term holding ({days_held} days). "
                  f"{days_until_long_term} days until long-term rate.")

    return {
        "unrealized_gain": round(total_gain, 2),
        "unrealized_gain_pct": round(gain_pct, 2),
        "days_held": days_held,
        "is_long_term": is_long_term,
        "days_until_long_term": days_until_long_term,
        "tax_cost_if_sold_now": round(tax_cost_if_sold_now, 2),
        "tax_rate_current": tax_rate_current,
        "potential_savings": round(potential_savings, 2) if potential_savings else None,
        "advice": advice,
        "jurisdiction": jurisdiction,
    }


def get_portfolio_tax_summary(portfolio: dict, live_prices: dict,
                               jurisdiction: str = "israel") -> dict:
    """Aggregate tax info across the entire portfolio."""
    total_gain = 0
    total_tax = 0
    total_loss = 0
    short_term_positions = 0
    long_term_positions = 0
    harvest_opportunities = []

    for h in portfolio.get("holdings", []):
        tk = h.get("ticker", "")
        qty = h.get("quantity", 0)
        cost = h.get("cost_price_usd") or h.get("cost_price_ils", 0)
        price = live_prices.get(tk, 0)
        if not cost or not price:
            continue

        info = compute_tax_info(
            tk, qty, cost, price,
            purchase_date=h.get("purchase_date"),
            jurisdiction=jurisdiction,
        )
        if not info:
            continue

        gain = info.get("unrealized_gain", 0)
        if gain > 0:
            total_gain += gain
            total_tax += info.get("tax_cost_if_sold_now", 0)
            if info.get("is_long_term"):
                long_term_positions += 1
            else:
                short_term_positions += 1
        elif gain < 0:
            total_loss += abs(gain)
            harvest_opportunities.append({
                "ticker": tk,
                "loss": round(gain, 2),
                "qty": qty,
            })

    return {
        "total_unrealized_gain": round(total_gain, 2),
        "total_unrealized_loss": round(total_loss, 2),
        "total_tax_if_sold_all": round(total_tax, 2),
        "short_term_count": short_term_positions,
        "long_term_count": long_term_positions,
        "loss_harvest_candidates": harvest_opportunities,
        "jurisdiction": jurisdiction,
    }
