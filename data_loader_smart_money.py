"""13F smart-money tracker — follows the 10 most-watched long-biased funds via
SEC EDGAR (edgartools).

The idea is simple: every 45 days after quarter-end, institutional investors
with over $100M AUM must file a 13F-HR listing their equity holdings. Tracking
what the top long-term funds (Berkshire/Baupost/Scion/Pershing/Appaloosa/
Greenlight/Bridgewater/Renaissance/Two Sigma/Citadel) did last quarter is a
classic "smart money" signal:

  - **New position** from a top fund → very bullish (they did weeks of work)
  - **Increased position** → bullish
  - **Net holders across funds** → breadth
  - **Decreased/exited** → bearish

We cache aggressively (7-day TTL) because 13F filings only update quarterly.

Environment:
  EDGAR_IDENTITY — same as `data_loader_insider.py`.
  EDGAR_VERIFY_SSL=false — same SSL workaround for corporate proxies.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent
_SMART_CACHE = _ROOT / "smart_money_cache.json"
_CACHE_TTL_SEC = 7 * 24 * 3600  # one week

# 10 classic long-biased / smart-money funds tracked by the project.
# CIKs are stable forever — they are the filer-identifier at SEC.
_TOP_FUNDS: list[tuple[str, str]] = [
    # (display name, CIK — zero-padded to 10 digits is optional for edgartools)
    ("Berkshire Hathaway",         "0001067983"),
    ("Baupost Group (Klarman)",    "0001061768"),
    ("Scion Asset Mgmt (Burry)",   "0001649339"),
    ("Pershing Square (Ackman)",   "0001336528"),
    ("Appaloosa (Tepper)",         "0001656456"),
    ("Greenlight Capital",         "0001040273"),
    ("Bridgewater Associates",     "0001350694"),
    ("Renaissance Technologies",   "0001037389"),
    ("Two Sigma Investments",      "0001179392"),
    ("Citadel Advisors",           "0001423053"),
]


def _setup_edgar() -> bool:
    os.environ.setdefault("EDGAR_IDENTITY",
                          os.environ.get("EDGAR_IDENTITY",
                                         "Amit Zahy amitzahy@gmail.com"))
    os.environ.setdefault("EDGAR_VERIFY_SSL", "false")
    try:
        import edgar  # noqa: F401
        return True
    except ImportError:
        print("[warn] edgartools not installed — run: pip install edgartools",
              flush=True)
        return False


def _load_cache() -> dict:
    if not _SMART_CACHE.exists():
        return {}
    try:
        return json.loads(_SMART_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    _SMART_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


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


def refresh_smart_money_snapshot() -> dict:
    """Fetch latest 13F-HR for each top fund and cache the QoQ diff per ticker.

    Returns the in-memory snapshot; also writes to disk cache.
    Structure:
        {
          "updated": ISO-8601,
          "funds": [  # one entry per fund that filed
              {"name", "report_period", "holdings": [{ticker, shares, value, status, value_change, ...}]}
          ],
          "by_ticker": {   # aggregated view keyed by ticker
              "NVDA": {"holders": N, "new": [...], "increased": [...], "decreased": [...], "exited": [...],
                        "net_value_change": X}
          },
        }
    """
    if not _setup_edgar():
        return {}

    from edgar import Company

    funds_out = []
    by_ticker: dict[str, dict] = {}

    for name, cik in _TOP_FUNDS:
        try:
            c = Company(cik)
            filings = c.get_filings(form="13F-HR").head(2)  # latest + prev
            if not filings:
                continue
            latest = list(filings)[0].obj()
        except Exception as e:
            print(f"[warn] {name} ({cik}) 13F fetch failed: {e}", flush=True)
            continue

        try:
            cmp = latest.compare_holdings()
        except Exception as e:
            print(f"[warn] {name} compare_holdings failed: {e}", flush=True)
            continue
        if cmp is None or not hasattr(cmp, "data"):
            continue
        df = cmp.data
        if df is None or df.empty:
            continue

        holdings = []
        for _, row in df.iterrows():
            raw_tk = row.get("Ticker")
            if raw_tk is None or (isinstance(raw_tk, float) and raw_tk != raw_tk):
                continue  # NaN or missing
            tk = str(raw_tk).strip().upper()
            if not tk or tk == "NAN":
                continue
            def _num(x, default=0):
                try:
                    if x is None:
                        return default
                    v = float(x)
                    return default if v != v else v  # NaN check
                except (TypeError, ValueError):
                    return default
            status = str(row.get("Status") or "").upper()
            value_change = _num(row.get("ValueChange"))
            value = _num(row.get("Value"))
            h = {
                "ticker": tk,
                "status": status,  # NEW / INCREASED / UNCHANGED / DECREASED / EXITED
                "shares": int(_num(row.get("Shares"))),
                "value_usd": value,
                "value_change_usd": value_change,
                "share_change_pct": _num(row.get("ShareChangePct")),
            }
            holdings.append(h)

            bt = by_ticker.setdefault(tk, {
                "holders": set(), "new": [], "increased": [],
                "unchanged": [], "decreased": [], "exited": [],
                "total_value_usd": 0.0, "net_value_change_usd": 0.0,
            })
            bt["holders"].add(name)
            bt["total_value_usd"] += value
            bt["net_value_change_usd"] += value_change
            bucket = {
                "NEW": "new", "INCREASED": "increased",
                "UNCHANGED": "unchanged",
                "DECREASED": "decreased", "EXITED": "exited",
            }.get(status)
            if bucket:
                bt[bucket].append(name)

        funds_out.append({
            "name": name,
            "report_period": str(cmp.current_period) if cmp else "",
            "previous_period": str(cmp.previous_period) if cmp else "",
            "holdings": holdings,
        })

    # Finalise — serialise the sets
    by_ticker_out = {}
    for tk, d in by_ticker.items():
        by_ticker_out[tk] = {
            "holders": sorted(d["holders"]),
            "holder_count": len(d["holders"]),
            "new": d["new"], "increased": d["increased"],
            "unchanged": d["unchanged"],
            "decreased": d["decreased"], "exited": d["exited"],
            "total_value_usd": round(d["total_value_usd"], 2),
            "net_value_change_usd": round(d["net_value_change_usd"], 2),
        }

    snapshot = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "funds": funds_out,
        "by_ticker": by_ticker_out,
    }

    cache = {"fetched_at": snapshot["updated"], "data": snapshot}
    _save_cache(cache)
    return snapshot


def _load_snapshot() -> dict:
    cache = _load_cache()
    if cache and _fresh(cache):
        return cache.get("data") or {}
    # Refresh if stale or missing
    return refresh_smart_money_snapshot()


def get_ticker_smart_money(ticker: str) -> Optional[dict]:
    """Return the aggregated 13F picture for one ticker, or None if no coverage."""
    if ticker.endswith(".TA") or "-USD" in ticker:
        return None
    snap = _load_snapshot()
    bt = (snap or {}).get("by_ticker", {}).get(ticker.upper())
    return bt


def score_smart_money(info: Optional[dict]) -> int:
    """Smart-money score 0-100 from the aggregated ticker info.

    Heuristic:
      - Base 50
      - +8 per top-fund holder (caps at +40)
      - +10 for any NEW position (one of the funds just entered)
      - +5 for net-positive value change
      - -10 if any EXITED and no NEW
      - -10 for net-negative value change
    """
    if info is None:
        return 50
    hc = info.get("holder_count", 0)
    score = 50 + min(hc * 8, 40)
    if info.get("new"):
        score += 10
    nvc = info.get("net_value_change_usd", 0)
    if nvc > 100_000_000:
        score += 10
    elif nvc > 0:
        score += 5
    elif nvc < -100_000_000:
        score -= 10
    elif nvc < 0:
        score -= 5
    if info.get("exited") and not info.get("new"):
        score -= 10
    return max(0, min(100, score))


def explain_smart_money(info: Optional[dict]) -> list[str]:
    """Return 1-3 short English strings describing the signal."""
    if info is None:
        return ["Not in smart-money universe or no coverage"]
    hc = info.get("holder_count", 0)
    if hc == 0:
        return ["No top-10 funds hold this ticker"]

    lines = [f"Held by {hc}/10 top funds"]
    if info.get("new"):
        lines.append("New position from: " + ", ".join(info["new"][:2]))
    if info.get("increased"):
        lines.append("Increased by: " + ", ".join(info["increased"][:2]))
    if info.get("exited"):
        lines.append("Exited by: " + ", ".join(info["exited"][:2]))
    return lines
