"""
Data Pipeline: loads portfolio from JSON, fetches live & historical data from Yahoo Finance.
"""

from __future__ import annotations

import json
import logging
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import pandas as pd
import numpy as np
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import (
    YF_CHART_URL, YF_HEADERS, USDILS_TICKER, SECTOR_MAP,
    ASSET_TYPE_MAP, DISPLAY_NAMES, ISRAELI_TICKERS, AGOROT_TICKERS,
)

logger = logging.getLogger(__name__)


# ─── Portfolio JSON ──────────────────────────────────────────────────────────

def load_portfolio(path: Path = None) -> dict:
    if path is None:
        path = Path(__file__).parent / "portfolio.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_holdings_df(portfolio: dict) -> pd.DataFrame:
    rows = []
    for h in portfolio["holdings"]:
        ticker = h["ticker"]
        is_israeli = ticker in ISRAELI_TICKERS
        rows.append({
            "ticker": ticker,
            "name": h["name"],
            "display_name": DISPLAY_NAMES.get(ticker, h["name"]),
            "quantity": h["quantity"],
            "cost_price": h.get("cost_price_ils") if is_israeli else h.get("cost_price_usd"),
            "cost_unknown": bool(h.get("cost_unknown", False)),
            "is_israeli": is_israeli,
            "sector": SECTOR_MAP.get(ticker, "Other"),
            "asset_type": ASSET_TYPE_MAP.get(ticker, "Other"),
            "ai_recommendation": h.get("ai_recommendation", "-"),
            "ai_rating": h.get("ai_rating", "-"),
            "current_price_ils": h.get("current_price_ils"),
        })
    return pd.DataFrame(rows)


# ─── Yahoo Finance API ───────────────────────────────────────────────────────

def _yf_chart(ticker: str, range_: str = "1d", interval: str = "1d") -> dict | None:
    try:
        url = YF_CHART_URL.format(ticker=ticker)
        params = {"range": range_, "interval": interval}
        r = requests.get(url, headers=YF_HEADERS, params=params, timeout=15, verify=False)
        if r.status_code == 200:
            data = r.json()
            result = data.get("chart", {}).get("result")
            if result:
                return result[0]
        else:
            logger.warning(f"Yahoo Finance returned {r.status_code} for {ticker}")
    except requests.Timeout:
        logger.warning(f"Timeout fetching {ticker}")
    except Exception as e:
        logger.warning(f"Failed to fetch {ticker}: {e}")
    return None


def _fetch_single_quote(ticker: str) -> dict | None:
    """Fetch a single ticker's live quote (for parallel execution)."""
    data = _yf_chart(ticker, range_="5d", interval="1d")
    if not data:
        return None
    meta = data.get("meta", {})
    closes = []
    indicators = data.get("indicators", {}).get("quote", [{}])[0]
    if "close" in indicators:
        closes = [c for c in indicators["close"] if c is not None]

    prev_close = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
    current_price = meta.get("regularMarketPrice")
    daily_change = 0
    if current_price and prev_close and prev_close > 0:
        daily_change = ((current_price / prev_close) - 1) * 100

    # TASE bond ETFs (KSM-F34/F77) are quoted in agorot — convert to ILS
    if ticker in AGOROT_TICKERS:
        if current_price:
            current_price = current_price / 100
        if prev_close:
            prev_close = prev_close / 100

    return {
        "ticker": ticker,
        "price": current_price,
        "prev_close": prev_close,
        "daily_change_pct": daily_change,
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
        "volume": meta.get("regularMarketVolume"),
        "currency": meta.get("currency", "ILS") if ticker in AGOROT_TICKERS else meta.get("currency", "USD"),
    }


def fetch_live_quotes(tickers: list[str]) -> pd.DataFrame:
    """Fetch current prices for all tickers in parallel."""
    rows = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch_single_quote, t): t for t in tickers}
        for future in as_completed(futures):
            result = future.result()
            if result:
                rows.append(result)
    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def fetch_usd_ils_rate() -> float:
    data = _yf_chart(USDILS_TICKER, range_="1d", interval="1d")
    if data:
        return data.get("meta", {}).get("regularMarketPrice", 3.12)
    return 3.12


def fetch_usd_ils_history(period: str = "1y") -> pd.Series:
    data = _yf_chart(USDILS_TICKER, range_=period, interval="1d")
    if data:
        timestamps = data.get("timestamp", [])
        closes = data.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        if timestamps and closes:
            dates = pd.to_datetime(timestamps, unit="s").normalize()
            series = pd.Series(closes, index=dates, name="USDILS")
            return series.dropna()
    return pd.Series(dtype=float, name="USDILS")


