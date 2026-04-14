"""
Earnings Calendar — fetches upcoming earnings dates from Alpha Vantage.

Caches results in earnings_cache.json (refresh weekly).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ROOT = Path(__file__).resolve().parent
_CACHE_PATH = _ROOT / "earnings_cache.json"


def _av_key() -> str | None:
    return os.environ.get("ALPHA_VANTAGE_API_KEY")


def fetch_earnings_date(ticker: str) -> dict | None:
    """Fetch next earnings date for a ticker from Alpha Vantage EARNINGS endpoint."""
    if ticker.endswith(".TA"):
        return None
    key = _av_key()
    if not key:
        return None
    try:
        resp = requests.get("https://www.alphavantage.co/query", params={
            "function": "EARNINGS",
            "symbol": ticker,
            "apikey": key,
        }, timeout=12, verify=False)
        if resp.status_code != 200:
            return None
        data = resp.json()
        quarterly = data.get("quarterlyEarnings", [])
        if not quarterly:
            return None
        latest = quarterly[0]
        return {
            "ticker": ticker,
            "report_date": latest.get("reportedDate", ""),
            "estimated_eps": latest.get("estimatedEPS", ""),
            "reported_eps": latest.get("reportedEPS", ""),
            "surprise_pct": latest.get("surprisePercentage", ""),
        }
    except Exception:
        return None


def fetch_all_earnings(tickers: list[str]) -> list[dict]:
    """Fetch earnings data for all tickers with caching (7-day TTL)."""
    cache = {}
    if _CACHE_PATH.exists():
        try:
            cache = json.loads(_CACHE_PATH.read_text())
        except Exception:
            pass

    # Check cache freshness (7 days)
    ts = cache.get("updated", "")
    is_fresh = False
    if ts:
        try:
            age = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
            is_fresh = age < 7 * 24 * 3600
        except Exception:
            pass

    if is_fresh and "tickers" in cache:
        return list(cache["tickers"].values())

    if not _av_key():
        return list(cache.get("tickers", {}).values())

    import time
    results = {}
    for tk in tickers:
        if tk.endswith(".TA"):
            continue
        data = fetch_earnings_date(tk)
        if data:
            results[tk] = data
        time.sleep(2.5)  # rate limit

    cache = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        "tickers": results,
    }
    _CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    return list(results.values())


def get_upcoming_earnings(tickers: list[str], days_ahead: int = 30) -> list[dict]:
    """Get earnings events happening in the next N days."""
    from datetime import timedelta
    all_earnings = fetch_all_earnings(tickers)
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    upcoming = [
        e for e in all_earnings
        if e.get("report_date", "") >= today and e.get("report_date", "") <= cutoff
    ]
    return sorted(upcoming, key=lambda e: e.get("report_date", ""))
