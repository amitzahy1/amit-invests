"""
Score History — tracks how scores and verdicts change over time.

Appends to scores_history.jsonl on each run.
Enables trend charts and accuracy measurement.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_HISTORY_PATH = _ROOT / "scores_history.jsonl"


def record_scores(recommendations: dict) -> None:
    """Append today's scores for all holdings + ideas to the history file."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries = []

    for h in recommendations.get("holdings", []):
        tk = h.get("ticker", "")
        if not tk:
            continue
        entries.append({
            "date": date,
            "ticker": tk,
            "type": "holding",
            "verdict": h.get("verdict", "hold"),
            "conviction": h.get("conviction", 0),
            "scores": h.get("scores", {}),
        })

    for idea in recommendations.get("new_ideas", []):
        tk = idea.get("ticker", "")
        if not tk:
            continue
        entries.append({
            "date": date,
            "ticker": tk,
            "type": "idea",
            "verdict": "buy",
            "conviction": idea.get("conviction", 0),
            "scores": idea.get("scores", {}),
        })

    if entries:
        with open(_HISTORY_PATH, "a") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")


def load_history(ticker: str | None = None, days: int = 90) -> list[dict]:
    """Load score history, optionally filtered by ticker and time window."""
    if not _HISTORY_PATH.exists():
        return []

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    entries = []
    for line in _HISTORY_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            if e.get("date", "") < cutoff:
                continue
            if ticker and e.get("ticker") != ticker:
                continue
            entries.append(e)
        except Exception:
            continue
    return entries


def get_score_trend(ticker: str, days: int = 30) -> list[dict]:
    """Get score history for a specific ticker, sorted by date."""
    entries = load_history(ticker=ticker, days=days)
    return sorted(entries, key=lambda e: e.get("date", ""))


def get_verdict_changes(days: int = 7) -> list[dict]:
    """Find tickers whose verdict changed in the last N days."""
    history = load_history(days=days)

    # Group by ticker, find first and last verdict
    by_ticker: dict[str, list[dict]] = {}
    for e in history:
        tk = e.get("ticker", "")
        by_ticker.setdefault(tk, []).append(e)

    changes = []
    for tk, entries in by_ticker.items():
        sorted_e = sorted(entries, key=lambda e: e.get("date", ""))
        if len(sorted_e) < 2:
            continue
        first = sorted_e[0]
        last = sorted_e[-1]
        if first["verdict"] != last["verdict"] or abs(first["conviction"] - last["conviction"]) >= 10:
            changes.append({
                "ticker": tk,
                "old_verdict": first["verdict"],
                "new_verdict": last["verdict"],
                "old_conviction": first["conviction"],
                "new_conviction": last["conviction"],
                "old_date": first["date"],
                "new_date": last["date"],
                "old_scores": first.get("scores", {}),
                "new_scores": last.get("scores", {}),
            })
    return changes