def _fetch_single_historical(ticker: str, period: str) -> tuple[str, pd.DataFrame | None]:
    """Fetch historical data for one ticker (for parallel execution)."""
    data = _yf_chart(ticker, range_=period, interval="1d")
    if not data:
        return ticker, None
    timestamps = data.get("timestamp", [])
    quote = data.get("indicators", {}).get("quote", [{}])[0]
    adjclose_list = data.get("indicators", {}).get("adjclose", [{}])
    adjclose = adjclose_list[0].get("adjclose", []) if adjclose_list else []

    if not timestamps or not quote:
        return ticker, None
    dates = pd.to_datetime(timestamps, unit="s").normalize()
    closes_raw = quote.get("close", [])
    # TASE bond ETFs quoted in agorot — convert to ILS
    if ticker in AGOROT_TICKERS:
        closes_raw = [c / 100 if c is not None else None for c in closes_raw]
        adjclose = [c / 100 if c is not None else None for c in (adjclose or closes_raw)]
    df = pd.DataFrame({
        "open": quote.get("open", []),
        "high": quote.get("high", []),
        "low": quote.get("low", []),
        "close": closes_raw,
        "volume": quote.get("volume", []),
        "adjclose": adjclose if adjclose else closes_raw,
    }, index=dates)
    df = df.dropna(subset=["close"])
    return ticker, df if len(df) > 0 else None


def fetch_historical_data(tickers: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """Fetch historical OHLCV data for all tickers in parallel."""
    result = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(_fetch_single_historical, t, period) for t in tickers]
        for future in as_completed(futures):
            ticker, df = future.result()
            if df is not None:
                result[ticker] = df
    return result


# ─── Build Unified Portfolio DataFrame ───────────────────────────────────────

def build_portfolio_df(holdings_df: pd.DataFrame, live_quotes: pd.DataFrame,
                       usd_ils: float) -> pd.DataFrame:
    df = holdings_df.copy()

    for idx, row in df.iterrows():
        ticker = row["ticker"]
        if row["is_israeli"]:
            # Use live price from Yahoo Finance if available; fall back to stored price
            if ticker in live_quotes.index and live_quotes.loc[ticker].get("price"):
                price_ils = live_quotes.loc[ticker]["price"]
                df.at[idx, "daily_change_pct"] = live_quotes.loc[ticker].get("daily_change_pct", 0)
            else:
                price_ils = row.get("current_price_ils") or row["cost_price"]
                df.at[idx, "daily_change_pct"] = 0
            df.at[idx, "live_price"] = price_ils
            df.at[idx, "value_ils"] = price_ils * row["quantity"]
            df.at[idx, "value_usd"] = (price_ils * row["quantity"]) / usd_ils
            df.at[idx, "cost_total_ils"] = row["cost_price"] * row["quantity"]
            df.at[idx, "cost_total_usd"] = df.at[idx, "cost_total_ils"] / usd_ils
            df.at[idx, "pnl_ils"] = df.at[idx, "value_ils"] - df.at[idx, "cost_total_ils"]
            df.at[idx, "pnl_usd"] = df.at[idx, "pnl_ils"] / usd_ils
            df.at[idx, "pnl_pct"] = ((price_ils / row["cost_price"]) - 1) * 100 if row["cost_price"] else 0
            df.at[idx, "currency"] = "ILS"
        elif ticker in live_quotes.index:
            quote = live_quotes.loc[ticker]
            price = quote["price"]
            cost_unknown = bool(row.get("cost_unknown"))
            cost_price = row["cost_price"] if row.get("cost_price") else None
            df.at[idx, "live_price"] = price
            df.at[idx, "value_usd"] = price * row["quantity"]
            df.at[idx, "value_ils"] = price * row["quantity"] * usd_ils
            if cost_unknown or not cost_price:
                # P&L is unknown — don't pollute totals with a fake zero-cost line.
                df.at[idx, "cost_total_usd"] = 0
                df.at[idx, "cost_total_ils"] = 0
                df.at[idx, "pnl_usd"] = 0
                df.at[idx, "pnl_ils"] = 0
                df.at[idx, "pnl_pct"] = np.nan  # Displayed as "—"
            else:
                df.at[idx, "cost_total_usd"] = cost_price * row["quantity"]
                df.at[idx, "cost_total_ils"] = df.at[idx, "cost_total_usd"] * usd_ils
                df.at[idx, "pnl_usd"] = df.at[idx, "value_usd"] - df.at[idx, "cost_total_usd"]
                df.at[idx, "pnl_ils"] = df.at[idx, "pnl_usd"] * usd_ils
                df.at[idx, "pnl_pct"] = ((price / cost_price) - 1) * 100
            df.at[idx, "daily_change_pct"] = quote.get("daily_change_pct", 0)
            df.at[idx, "fifty_two_week_high"] = quote.get("fifty_two_week_high")
            df.at[idx, "fifty_two_week_low"] = quote.get("fifty_two_week_low")
            df.at[idx, "currency"] = quote.get("currency", "USD")
        else:
            df.at[idx, "live_price"] = row["cost_price"]
            df.at[idx, "value_usd"] = row["cost_price"] * row["quantity"]
            df.at[idx, "value_ils"] = df.at[idx, "value_usd"] * usd_ils
            df.at[idx, "cost_total_usd"] = row["cost_price"] * row["quantity"]
            df.at[idx, "cost_total_ils"] = df.at[idx, "cost_total_usd"] * usd_ils
            df.at[idx, "pnl_usd"] = 0
            df.at[idx, "pnl_ils"] = 0
            df.at[idx, "pnl_pct"] = 0
            df.at[idx, "daily_change_pct"] = 0
            df.at[idx, "currency"] = "USD"

    total_value_ils = df["value_ils"].sum()
    df["weight"] = (df["value_ils"] / total_value_ils * 100) if total_value_ils > 0 else 0
    df = df.sort_values("value_ils", ascending=False).reset_index(drop=True)
    return df


