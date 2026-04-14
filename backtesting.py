"""
Backtesting — measure how well the scoring engine would have performed historically.

Uses verdict_history.jsonl + price data to compute:
- If you had followed every BUY → what was your average return after 7d, 30d, 90d?
- If you had followed every SELL → did the price actually drop?
- Overall hit rate by verdict type and score category
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def _load_verdict_history() -> list[dict]:
    """Load all verdict history entries."""
    path = _ROOT / "verdict_history.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    return entries


def _fetch_price_on_date(ticker: str, date_str: str) -> float | None:
    """Fetch the closing price for a ticker on a specific date (or nearest)."""
    import requests
    try:
        # Fetch 5-day range around the target date
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"range": "1y", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10, verify=False,
        )
        if resp.status_code != 200:
            return None
        result = resp.json()["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        closes = result["indicators"]["quote"][0].get("close", [])

        import pandas as pd
        dates = pd.to_datetime(timestamps, unit="s").normalize()
        target = pd.Timestamp(date_str)

        # Find nearest date
        for i, d in enumerate(dates):
            if d >= target and closes[i] is not None:
                return closes[i]
        return None
    except Exception:
        return None


def compute_backtest(lookback_days: int = 90) -> dict:
    """Compute backtest results for all verdicts in the last N days.

    Returns:
    {
        "total_verdicts": int,
        "by_verdict": {
            "buy": {"count": N, "avg_return_7d": %, "avg_return_30d": %, "hit_rate_30d": %},
            "sell": {...},
            "hold": {...},
        },
        "overall_hit_rate": float,
        "details": [{"ticker": str, "date": str, "verdict": str, "return_30d": float}, ...]
    }
    """
    history = _load_verdict_history()
    if not history:
        return {"total_verdicts": 0, "by_verdict": {}, "overall_hit_rate": 0, "details": []}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # Group by ticker + date (deduplicate same-day entries)
    seen = set()
    unique = []
    for e in history:
        key = (e.get("ticker", ""), e.get("date", ""))
        if key in seen or e.get("date", "") < cutoff:
            continue
        seen.add(key)
        unique.append(e)

    # For each verdict, check if it was correct by looking at price now vs then
    # (This is simplified — a proper backtest would need historical prices at verdict date)
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    results = {"buy": [], "sell": [], "hold": []}
    details = []

    # Fetch current prices for all tickers
    tickers = list({e["ticker"] for e in unique if not e["ticker"].endswith(".TA")})

    try:
        from data_loader import fetch_live_quotes
        quotes_df = fetch_live_quotes(tickers)
        current_prices = {}
        if not quotes_df.empty:
            for _, row in quotes_df.iterrows():
                current_prices[row["ticker"]] = row.get("price", 0)
    except Exception:
        current_prices = {}

    # For a real backtest, we'd need the price at verdict date
    # For now, use ideas_history.json which has suggested_price
    ideas_path = _ROOT / "ideas_history.json"
    idea_prices = {}
    if ideas_path.exists():
        try:
            ideas = json.loads(ideas_path.read_text())
            idea_prices = {i["ticker"]: i.get("suggested_price", 0) for i in ideas if i.get("suggested_price")}
        except Exception:
            pass

    for e in unique:
        tk = e.get("ticker", "")
        v = e.get("verdict", "hold")
        current = current_prices.get(tk)
        if current is None or current == 0:
            continue

        # Try to find price at verdict date (from ideas or estimated)
        entry_price = idea_prices.get(tk)
        if not entry_price:
            continue  # can't compute return without entry price

        ret = ((current / entry_price) - 1) * 100

        # Is the verdict "correct"?
        correct = (v == "buy" and ret > 0) or (v == "sell" and ret < 0) or (v == "hold" and abs(ret) < 10)

        results[v].append({"ticker": tk, "return": ret, "correct": correct})
        details.append({
            "ticker": tk, "date": e.get("date", ""), "verdict": v,
            "entry_price": entry_price, "current_price": current,
            "return_pct": round(ret, 2), "correct": correct,
        })

    # Aggregate
    by_verdict = {}
    total_correct = 0
    total_count = 0
    for v_type in ["buy", "sell", "hold"]:
        entries = results[v_type]
        if entries:
            avg_ret = sum(e["return"] for e in entries) / len(entries)
            hits = sum(1 for e in entries if e["correct"])
            by_verdict[v_type] = {
                "count": len(entries),
                "avg_return": round(avg_ret, 2),
                "hit_rate": round(hits / len(entries) * 100, 1),
            }
            total_correct += hits
            total_count += len(entries)
        else:
            by_verdict[v_type] = {"count": 0, "avg_return": 0, "hit_rate": 0}

    return {
        "total_verdicts": total_count,
        "by_verdict": by_verdict,
        "overall_hit_rate": round(total_correct / max(1, total_count) * 100, 1),
        "details": sorted(details, key=lambda d: -abs(d.get("return_pct", 0))),
    }
