"""Insider trading data via SEC EDGAR Form 4 filings (edgartools).

Form 4 filings are legally required within 2 business days whenever any
officer, director, or 10%+ owner trades the company's stock. Aggregating them
gives a strong "smart insider" signal:

  - **Cluster buys** — 3+ insiders buying in a 30-day window (one of the most
    statistically robust indicators in the literature)
  - **Executive-level buys** — CEO/CFO open-market purchases > $100k
  - **Net-buy/net-sell ratio** in dollar terms

We cache responses (24h TTL) because EDGAR is free but rate-limited
(10 req/sec per SEC guidance) and we don't want to hammer it.

Environment:
  EDGAR_IDENTITY  -- required by SEC. Format: "Name email@domain.com".
  EDGAR_VERIFY_SSL  -- set to "false" to disable SSL verification (needed
  behind corporate proxies that inject self-signed certs).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent
_INSIDER_CACHE = _ROOT / "insider_cache.json"
_CACHE_TTL_SEC = 24 * 3600

# Identity (SEC-mandated User-Agent). Override via env if you'd rather not
# hard-code yours.
_DEFAULT_IDENTITY = "Amit Zahy amitzahy@gmail.com"

# Roles that get extra weight — these are the ones who have inside-the-room
# knowledge.
_EXECUTIVE_ROLES = {
    "chief executive officer", "ceo",
    "chief financial officer", "cfo",
    "chief operating officer", "coo",
    "president",
    "chairman", "chairperson",
    "chief executive", "director",  # directors count but less
}

_LOOKBACK_DAYS = 90
_CLUSTER_WINDOW_DAYS = 30
_CLUSTER_MIN_INSIDERS = 3


def _setup_edgar():
    """Lazily configure edgartools once per process."""
    global _edgar_ready
    try:
        return _edgar_ready
    except NameError:
        pass
    os.environ.setdefault("EDGAR_IDENTITY",
                          os.environ.get("EDGAR_IDENTITY", _DEFAULT_IDENTITY))
    os.environ.setdefault("EDGAR_VERIFY_SSL", "false")
    try:
        import edgar  # noqa: F401
        _edgar_ready = True
    except ImportError:
        print("[warn] edgartools not installed — run: pip install edgartools",
              flush=True)
        _edgar_ready = False
    return _edgar_ready


def _load_cache() -> dict:
    if not _INSIDER_CACHE.exists():
        return {}
    try:
        return json.loads(_INSIDER_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    _INSIDER_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


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


def _is_executive(role: str) -> bool:
    if not role:
        return False
    r = role.lower().strip()
    return any(er in r for er in _EXECUTIVE_ROLES if er not in ("director",))


def fetch_insider_activity(ticker: str) -> Optional[dict]:
    """Return aggregated insider activity for the last 90 days.

    Returns dict:
      {
        "ticker", "buy_count", "sell_count",
        "buy_value_usd", "sell_value_usd", "net_value_usd",
        "executive_buy": bool,
        "executive_sell_only": bool,
        "cluster_buy": bool,          # >=3 insiders bought within 30 days
        "unique_buyers": int,
        "recent_trades": [  # last 10, most recent first
            {"date", "insider", "role", "action", "shares", "price", "value_usd"}
        ],
        "last_filed": "YYYY-MM-DD" or None,
      }
    Returns None for non-US tickers or if edgartools is unavailable.
    """
    if ticker.endswith(".TA") or "-USD" in ticker:
        return None

    cache = _load_cache()
    entry = cache.get(ticker)
    if entry and _fresh(entry):
        return entry.get("data")

    if not _setup_edgar():
        return None

    try:
        from edgar import Company
        c = Company(ticker)
    except Exception as e:
        print(f"[warn] edgartools Company({ticker}) failed: {e}", flush=True)
        return None
    if not c:
        return None

    cutoff = datetime.now().date() - timedelta(days=_LOOKBACK_DAYS)

    try:
        # Fetch last 25 Form 4 filings — covers ~3 months for most active names
        filings = c.get_filings(form="4").head(25)
    except Exception as e:
        print(f"[warn] get_filings Form 4 failed for {ticker}: {e}", flush=True)
        return None

    buy_count = 0
    sell_count = 0
    buy_value = 0.0
    sell_value = 0.0
    executive_buy = False
    executive_sell_count = 0
    buyers_by_date = []  # (date, insider_name) for cluster detection
    trades = []
    last_filed = None

    for f in filings:
        try:
            fd = f.filing_date
            if hasattr(fd, "date"):
                fd = fd.date()
            elif isinstance(fd, str):
                fd = datetime.strptime(fd[:10], "%Y-%m-%d").date()
        except Exception:
            fd = None
        if fd and fd < cutoff:
            break  # filings are newest-first; we're past the window
        if last_filed is None and fd:
            last_filed = fd.isoformat()

        try:
            obj = f.obj()
        except Exception:
            continue
        if obj is None:
            continue

        insider = getattr(obj, "insider_name", "") or ""
        role = getattr(obj, "position", "") or ""
        is_exec = _is_executive(role)

        mt = getattr(obj, "market_trades", None)
        if mt is None or getattr(mt, "empty", True):
            continue

        for _, row in mt.iterrows():
            action = str(row.get("TransactionType") or "").strip()
            shares = float(row.get("Shares") or 0)
            price = float(row.get("Price") or 0)
            value = shares * price
            date_raw = row.get("Date")
            try:
                tdate = (datetime.strptime(str(date_raw)[:10], "%Y-%m-%d").date()
                         if date_raw else None)
            except Exception:
                tdate = None

            trade = {
                "date": tdate.isoformat() if tdate else None,
                "insider": insider,
                "role": role,
                "action": action,
                "shares": shares,
                "price": price,
                "value_usd": round(value, 2),
            }

            if action.lower() == "purchase":
                buy_count += 1
                buy_value += value
                if is_exec and value >= 100_000:
                    executive_buy = True
                if tdate:
                    buyers_by_date.append((tdate, insider))
            elif action.lower() == "sale":
                sell_count += 1
                sell_value += value
                if is_exec:
                    executive_sell_count += 1

            trades.append(trade)

    # Cluster detection: >=3 distinct insiders buying within any 30-day window
    cluster_buy = False
    if len(buyers_by_date) >= _CLUSTER_MIN_INSIDERS:
        buyers_by_date.sort()
        for i, (d1, _) in enumerate(buyers_by_date):
            window = {
                name for d2, name in buyers_by_date[i:]
                if (d2 - d1).days <= _CLUSTER_WINDOW_DAYS
            }
            if len(window) >= _CLUSTER_MIN_INSIDERS:
                cluster_buy = True
                break

    unique_buyers = len({n for _, n in buyers_by_date})
    executive_sell_only = sell_count > 0 and buy_count == 0 and executive_sell_count > 0

    trades.sort(key=lambda t: (t["date"] or "", t["insider"]), reverse=True)

    out = {
        "ticker": ticker,
        "lookback_days": _LOOKBACK_DAYS,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_value_usd": round(buy_value, 2),
        "sell_value_usd": round(sell_value, 2),
        "net_value_usd": round(buy_value - sell_value, 2),
        "executive_buy": executive_buy,
        "executive_sell_only": executive_sell_only,
        "cluster_buy": cluster_buy,
        "unique_buyers": unique_buyers,
        "recent_trades": trades[:10],
        "last_filed": last_filed,
    }

    cache[ticker] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": out,
    }
    _save_cache(cache)
    return out


def score_insider(activity: Optional[dict]) -> int:
    """Turn insider-activity dict into a 0-100 score (higher = more bullish insiders).

    No activity → neutral 50.
    Cluster buy → +20
    Executive open-market buy ≥ $100k → +15
    Net-positive dollar flow → +10 (or scaled if very large)
    Executive-only selling → -15
    Heavy overall selling → scaled penalty

    Caps at 0..100.
    """
    if activity is None:
        return 50

    score = 50
    if activity.get("cluster_buy"):
        score += 20
    if activity.get("executive_buy"):
        score += 15

    net = activity.get("net_value_usd", 0)
    if net > 1_000_000:
        score += 15
    elif net > 100_000:
        score += 10
    elif net < -5_000_000:
        score -= 20
    elif net < -1_000_000:
        score -= 10

    if activity.get("executive_sell_only"):
        score -= 15

    # Mild boost for lots of unique buyers (signal breadth)
    ub = activity.get("unique_buyers", 0)
    if ub >= 5:
        score += 5
    elif ub >= 3:
        score += 3

    return max(0, min(100, score))


def explain_insider(activity: Optional[dict]) -> list[str]:
    """Return 1-3 short English strings describing the signal — for score_details."""
    if activity is None:
        return ["No insider activity (non-US or data unavailable)"]

    if activity.get("buy_count", 0) == 0 and activity.get("sell_count", 0) == 0:
        return [f"No insider trades in last {activity.get('lookback_days', 90)} days"]

    lines = []
    net = activity.get("net_value_usd", 0)
    buys = activity.get("buy_count", 0)
    sells = activity.get("sell_count", 0)
    ub = activity.get("unique_buyers", 0)
    sign = "+" if net >= 0 else ""
    lines.append(f"Last 90d: {buys} buys / {sells} sells, net {sign}${net:,.0f}")

    if activity.get("cluster_buy"):
        lines.append(f"Cluster buy — {ub} insiders bought within 30 days")
    if activity.get("executive_buy"):
        lines.append("Executive open-market purchase (C-level) > $100k")
    if activity.get("executive_sell_only"):
        lines.append("Executive-only selling pressure")
    return lines
