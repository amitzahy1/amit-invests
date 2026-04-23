"""Portfolio optimizer — Hierarchical Risk Parity (HRP) + CVaR-minimization.

Uses Riskfolio-Lib to produce robust rebalance suggestions that the mean-variance
optimizer can't match:

    - **HRP (Lopez de Prado, 2016)**: clusters assets by correlation, allocates
      risk hierarchically → no covariance inversion, stable out-of-sample.
    - **CVaR-min**: minimizes the expected loss in the worst 5% of scenarios,
      rather than variance (symmetric). Better matches investor intuition for
      "how much can I lose".

The output is a suggested weight per ticker. We compare vs. the current
portfolio weights and produce a "suggested delta" view.

Free, pure-Python, CPU-only. Price history comes from yfinance + pymaya.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd

_ROOT = Path(__file__).resolve().parent


def _fetch_returns_matrix(tickers: list[str],
                          period: str = "1y") -> Optional[pd.DataFrame]:
    """Build a (T, N) daily-return matrix for the given tickers.

    Uses the project's existing data loader so TASE tickers flow through pymaya
    while US equities go to Yahoo.
    """
    import sys
    sys.path.insert(0, str(_ROOT))
    try:
        from data_loader import fetch_historical_data
    except ImportError:
        return None

    histories = fetch_historical_data(tickers, period=period)
    # histories is {ticker: DataFrame(columns=[open, high, low, close, ...])}
    closes = {}
    for tk, df in histories.items():
        if df is None or df.empty:
            continue
        if "close" not in df.columns:
            continue
        closes[tk] = df["close"].astype(float)

    if not closes:
        return None

    # Align on common index, forward-fill (holidays differ across markets)
    px = pd.DataFrame(closes).sort_index()
    px = px.ffill().dropna(how="all")
    if len(px) < 30:
        return None
    # Drop columns that are still mostly NaN after ffill
    px = px.dropna(axis=1, thresh=int(len(px) * 0.6))
    returns = px.pct_change().dropna()
    return returns if not returns.empty else None


def compute_hrp_weights(tickers: list[str]) -> Optional[dict[str, float]]:
    """Return {ticker: target_weight} using Hierarchical Risk Parity.

    Returns None if Riskfolio-Lib is unavailable or the return matrix fails
    (e.g. yfinance rate-limited).
    """
    try:
        import riskfolio as rp
    except ImportError:
        return None

    returns = _fetch_returns_matrix(tickers)
    if returns is None or returns.shape[1] < 2:
        return None

    port = rp.HCPortfolio(returns=returns)
    try:
        w = port.optimization(
            model="HRP",               # Hierarchical Risk Parity
            codependence="pearson",
            rm="MV",                   # risk measure for leaf allocation
            rf=0.0,
            linkage="single",
            leaf_order=True,
        )
    except Exception as e:
        print(f"[warn] HRP optimization failed: {e}", flush=True)
        return None

    # w is a DataFrame with index=ticker, column='weights'
    weights = {idx: float(w.loc[idx].iloc[0]) for idx in w.index}
    return weights


def compute_cvar_min_weights(tickers: list[str],
                             alpha: float = 0.05) -> Optional[dict[str, float]]:
    """CVaR-minimization at the given alpha. Returns {ticker: weight} or None."""
    try:
        import riskfolio as rp
    except ImportError:
        return None

    returns = _fetch_returns_matrix(tickers)
    if returns is None or returns.shape[1] < 2:
        return None

    port = rp.Portfolio(returns=returns)
    try:
        port.assets_stats(method_mu="hist", method_cov="hist")
        w = port.optimization(
            model="Classic",
            rm="CVaR",
            obj="MinRisk",
            rf=0.0,
            l=0.0,
            hist=True,
            alpha=alpha,
        )
    except Exception as e:
        print(f"[warn] CVaR-min optimization failed: {e}", flush=True)
        return None

    if w is None or w.empty:
        return None
    weights = {idx: float(w.loc[idx].iloc[0]) for idx in w.index}
    return weights


def compare_to_current(target_weights: dict[str, float],
                       current_weights: dict[str, float]) -> list[dict]:
    """Build a sorted delta table: {ticker, current_pct, target_pct, delta_pct}."""
    all_tickers = set(target_weights) | set(current_weights)
    rows = []
    for tk in all_tickers:
        cur = current_weights.get(tk, 0) * 100
        tgt = target_weights.get(tk, 0) * 100
        rows.append({
            "ticker": tk,
            "current_pct": round(cur, 2),
            "target_pct": round(tgt, 2),
            "delta_pct": round(tgt - cur, 2),
            "action": ("add" if tgt - cur > 1 else
                       "trim" if tgt - cur < -1 else "hold"),
        })
    rows.sort(key=lambda r: -abs(r["delta_pct"]))
    return rows


def _load_portfolio_weights() -> dict[str, float]:
    """Read current holdings from portfolio.json and compute weights using live prices."""
    p = _ROOT / "portfolio.json"
    if not p.exists():
        return {}
    try:
        portfolio = json.loads(p.read_text())
    except Exception:
        return {}
    holdings = portfolio.get("holdings", [])
    if not holdings:
        return {}

    # Fetch live prices so weights reflect current market value, not cost basis
    import sys
    sys.path.insert(0, str(_ROOT))
    try:
        from data_loader import fetch_live_quotes
        tickers = [h["ticker"] for h in holdings if h.get("ticker")]
        quotes_df = fetch_live_quotes(tickers)
        prices = {row.get("ticker", ""): row.get("price", 0) or 0
                  for _, row in quotes_df.iterrows()}
    except Exception:
        prices = {}

    values: dict[str, float] = {}
    for h in holdings:
        tk = h.get("ticker")
        qty = h.get("quantity", 0) or 0
        px = prices.get(tk) or h.get("cost_price_usd", 0) or 0  # fallback to cost
        if tk and qty > 0 and px > 0:
            values[tk] = qty * px

    total = sum(values.values())
    if total <= 0:
        return {}
    return {tk: v / total for tk, v in values.items()}


def build_rebalance_summary(mode: str = "HRP") -> dict:
    """Full rebalance pipeline for the Streamlit UI.

    mode: "HRP" or "CVaR"
    Returns {mode, current_weights, target_weights, deltas, notes}.
    """
    current = _load_portfolio_weights()
    if not current:
        return {"mode": mode, "error": "No portfolio data found"}

    tickers = sorted(current.keys())
    if mode.upper() == "CVAR":
        target = compute_cvar_min_weights(tickers)
        label = "CVaR-Min (5%)"
    else:
        target = compute_hrp_weights(tickers)
        label = "Hierarchical Risk Parity"

    if not target:
        return {"mode": label,
                "error": "Optimization failed — likely rate-limited price data"}

    deltas = compare_to_current(target, current)
    return {
        "mode": label,
        "current_weights": {tk: round(w * 100, 2) for tk, w in current.items()},
        "target_weights": {tk: round(w * 100, 2) for tk, w in target.items()},
        "deltas": deltas,
    }
