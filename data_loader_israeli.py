"""TASE (Israeli market) data fetcher via pymaya.

pymaya scrapes TASE's public Maya portal (maya.tase.co.il) and works for mutual
funds, ETFs, and bonds. It is the project's fix for the long-standing
`Yahoo Finance returned 404 for 5108.TA` problem.

Environment:
  Requires internet access to api.tase.co.il. SSL verification is off because
  Israeli corporate proxies (e.g. the user's Zscaler) inject self-signed certs.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent
_TASE_CACHE = _ROOT / "tase_cache.json"
_CACHE_TTL_SEC = 6 * 3600  # 6 hours — TASE prices update once a day anyway

# Yahoo-style ticker → TASE security number. These IDs are the keys required
# by Maya's API (pymaya.get_details / get_price_history). They are stable —
# TASE never recycles security numbers.
TICKER_TO_TASE_ID = {
    "5108.TA": "5108329",     # KSM TA-Insurance ETF
    "KSM-F34.TA": "1209509",  # KSM Government Bond F34 (verify by calling m.mapped_securities)
}


def _get_maya_client():
    """Return a cached pymaya.Maya instance with SSL verification disabled.

    Creating the client hits /api/content/searchentities to populate the
    ticker→security-id map, so we cache the client for the process lifetime.
    """
    global _client
    try:
        return _client
    except NameError:
        pass
    try:
        from pymaya.maya import Maya
        _client = Maya(verify=False)
    except Exception as e:
        print(f"[warn] pymaya init failed: {e}", flush=True)
        _client = None
    return _client


def _load_cache() -> dict:
    if not _TASE_CACHE.exists():
        return {}
    try:
        return json.loads(_TASE_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    _TASE_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _cache_fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
        return age < _CACHE_TTL_SEC
    except Exception:
        return False


def fetch_tase_quote(ticker: str) -> Optional[dict]:
    """Fetch the latest quote + yields for a TASE ticker.

    Returns {price, currency, day_change_pct, month_change_pct, ytd_pct,
             year_change_pct, asset_value_ils, updated} — or None on failure.
    Prices are reported in **agorot** (1/100 of a shekel); the caller is
    responsible for converting to NIS if needed.
    """
    if not ticker.endswith(".TA"):
        return None
    sec_id = TICKER_TO_TASE_ID.get(ticker)
    if not sec_id:
        return None

    # Cache check
    cache = _load_cache()
    entry = cache.get(ticker)
    if entry and _cache_fresh(entry):
        return entry.get("data")

    m = _get_maya_client()
    if not m:
        return None

    try:
        d = m.get_details(sec_id)
    except Exception as e:
        print(f"[warn] pymaya get_details({sec_id}) failed: {e}", flush=True)
        return None
    if not d:
        return None

    out = {
        "ticker": ticker,
        "security_id": sec_id,
        "price": d.get("UnitValuePrice") or d.get("PurchasePrice"),  # agorot
        "currency": "ILS",
        "day_change_pct": d.get("DayYield"),
        "month_change_pct": d.get("MonthYield"),
        "year_change_pct": d.get("YearYield"),
        "twelve_month_pct": d.get("Last12MonthYield"),
        "asset_value_ils": d.get("AssetValue"),
        "mng_fee_pct": d.get("ManagementFee"),
        "updated": d.get("UnitValueValidDate"),
        "name": d.get("FundLongName") or d.get("FundShortName") or "",
    }

    cache[ticker] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": out,
    }
    _save_cache(cache)
    return out


def fetch_tase_history(ticker: str, days_back: int = 365) -> Optional[list[dict]]:
    """Fetch daily OHLC(V)-equivalent history from TASE Maya.

    pymaya returns a sequence of dicts with {TradeDate, PurchasePrice,
    SellPrice, AssetValue, ...}. We normalise to {date, close} so the rest of
    the codebase can treat it like any other price series.
    """
    sec_id = TICKER_TO_TASE_ID.get(ticker)
    if not sec_id:
        return None
    m = _get_maya_client()
    if not m:
        return None

    today = date.today()
    try:
        gen = m.get_price_history(sec_id, today - timedelta(days=days_back), today)
        rows = list(gen)
    except Exception as e:
        print(f"[warn] pymaya get_price_history({sec_id}) failed: {e}", flush=True)
        return None

    out = []
    for r in rows:
        td = r.get("TradeDate")
        px = r.get("PurchasePrice") or r.get("SellPrice")
        if td and px is not None:
            out.append({"date": td[:10], "close": px})
    # Maya returns newest-first; reverse to oldest-first like yfinance
    out.reverse()
    return out or None
