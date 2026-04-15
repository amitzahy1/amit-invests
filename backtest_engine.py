"""
Backtest Engine — measures how accurate past recommendations were.

For each verdict in verdict_history.jsonl:
  1. Fetch the price on the verdict date (from Yahoo Finance historical)
  2. Fetch price 7/30/90 days later (or current if less time elapsed)
  3. Determine if the verdict was "correct":
       BUY → price went up (even if <20% gain, still "correct")
       SELL → price went down (or flat)
       HOLD → price stayed within +/-10%
  4. Aggregate into hit rates, avg returns, calibration metrics

Cached to backtest_cache.json for performance (refreshed daily).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_VERDICT_HISTORY = _ROOT / "verdict_history.jsonl"
_SCORES_HISTORY = _ROOT / "scores_history.jsonl"
_CACHE_PATH = _ROOT / "backtest_cache.json"


def _load_verdict_history(days: int = 180) -> list[dict]:
    """Load verdict history entries from the last N days."""
    if not _VERDICT_HISTORY.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    entries = []
    for line in _VERDICT_HISTORY.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            if e.get("date", "") >= cutoff:
                entries.append(e)
        except Exception:
            continue
    return entries


def _deduplicate_verdicts(history: list[dict]) -> list[dict]:
    """Keep only the FIRST verdict per (ticker, date) pair."""
    seen = set()
    unique = []
    for e in history:
        key = (e.get("ticker", ""), e.get("date", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    return unique


def _fetch_historical_prices(tickers: list[str],
                              lookback_days: int = 200) -> dict:
    """Fetch 1-year OHLCV for a batch of tickers. Returns {ticker: DataFrame}."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(_ROOT))
        from data_loader import fetch_historical_data
        return fetch_historical_data(tickers, period="1y")
    except Exception as e:
        print(f"[warn] historical fetch failed: {e}")
        return {}


def _price_on_date(hist_df, target_date: str):
    """Get the closing price on (or nearest to) target_date."""
    if hist_df is None or len(hist_df) == 0:
        return None
    import pandas as pd
    try:
        target = pd.Timestamp(target_date).tz_localize(None)
        # Ensure index has no tz
        if hist_df.index.tz is not None:
            hist_df = hist_df.copy()
            hist_df.index = hist_df.index.tz_localize(None)
        # Find the first row >= target date
        mask = hist_df.index >= target
        if mask.any():
            return float(hist_df.loc[mask, "close"].iloc[0])
        # Fallback to latest if date is in future
        return float(hist_df["close"].iloc[-1])
    except Exception:
        return None


def _classify_verdict(verdict: str, return_pct: float) -> str:
    """Determine if a verdict was correct given the actual return.

    Rules:
      BUY correct if return > 0 (any gain counts)
      SELL correct if return < 0 (or flat within -2%)
      HOLD correct if return between -10% and +10% (no strong signal)
    """
    v = (verdict or "").lower()
    if v == "buy":
        return "correct" if return_pct > 0 else "wrong"
    elif v == "sell":
        return "correct" if return_pct < 0 else "wrong"
    elif v == "hold":
        return "correct" if -10 <= return_pct <= 10 else "wrong"
    return "unknown"


