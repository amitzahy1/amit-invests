"""
Fundamental data fetcher — yfinance (primary) + Alpha Vantage OVERVIEW (supplement)
+ Google News RSS.

yfinance covers ETFs, ADRs, crypto ETFs, and international tickers that Alpha
Vantage's OVERVIEW endpoint returns empty for. We still hit AV for US stocks to
pick up fields yfinance sometimes lacks (EPS, RevenuePerShareTTM, DebtToEquityRatio).

We cache results in fundamentals_cache.json (refresh if >24 h old) so a daily run
only hits the APIs once per ticker.
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


# ── Fundamentals (yfinance — works for ETFs and international too) ──────────

def fetch_fundamentals_yfinance(ticker: str) -> dict | None:
    """Fetch key fundamentals via yfinance (unofficial Yahoo Finance client).

    Works for ETFs, ADRs, crypto ETFs, and most international tickers. Returns
    None if the ticker is unknown to Yahoo (e.g. some TASE symbols).
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        return None
    if not info or not info.get("regularMarketPrice"):
        return None

    def _get(*keys):
        for k in keys:
            v = info.get(k)
            if v is not None and v != "None":
                return v
        return None

    def _f(*keys):
        v = _get(*keys)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # yfinance returns 0-1 ratios for dividendYield, profitMargins, ROE → convert to %
    def _pct(*keys):
        v = _f(*keys)
        if v is None:
            return None
        return round(v * 100, 2) if abs(v) < 1 else round(v, 2)

    return {
        "pe": _f("trailingPE", "forwardPE"),
        "peg": _f("pegRatio", "trailingPegRatio"),
        "eps": _f("trailingEps"),
        "revenue_per_share": _f("revenuePerShare"),
        "profit_margin": _pct("profitMargins"),
        "roe": _pct("returnOnEquity"),
        "debt_equity": _f("debtToEquity"),
        "dividend_yield": _pct("dividendYield"),
        "market_cap": _f("marketCap"),
        "beta": _f("beta"),
        "analyst_target": _f("targetMeanPrice"),
        "analyst_buy": int(_f("numberOfAnalystOpinions") or 0)
            if _get("recommendationKey") in ("buy", "strong_buy") else 0,
        "analyst_hold": int(_f("numberOfAnalystOpinions") or 0)
            if _get("recommendationKey") == "hold" else 0,
        "analyst_sell": int(_f("numberOfAnalystOpinions") or 0)
            if _get("recommendationKey") in ("sell", "strong_sell", "underperform") else 0,
        "price": _f("regularMarketPrice", "currentPrice"),
        # Metadata — used by ticker_metadata.py to resolve sector/name/type
        # dynamically for any uploaded ticker, without needing config.py edits.
        "sector": info.get("sector") or "",
        "long_name": info.get("longName") or info.get("shortName") or "",
        "quote_type": (info.get("quoteType") or "").upper(),  # "ETF", "EQUITY", "CRYPTOCURRENCY", …
        "currency": info.get("currency") or "",
        "_source": "yfinance",
    }


def _merge_non_null(primary: dict, secondary: dict) -> dict:
    """Fill null/None fields in `primary` with values from `secondary`."""
    if not secondary:
        return primary
    out = dict(primary)
    for k, v in secondary.items():
        if out.get(k) in (None, 0, 0.0, "") and v not in (None, 0, 0.0, ""):
            out[k] = v
    return out


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

    Strategy:
      1. yfinance is called for every ticker (free, no API key, covers ETFs +
         international).
      2. For US equities, Alpha Vantage OVERVIEW is called as a supplement to
         fill any missing fields (EPS, RevenuePerShareTTM, DebtToEquityRatio).

    Returns {ticker: fundamentals_dict}. Cached results reused if < 24 h old.
    """
    cache = _load_cache(_FUND_CACHE)
    tickers_data = cache.get("tickers", {})
    is_fresh = _cache_is_fresh(cache, max_age_hours=24)

    result: dict[str, dict] = {}
    need_fetch: list[str] = []

    for tk in tickers:
        if is_fresh and tk in tickers_data:
            result[tk] = tickers_data[tk]
        else:
            need_fetch.append(tk)

    if not need_fetch:
        return result

    print(f"[info] fetching fundamentals for {len(need_fetch)} tickers "
          f"(yfinance primary, Alpha Vantage supplement for US equities)…",
          flush=True)

    have_av = bool(_av_key())
    for i, tk in enumerate(need_fetch):
        # 1. yfinance (covers ETFs, international, etc.)
        yf_data = fetch_fundamentals_yfinance(tk)
        if not yf_data:
            print(f"  [warn] yfinance returned nothing for {tk}", flush=True)

        # 2. Alpha Vantage supplement — skip .TA (AV doesn't cover TASE) and skip
        #    ETFs where we already got clean data from yfinance
        av_data = None
        if have_av and not tk.endswith(".TA"):
            if i > 0:
                time.sleep(2.5)  # stay under 25 calls/min
            av_data = fetch_fundamentals(tk)

        merged = _merge_non_null(yf_data or {}, av_data or {})
        if merged:
            result[tk] = merged
            tickers_data[tk] = merged

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
