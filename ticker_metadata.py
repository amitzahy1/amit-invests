"""Dynamic ticker metadata — resolves sector / asset type / display name without
requiring hand-edits to config.py every time a new ticker is uploaded.

Resolution order (first hit wins):
  1. User override in `config.py` (SECTOR_MAP / ASSET_TYPE_MAP / DISPLAY_NAMES) —
     lets the user keep custom Hebrew names or broader theme-based sectors like
     "Broad Market" or "Energy / Uranium" that yfinance doesn't produce.
  2. `fundamentals_cache.json` — populated by
     `data_loader_fundamentals.fetch_fundamentals_yfinance()` with the
     ticker's `sector`, `long_name`, and `quote_type` fields from Yahoo Finance.
  3. Sensible fallback (the ticker itself / "Other").

This way, adding a new ticker via the Excel upload path just works — the first
daily cron run fills its metadata from yfinance automatically.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_FUND_CACHE = _ROOT / "fundamentals_cache.json"


@lru_cache(maxsize=1)
def _load_cache_tickers() -> dict:
    """Cached read of fundamentals_cache.json → {ticker: metadata-dict}."""
    if not _FUND_CACHE.exists():
        return {}
    try:
        return json.loads(_FUND_CACHE.read_text()).get("tickers", {}) or {}
    except Exception:
        return {}


def _refresh_cache_view() -> None:
    """Invalidate the lru_cache so subsequent calls re-read the file.

    Call after writing to fundamentals_cache.json within the same process.
    """
    _load_cache_tickers.cache_clear()


# ── Quote-type → asset-type mapping ──────────────────────────────────────────
# yfinance's `quoteType` is coarse ("ETF", "EQUITY", …). We map it to the more
# granular labels the project already uses where possible.
_QUOTE_TYPE_MAP = {
    "EQUITY": "US Stock",
    "ETF": "ETF",
    "MUTUALFUND": "Mutual Fund",
    "CRYPTOCURRENCY": "Crypto",
    "INDEX": "Index",
    "CURRENCY": "Currency",
    "FUTURE": "Future",
}


def get_sector(ticker: str) -> str:
    """Return a sector label for `ticker`. Never raises; never returns empty."""
    # 1. User override
    try:
        from config import SECTOR_MAP
        if ticker in SECTOR_MAP:
            return SECTOR_MAP[ticker]
    except Exception:
        pass

    # 2. yfinance cache
    meta = _load_cache_tickers().get(ticker) or {}
    sector = (meta.get("sector") or "").strip()
    if sector:
        return sector

    # 3. Fallback
    return "Other"


def get_asset_type(ticker: str) -> str:
    """Return an asset-type label (e.g. 'US Stock', 'ETF', 'Crypto')."""
    # 1. User override
    try:
        from config import ASSET_TYPE_MAP
        if ticker in ASSET_TYPE_MAP:
            return ASSET_TYPE_MAP[ticker]
    except Exception:
        pass

    # 2. yfinance cache → quote_type
    meta = _load_cache_tickers().get(ticker) or {}
    qt = (meta.get("quote_type") or "").upper()
    if qt:
        return _QUOTE_TYPE_MAP.get(qt, qt.title())

    # 3. Fallback
    return ""


def get_display_name(ticker: str) -> str:
    """Return a human-readable display name (long_name from yfinance, or fallback)."""
    # 1. User override
    try:
        from config import DISPLAY_NAMES
        if ticker in DISPLAY_NAMES:
            return DISPLAY_NAMES[ticker]
    except Exception:
        pass

    # 2. yfinance cache
    meta = _load_cache_tickers().get(ticker) or {}
    name = (meta.get("long_name") or "").strip()
    if name:
        return name

    # 3. Fallback — just the ticker
    return ticker


def get_all_metadata(ticker: str) -> dict:
    """Convenience — returns {sector, asset_type, display_name} in one call."""
    return {
        "sector": get_sector(ticker),
        "asset_type": get_asset_type(ticker),
        "display_name": get_display_name(ticker),
    }
