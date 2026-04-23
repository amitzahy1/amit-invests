"""Unusual Options Activity (UOA) scanner via yfinance option chains.

yfinance exposes 15-minute-delayed option chains per ticker at no cost. We scan
the nearest 3 expirations per ticker for contracts where:

  - Volume > 3 × Open Interest  (fresh aggressive buying, not scalping)
  - Contract premium (volume × mid) > $25k
  - DTE in [7, 90] days  (>7 filters out weekly scalps; <=90 captures positioning)

The scan yields a per-ticker score 0-100 reflecting whether UOA is net bullish
(big-call activity) or bearish (big-put activity), plus top contracts.

Why this matters: sustained UOA is a leading indicator around binary catalysts
(earnings, FDA, M&A) that the underlying price hasn't reflected yet. This is
the free-data version of what Unusual Whales / FlowAlgo ($100+/mo) expose.

Graceful degradation: yfinance aggressively rate-limits option endpoints — if
we hit a 429, we return None and caller skips.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent
_CACHE = _ROOT / "options_cache.json"
_CACHE_TTL_SEC = 6 * 3600  # chains update intraday, 6h is a reasonable refresh
_MIN_DTE = 7
_MAX_DTE = 90
_MIN_PREMIUM = 25_000
_VOL_OI_MULT = 3.0


def _load_cache() -> dict:
    if not _CACHE.exists():
        return {}
    try:
        return json.loads(_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
        return age < _CACHE_TTL_SEC
    except Exception:
        return False


def _scan_chain_side(chain_df, *, side: str, spot: float, dte: int) -> list[dict]:
    """Return contracts on one side (`calls` or `puts`) that meet UOA criteria."""
    hits = []
    if chain_df is None or chain_df.empty:
        return hits
    for _, row in chain_df.iterrows():
        # yfinance option rows can have NaN for volume/openInterest on
        # illiquid strikes. `float(x or 0)` keeps NaN (truthy), so int(NaN)
        # raises later — coerce NaN to 0 explicitly.
        def _num(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return 0.0
            return 0.0 if math.isnan(v) else v

        vol = _num(row.get("volume"))
        oi = _num(row.get("openInterest"))
        last = _num(row.get("lastPrice"))
        strike = _num(row.get("strike"))
        if vol <= 0 or oi <= 0 or last <= 0 or strike <= 0:
            continue
        vol_oi = vol / max(oi, 1)
        premium = vol * last * 100  # option contracts are 100 shares each
        if vol_oi < _VOL_OI_MULT or premium < _MIN_PREMIUM:
            continue
        hits.append({
            "side": side,
            "strike": strike,
            "otm_pct": round((strike / spot - 1) * 100, 2) if side == "call"
                        else round((1 - strike / spot) * 100, 2),
            "dte": dte,
            "volume": int(vol),
            "open_interest": int(oi),
            "vol_oi_ratio": round(vol_oi, 2),
            "last_price": round(last, 2),
            "premium_usd": round(premium, 2),
        })
    return hits


def scan_ticker_options(ticker: str) -> Optional[dict]:
    """Scan nearest 3 expirations for UOA on `ticker`.

    Returns:
        {ticker, spot, call_hits: [...], put_hits: [...],
         net_call_premium, net_put_premium, bias}
    Returns None if yfinance fails (rate-limited, delisted, no options, etc.).
    """
    if ticker.endswith(".TA") or "-USD" in ticker:
        return None

    cache = _load_cache()
    entry = cache.get(ticker)
    if entry and _fresh(entry):
        return entry.get("data")

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        spot = float(info.get("regularMarketPrice") or info.get("currentPrice") or 0)
        if spot <= 0:
            return None
        expirations = t.options or ()
    except Exception as e:
        print(f"[warn] UOA: options listing failed for {ticker}: {e}", flush=True)
        return None

    if not expirations:
        return None

    today = datetime.now().date()
    call_hits: list[dict] = []
    put_hits: list[dict] = []

    for exp_str in expirations[:6]:  # scan up to 6 expirations, filter by DTE below
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (exp_date - today).days
        if dte < _MIN_DTE or dte > _MAX_DTE:
            continue
        try:
            chain = t.option_chain(exp_str)
        except Exception as e:
            # yfinance rate-limit shows up here — stop scanning quietly
            print(f"[warn] UOA: chain fetch failed for {ticker} {exp_str}: {e}",
                  flush=True)
            break

        call_hits.extend(_scan_chain_side(chain.calls, side="call",
                                          spot=spot, dte=dte))
        put_hits.extend(_scan_chain_side(chain.puts, side="put",
                                         spot=spot, dte=dte))

    # Sort by premium size (the biggest-$ bets first)
    call_hits.sort(key=lambda c: -c["premium_usd"])
    put_hits.sort(key=lambda p: -p["premium_usd"])

    net_call_prem = sum(c["premium_usd"] for c in call_hits)
    net_put_prem = sum(p["premium_usd"] for p in put_hits)

    # Bias: which side has more dollar flow
    if net_call_prem + net_put_prem == 0:
        bias = "none"
    else:
        ratio = net_call_prem / (net_call_prem + net_put_prem)
        if ratio > 0.65:
            bias = "bullish"
        elif ratio < 0.35:
            bias = "bearish"
        else:
            bias = "mixed"

    out = {
        "ticker": ticker,
        "spot": spot,
        "call_hits": call_hits[:5],
        "put_hits": put_hits[:5],
        "net_call_premium": round(net_call_prem, 2),
        "net_put_premium": round(net_put_prem, 2),
        "bias": bias,
    }

    cache[ticker] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": out,
    }
    _save_cache(cache)
    return out


def format_uoa_telegram(info: Optional[dict]) -> str:
    """Build a short Markdown block for Telegram; returns "" if nothing to show."""
    if info is None:
        return ""
    call_count = len(info.get("call_hits", []))
    put_count = len(info.get("put_hits", []))
    if call_count == 0 and put_count == 0:
        return ""

    tk = info["ticker"]
    bias = info.get("bias", "none")
    bias_dot = {"bullish": "🟢", "bearish": "🔴", "mixed": "🟡", "none": "⚪"}.get(bias, "⚪")
    spot = info.get("spot", 0)
    cp = info.get("net_call_premium", 0)
    pp = info.get("net_put_premium", 0)

    lines = [f"⚡ *UOA {tk}* @ ${spot:,.2f} · {bias_dot} {bias}"]
    lines.append(f"   CALL${cp/1000:.0f}k · PUT${pp/1000:.0f}k")

    for c in (info.get("call_hits") or [])[:2]:
        lines.append(
            f"   🟢 ${c['strike']:.0f}C {c['dte']}d · "
            f"vol/OI {c['vol_oi_ratio']:.1f}× · ${c['premium_usd']/1000:.0f}k"
        )
    for p in (info.get("put_hits") or [])[:2]:
        lines.append(
            f"   🔴 ${p['strike']:.0f}P {p['dte']}d · "
            f"vol/OI {p['vol_oi_ratio']:.1f}× · ${p['premium_usd']/1000:.0f}k"
        )
    return "\n".join(lines)


def scan_portfolio_uoa(tickers: list[str]) -> list[dict]:
    """Scan a list of tickers and return only those with UOA hits, sorted by
    total premium flow. Safe to call even when yfinance is rate-limited."""
    results = []
    for tk in tickers:
        info = scan_ticker_options(tk)
        if not info:
            continue
        if info.get("call_hits") or info.get("put_hits"):
            info["total_premium"] = (info.get("net_call_premium", 0)
                                     + info.get("net_put_premium", 0))
            results.append(info)
    results.sort(key=lambda x: -x.get("total_premium", 0))
    return results