# ─── Period P&L Calculations ─────────────────────────────────────────────────

def compute_period_changes(historical: dict[str, pd.DataFrame],
                           holdings_df: pd.DataFrame,
                           usd_ils_history: pd.Series,
                           usd_ils_now: float) -> dict:
    periods = {
        "1d": 1, "1w": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252, "all": 9999,
    }
    results = {}

    for period_name, days_back in periods.items():
        total_value_now_usd = 0
        total_value_then_usd = 0

        for _, row in holdings_df.iterrows():
            ticker = row["ticker"]
            qty = row["quantity"]

            if row["is_israeli"] and ticker not in historical:
                # Fallback: no historical data — use static price for both
                price_ils = row.get("current_price_ils") or row["cost_price"]
                val_now = (price_ils * qty) / usd_ils_now
                total_value_now_usd += val_now
                total_value_then_usd += val_now
                continue

            if ticker not in historical:
                continue

            hist = historical[ticker]
            if len(hist) == 0:
                continue

            price_now = hist["close"].iloc[-1]
            idx = min(days_back, len(hist) - 1)
            price_then = hist["close"].iloc[-1 - idx] if idx < len(hist) else hist["close"].iloc[0]

            if row["is_israeli"]:
                # Prices are in ILS — convert to USD
                total_value_now_usd += (price_now * qty) / usd_ils_now
                total_value_then_usd += (price_then * qty) / usd_ils_now
            else:
                total_value_now_usd += price_now * qty
                total_value_then_usd += price_then * qty

        pnl_usd = total_value_now_usd - total_value_then_usd
        pnl_pct = ((total_value_now_usd / total_value_then_usd) - 1) * 100 if total_value_then_usd > 0 else 0

        fx_now = usd_ils_now
        fx_then = fx_now
        if len(usd_ils_history) > days_back:
            fx_then = usd_ils_history.iloc[-min(days_back, len(usd_ils_history))]
        elif len(usd_ils_history) > 0:
            fx_then = usd_ils_history.iloc[0]

        value_now_ils = total_value_now_usd * fx_now
        value_then_ils = total_value_then_usd * fx_then
        pnl_ils = value_now_ils - value_then_ils
        pnl_ils_pct = ((value_now_ils / value_then_ils) - 1) * 100 if value_then_ils > 0 else 0
        fx_impact_pct = ((fx_now / fx_then) - 1) * 100 if fx_then > 0 else 0

        results[period_name] = {
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "pnl_ils": pnl_ils,
            "pnl_ils_pct": pnl_ils_pct,
            "fx_rate_now": fx_now,
            "fx_rate_then": fx_then,
            "fx_impact_pct": fx_impact_pct,
        }

    return results
