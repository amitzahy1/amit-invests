"""
Fundamental data fetcher — Alpha Vantage OVERVIEW endpoint + Google News RSS.

Free tier: 25 calls/minute, 500/day.  We cache results in fundamentals_cache.json
(refresh if >24 h old) so a daily run only hits the API once per ticker.
"""

from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

# Suppress SSL warnings when behind corporate proxy
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ROOT = Path(__file__).resolve().parent
# Corporate proxy may inject self-signed certs — disable verification
_VERIFY_SSL = not os.environ.get("REQUESTS_CA_BUNDLE_DISABLED", "")
_FUND_CACHE = _ROOT / "fundamentals_cache.json"
_NEWS_CACHE = _ROOT / "news_cache.json"

_AV_BASE = "https://www.alphavantage.co/query"
_AV_TIMEOUT = 12  # seconds


# ── Fundamentals (Alpha Vantage) ─────────────────────────────────────────────

def _av_key() -> str | None:
    return os.environ.get("ALPHA_VANTAGE_API_KEY")


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_cache(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_fundamentals_cache() -> dict:
    """Load the cached fundamentals from disk (for use by other modules)."""
    return _load_cache(_FUND_CACHE)


def _cache_is_fresh(cache: dict, max_age_hours: float = 24) -> bool:
    ts = cache.get("updated")
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
        return age < max_age_hours * 3600
    except Exception:
        return False


def fetch_fundamentals(ticker: str) -> dict | None:
    """Fetch key fundamentals from Alpha Vantage OVERVIEW endpoint.

    Returns a flat dict with normalised numeric fields, or None on failure.
    Israeli tickers (*.TA) are skipped — AV doesn't cover TASE.
    """
    if ticker.endswith(".TA"):
        return None
    key = _av_key()
    if not key:
        return None
    try:
        resp = requests.get(_AV_BASE, params={
            "function": "OVERVIEW",
            "symbol": ticker,
            "apikey": key,
        }, timeout=_AV_TIMEOUT, verify=False)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if "Symbol" not in data:
            return None
    except Exception:
        return None

    def _float(k: str) -> float | None:
        v = data.get(k)
        if v in (None, "None", "-", ""):
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    def _int(k: str) -> int:
        v = data.get(k)
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0

    return {
        "pe": _float("PERatio"),
        "peg": _float("PEGRatio"),
        "eps": _float("EPS"),
        "revenue_per_share": _float("RevenuePerShareTTM"),
        "profit_margin": _pct(_float("ProfitMargin")),
        "roe": _pct(_float("ReturnOnEquityTTM")),
        "debt_equity": _float("DebtToEquityRatio"),  # AV returns as ratio * 100 sometimes
        "dividend_yield": _pct(_float("DividendYield")),
        "market_cap": _float("MarketCapitalization"),
        "beta": _float("Beta"),
        "analyst_target": _float("AnalystTargetPrice"),
        "analyst_buy": _int("AnalystRatingStrongBuy") + _int("AnalystRatingBuy"),
        "analyst_hold": _int("AnalystRatingHold"),
        "analyst_sell": _int("AnalystRatingSell") + _int("AnalystRatingStrongSell"),
        "price": _float("50DayMovingAverage"),  # approximate current price
        "sector": data.get("Sector", ""),
    }


def _pct(v: float | None) -> float | None:
    """Convert 0.xx ratio to percentage if needed.

    Alpha Vantage sometimes returns 0.15 (ratio) and sometimes 15.0 (%).
    Heuristic: if abs value is < 1 and non-zero, treat as ratio and multiply by 100.
    Values exactly at |1.0| are treated as already-a-percentage (ambiguous but safer
    since real margins of exactly 1.0/-1.0 are rare, while 1%/100% both appear).
    """
    if v is None:
        return None
    # Use <= and strict zero check
    if v == 0:
        return 0.0
    if abs(v) < 1:
        return round(v * 100, 2)
    return round(v, 2)


def fetch_all_fundamentals(tickers: list[str]) -> dict[str, dict]:
    """Fetch fundamentals for all tickers with caching + rate limiting.

    Returns {ticker: fundamentals_dict}.  Cached results are reused if < 24 h old.
    """
    cache = _load_cache(_FUND_CACHE)
    tickers_data = cache.get("tickers", {})
    is_fresh = _cache_is_fresh(cache, max_age_hours=24)

    result: dict[str, dict] = {}
    need_fetch: list[str] = []

    for tk in tickers:
        if tk.endswith(".TA"):
            continue  # skip Israeli tickers
        if is_fresh and tk in tickers_data:
            result[tk] = tickers_data[tk]
        else:
            need_fetch.append(tk)

    if not need_fetch:
        return result

    if not _av_key():
        print("[warn] ALPHA_VANTAGE_API_KEY not set — skipping fundamentals",
              flush=True)
        return result

    print(f"[info] fetching fundamentals for {len(need_fetch)} tickers from Alpha Vantage…",
          flush=True)
    for i, tk in enumerate(need_fetch):
        if i > 0:
            time.sleep(2.5)  # stay under 25 calls/min
        data = fetch_fundamentals(tk)
        if data:
            result[tk] = data
            tickers_data[tk] = data
        else:
            print(f"  [warn] no fundamentals for {tk}", flush=True)

    # Update cache
    cache["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
    cache["tickers"] = tickers_data
    _save_cache(_FUND_CACHE, cache)
    print(f"[ok] fundamentals cached for {len(tickers_data)} tickers", flush=True)
    return result


# ── News Headlines (Google News RSS — no API key needed) ─────────────────────

_NEWS_RSS = "https://news.google.com/rss/search"


def fetch_news_headlines(ticker: str, max_items: int = 5) -> list[str]:
    """Fetch recent headlines from Google News RSS for a ticker."""
    if ticker.endswith(".TA"):
        return []
    try:
        resp = requests.get(_NEWS_RSS, params={
            "q": f"{ticker} stock",
            "hl": "en",
            "gl": "US",
            "ceid": "US:en",
        }, timeout=8, verify=False)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        items = root.findall(".//item/title")
        return [item.text for item in items[:max_items] if item.text]
    except Exception:
        return []


def fetch_all_news(tickers: list[str], max_items: int = 3) -> dict[str, list[str]]:
    """Fetch news for all tickers with caching (4 h TTL)."""
    cache = _load_cache(_NEWS_CACHE)
    is_fresh = _cache_is_fresh(cache, max_age_hours=4)
    cached_tickers = cache.get("tickers", {})

    result: dict[str, list[str]] = {}
    need_fetch: list[str] = []

    for tk in tickers:
        if tk.endswith(".TA"):
            continue
        if is_fresh and tk in cached_tickers:
            result[tk] = cached_tickers[tk]
        else:
            need_fetch.append(tk)

    if not need_fetch:
        return result

    print(f"[info] fetching news for {len(need_fetch)} tickers…", flush=True)
    for tk in need_fetch:
        headlines = fetch_news_headlines(tk, max_items=max_items)
        result[tk] = headlines
        cached_tickers[tk] = headlines
        time.sleep(0.5)  # polite

    cache["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
    cache["tickers"] = cached_tickers
    _save_cache(_NEWS_CACHE, cache)
    return result