def compute_backtest(days_elapsed: int = 30) -> dict:
    """Run a full backtest on verdict history.

    Args:
        days_elapsed: How many days after each verdict to measure returns.
                      Default 30 (measure 1-month returns).

    Returns a dict with:
      - total_verdicts: int
      - correct / wrong counts
      - hit_rate: float 0-1
      - by_verdict: {buy: {...}, sell: {...}, hold: {...}}
      - by_conviction_bucket: calibration data
      - avg_returns: mean/median returns per verdict type
      - details: sorted list of individual results
    """
    history = _load_verdict_history(days=180)
    if not history:
        return {"total": 0, "hit_rate": 0, "details": [], "status": "no_data"}

    unique = _deduplicate_verdicts(history)

    # We can only backtest verdicts where enough time has passed
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_elapsed))
    eligible = []
    for e in unique:
        try:
            d = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc)
            if d <= cutoff:
                eligible.append(e)
        except Exception:
            continue

    if not eligible:
        # Too early — use whatever elapsed time we have (min 3 days)
        min_cutoff = datetime.now(timezone.utc) - timedelta(days=3)
        for e in unique:
            try:
                d = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc)
                if d <= min_cutoff:
                    eligible.append(e)
            except Exception:
                continue

    if not eligible:
        return {"total": 0, "hit_rate": 0, "details": [],
                "status": "insufficient_history"}

    # Fetch historical prices for all tickers
    tickers = list({e["ticker"] for e in eligible if e.get("ticker")})
    hist = _fetch_historical_prices(tickers)

    details = []
    for e in eligible:
        tk = e["ticker"]
        verdict_date = e["date"]
        verdict = e.get("verdict", "hold")
        conviction = e.get("conviction", 50)

        hist_df = hist.get(tk)
        if hist_df is None:
            continue

        entry_price = _price_on_date(hist_df, verdict_date)
        if not entry_price or entry_price <= 0:
            continue

        # "Exit" price = latest available (proxy for current)
        try:
            current_price = float(hist_df["close"].iloc[-1])
        except Exception:
            continue

        return_pct = ((current_price / entry_price) - 1) * 100
        outcome = _classify_verdict(verdict, return_pct)

        details.append({
            "ticker": tk,
            "date": verdict_date,
            "verdict": verdict,
            "conviction": conviction,
            "entry_price": round(entry_price, 2),
            "current_price": round(current_price, 2),
            "return_pct": round(return_pct, 2),
            "outcome": outcome,
        })

    # Aggregate
    total = len(details)
    correct = sum(1 for d in details if d["outcome"] == "correct")
    hit_rate = correct / max(1, total)

    by_verdict = {}
    for v in ("buy", "hold", "sell"):
        subset = [d for d in details if d["verdict"] == v]
        if subset:
            c = sum(1 for d in subset if d["outcome"] == "correct")
            avg_ret = sum(d["return_pct"] for d in subset) / len(subset)
            by_verdict[v] = {
                "count": len(subset),
                "correct": c,
                "hit_rate": round(c / len(subset) * 100, 1),
                "avg_return_pct": round(avg_ret, 2),
            }
        else:
            by_verdict[v] = {"count": 0, "correct": 0, "hit_rate": 0, "avg_return_pct": 0}

    # Calibration: hit rate by conviction bucket
    buckets = {"50-60": [], "60-70": [], "70-80": [], "80+": []}
    for d in details:
        c = d["conviction"]
        if c >= 80:
            bucket = "80+"
        elif c >= 70:
            bucket = "70-80"
        elif c >= 60:
            bucket = "60-70"
        elif c >= 50:
            bucket = "50-60"
        else:
            continue
        buckets[bucket].append(d)

    calibration = {}
    for b, items in buckets.items():
        if items:
            c = sum(1 for d in items if d["outcome"] == "correct")
            calibration[b] = {
                "count": len(items),
                "hit_rate": round(c / len(items) * 100, 1),
                "avg_return_pct": round(sum(d["return_pct"] for d in items) / len(items), 2),
            }
        else:
            calibration[b] = {"count": 0, "hit_rate": 0, "avg_return_pct": 0}

    # BUY portfolio return (if user followed every BUY)
    buys = [d for d in details if d["verdict"] == "buy"]
    buy_return = (sum(d["return_pct"] for d in buys) / len(buys)) if buys else 0

    # vs SPY (if tracked)
    spy_return = None
    if "SPY" in hist and hist["SPY"] is not None and len(hist["SPY"]) > 0:
        try:
            spy_entry = float(hist["SPY"]["close"].iloc[0])
            spy_current = float(hist["SPY"]["close"].iloc[-1])
            spy_return = ((spy_current / spy_entry) - 1) * 100
        except Exception:
            pass

    alpha_vs_spy = (buy_return - spy_return) if spy_return is not None else None

    return {
        "status": "ok",
        "total": total,
        "correct": correct,
        "hit_rate": round(hit_rate * 100, 1),
        "by_verdict": by_verdict,
        "calibration": calibration,
        "buy_portfolio_avg_return_pct": round(buy_return, 2),
        "spy_return_pct": round(spy_return, 2) if spy_return is not None else None,
        "alpha_vs_spy_pct": round(alpha_vs_spy, 2) if alpha_vs_spy is not None else None,
        "days_elapsed_measured": days_elapsed,
        "details": sorted(details, key=lambda d: -abs(d.get("return_pct", 0))),
    }


def save_backtest_cache(result: dict) -> None:
    """Cache backtest results to disk."""
    result["cached_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
    _CACHE_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))


def load_backtest_cache() -> dict:
    """Load cached backtest results."""
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}


def is_cache_fresh(max_age_hours: int = 12) -> bool:
    """Check if the cache is still fresh."""
    cache = load_backtest_cache()
    ts = cache.get("cached_at")
    if not ts:
        return False
    try:
        cached = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - cached).total_seconds() < max_age_hours * 3600
    except Exception:
        return False


def get_or_compute_backtest(days_elapsed: int = 30) -> dict:
    """Return cached backtest if fresh, else recompute."""
    if is_cache_fresh():
        cached = load_backtest_cache()
        if cached.get("status") == "ok":
            return cached
    result = compute_backtest(days_elapsed=days_elapsed)
    if result.get("status") == "ok":
        save_backtest_cache(result)
    return result
