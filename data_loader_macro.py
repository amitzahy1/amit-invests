"""
Macro data fetcher — FRED API + Yahoo Finance fallbacks.

FRED is free and unlimited.  We cache results in macro_cache.json (6 h TTL).
Yahoo Finance provides VIX and index daily changes as fallback.
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
_MACRO_CACHE = _ROOT / "macro_cache.json"
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_TIMEOUT = 10

# Yahoo Finance chart API (same as data_loader.py)
_YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_YF_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fred_key() -> str | None:
    return os.environ.get("FRED_API_KEY")


def _load_cache() -> dict:
    if not _MACRO_CACHE.exists():
        return {}
    try:
        return json.loads(_MACRO_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    _MACRO_CACHE.write_text(json.dumps(data, indent=2))


def _cache_is_fresh(cache: dict, max_age_hours: float = 6) -> bool:
    ts = cache.get("updated")
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
        return age < max_age_hours * 3600
    except Exception:
        return False


# ── FRED fetchers ────────────────────────────────────────────────────────────

def _fred_latest(series_id: str) -> float | None:
    """Get the most recent observation for a FRED series."""
    key = _fred_key()
    if not key:
        return None
    try:
        resp = requests.get(_FRED_BASE, params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        }, timeout=_TIMEOUT, verify=False)
        if resp.status_code != 200:
            return None
        obs = resp.json().get("observations", [])
        for o in obs:
            v = o.get("value")
            if v and v != ".":
                return float(v)
    except Exception:
        pass
    return None


# ── Yahoo Finance fallbacks ──────────────────────────────────────────────────

def _yf_quote(ticker: str) -> dict | None:
    """Minimal Yahoo Finance quote for a single ticker."""
    try:
        resp = requests.get(
            _YF_CHART.format(ticker=ticker),
            params={"range": "5d", "interval": "1d"},
            headers=_YF_HEADERS,
            timeout=_TIMEOUT,
            verify=False,
        )
        if resp.status_code != 200:
            return None
        result = resp.json()["chart"]["result"][0]
        meta = result.get("meta", {})
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        closes = [c for c in closes if c is not None]
        price = meta.get("regularMarketPrice")
        prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
        change_pct = ((price / prev) - 1) * 100 if price and prev else None
        return {"price": price, "change_pct": change_pct}
    except Exception:
        return None


# ── Main entry point ─────────────────────────────────────────────────────────

def fetch_macro_snapshot() -> dict:
    """Fetch current macro environment from FRED + Yahoo Finance.

    Returns dict with keys: fed_rate, ten_year_yield, cpi_yoy, vix,
    sp500_change, nasdaq_change, usd_ils.
    Uses cached values if < 6 h old.
    """
    cache = _load_cache()
    # Only use cache if it has essential fields (prevents broken-cache pollution)
    ESSENTIAL_FIELDS = ["vix", "fed_rate", "ten_year_yield"]
    cache_is_valid = all(cache.get(f) is not None for f in ESSENTIAL_FIELDS)
    if _cache_is_fresh(cache, max_age_hours=6) and cache_is_valid:
        data = dict(cache)
        data.pop("updated", None)
        return data

    print("[info] fetching macro data (FRED + Yahoo Finance)…", flush=True)
    result: dict = {}

    # FRED data
    if _fred_key():
        result["fed_rate"] = _fred_latest("FEDFUNDS")
        result["ten_year_yield"] = _fred_latest("DGS10")
        # CPI year-over-year: fetch latest value (index level, not YoY directly)
        cpi = _fred_latest("CPIAUCSL")
        result["cpi_latest"] = cpi  # raw index; YoY needs 12-month-ago value
    else:
        print("[warn] FRED_API_KEY not set — skipping FRED macro data", flush=True)

    # Yahoo Finance: VIX, S&P 500, Nasdaq, USD/ILS
    vix_q = _yf_quote("^VIX")
    if vix_q:
        result["vix"] = round(vix_q["price"], 1) if vix_q.get("price") else None

    sp_q = _yf_quote("^GSPC")
    if sp_q and sp_q.get("change_pct") is not None:
        result["sp500_change"] = round(sp_q["change_pct"], 2)

    ndx_q = _yf_quote("^IXIC")
    if ndx_q and ndx_q.get("change_pct") is not None:
        result["nasdaq_change"] = round(ndx_q["change_pct"], 2)

    ils_q = _yf_quote("USDILS=X")
    if ils_q and ils_q.get("price"):
        result["usd_ils"] = round(ils_q["price"], 3)

    # Save cache
    cache_out = {**result, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"}
    _save_cache(cache_out)
    print(f"[ok] macro snapshot: VIX={result.get('vix')}, "
          f"S&P={result.get('sp500_change')}%, Fed={result.get('fed_rate')}%",
          flush=True)
    return result
