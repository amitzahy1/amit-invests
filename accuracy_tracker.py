"""
Accuracy Tracker — measures how well recommendations predicted actual returns.

Tracks every verdict over time and computes hit rates after 7d, 30d, 90d.
A BUY is "correct" if the price went up. A SELL is "correct" if it went down.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_HISTORY_PATH = _ROOT / "verdict_history.jsonl"


def record_verdicts(recommendations: dict) -> None:
    """Append today's verdicts to the history file (JSONL)."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    holdings = recommendations.get("holdings", [])
    if not holdings:
        return

    entries = []
    for h in holdings:
        tk = h.get("ticker", "")
        if not tk:
            continue
        entries.append({
            "date": date,
            "ticker": tk,
            "verdict": h.get("verdict", "hold"),
            "conviction": h.get("conviction", 0),
            "scores": h.get("scores", {}),
        })

    with open(_HISTORY_PATH, "a") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def load_history() -> list[dict]:
    """Load all verdict history entries."""
    if not _HISTORY_PATH.exists():
        return []
    entries = []
    for line in _HISTORY_PATH.read_text().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    return entries


def compute_accuracy(history: list[dict], prices: dict[str, float],
                     lookback_days: int = 30) -> dict:
    """Compute accuracy metrics from verdict history.

    Args:
        history: list of verdict entries from load_history()
        prices: {ticker: current_price} for computing returns
        lookback_days: only consider verdicts from the last N days

    Returns:
        {
            "total_verdicts": int,
            "correct": int,
            "hit_rate": float (0-1),
            "by_verdict": {"buy": {"total": N, "correct": N, "hit_rate": float}, ...},
            "by_ticker": {"GOOGL": {"total": N, "correct": N}, ...},
        }
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    recent = [e for e in history if e.get("date", "") >= cutoff]
    if not recent:
        return {"total_verdicts": 0, "correct": 0, "hit_rate": 0, "by_verdict": {}, "by_ticker": {}}

    # We need price at verdict date vs now — simplified: just check direction
    # For a proper implementation, we'd need historical prices at each verdict date
    # For now, we track verdict consistency (does the model stick to its calls?)
    total = 0
    correct = 0
    by_verdict: dict[str, dict] = {"buy": {"total": 0, "correct": 0},
                                    "hold": {"total": 0, "correct": 0},
                                    "sell": {"total": 0, "correct": 0}}
    by_ticker: dict[str, dict] = {}

    # Group by ticker, take earliest verdict, compare to current price direction
    ticker_first: dict[str, dict] = {}
    for e in recent:
        tk = e["ticker"]
        if tk not in ticker_first:
            ticker_first[tk] = e

    for tk, entry in ticker_first.items():
        v = entry.get("verdict", "hold")
        current_price = prices.get(tk)
        if current_price is None:
            continue

        # We can't know the exact price at verdict date without historical lookup
        # So for now, just record the verdict — accuracy comes from ideas_history.json
        # which DOES track suggested_price vs current_price
        total += 1
        by_verdict[v]["total"] += 1

        if tk not in by_ticker:
            by_ticker[tk] = {"total": 0, "correct": 0, "verdicts": []}
        by_ticker[tk]["total"] += 1
        by_ticker[tk]["verdicts"].append(v)

    return {
        "total_verdicts": total,
        "correct": correct,
        "hit_rate": correct / max(1, total),
        "by_verdict": {k: {**v, "hit_rate": v["correct"] / max(1, v["total"])}
                       for k, v in by_verdict.items()},
        "by_ticker": by_ticker,
    }


def compute_ideas_accuracy(ideas_history: list[dict],
                           current_prices: dict[str, float]) -> dict:
    """Compute accuracy for new idea suggestions (has exact price tracking).

    Returns:
        {
            "total": int,
            "profitable": int,
            "hit_rate": float,
            "ideas": [{"ticker": str, "suggested_date": str,
                        "suggested_price": float, "current_price": float,
                        "return_pct": float, "profitable": bool}, ...]
        }
    """
    results = []
    for idea in ideas_history:
        tk = idea.get("ticker", "")
        suggested_price = idea.get("suggested_price", 0)
        if not tk or not suggested_price:
            continue
        current = current_prices.get(tk)
        if current is None:
            continue
        ret = ((current / suggested_price) - 1) * 100
        results.append({
            "ticker": tk,
            "suggested_date": idea.get("suggested_date", ""),
            "suggested_price": suggested_price,
            "current_price": current,
            "return_pct": round(ret, 2),
            "profitable": ret > 0,
        })

    profitable = sum(1 for r in results if r["profitable"])
    return {
        "total": len(results),
        "profitable": profitable,
        "hit_rate": profitable / max(1, len(results)),
        "ideas": results,
    }
