#!/usr/bin/env python3
"""
Daily portfolio snapshot — appends one row to `snapshots.jsonl` with today's
total value (USD and ILS), sector weights, and top-holding weights.

Why this exists: the Performance chart on the Portfolio page reconstructs
historical value from CURRENT weights × historical prices, which drifts from
reality for any stock added mid-period. Real daily snapshots give a true curve.

Run:
    python scripts/snapshot_portfolio.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SNAPSHOTS_PATH = _ROOT / "snapshots.jsonl"


def main() -> None:
    # Lazy-import to keep the script runnable without Streamlit loaded
    from data_loader import (
        load_portfolio, get_holdings_df, fetch_live_quotes,
        fetch_usd_ils_rate, build_portfolio_df,
    )
    from config import ISRAELI_TICKERS

    portfolio = load_portfolio()
    h_df = get_holdings_df(portfolio)
    us = [t for t in h_df["ticker"] if t not in ISRAELI_TICKERS]
    usd_ils = fetch_usd_ils_rate()
    lq = fetch_live_quotes(us)
    pf = build_portfolio_df(h_df, lq, usd_ils)

    tv_u = float(pf["value_usd"].sum())
    tv_i = float(pf["value_ils"].sum())
    tp_u = float(pf["pnl_usd"].sum())
    tc = float(pf["cost_total_usd"].sum())
    tp_pct = (tp_u / tc * 100) if tc > 0 else 0.0

    sector_weights = pf.groupby("sector")["weight"].sum().to_dict()
    top_holdings = (
        pf.sort_values("value_usd", ascending=False)[["ticker", "weight"]]
          .head(5)
          .to_dict("records")
    )

    snapshot = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "value_usd": round(tv_u, 2),
        "value_ils": round(tv_i, 2),
        "pnl_usd": round(tp_u, 2),
        "pnl_pct": round(tp_pct, 2),
        "usd_ils": round(usd_ils, 4),
        "holdings_count": int(len(pf)),
        "sector_weights": {k: round(float(v), 2) for k, v in sector_weights.items()},
        "top_holdings": [{"ticker": h["ticker"], "weight": round(float(h["weight"]), 2)}
                         for h in top_holdings],
    }

    # Append-only JSONL
    with SNAPSHOTS_PATH.open("a") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    print(f"[ok] appended snapshot for {snapshot['date']}: "
          f"${tv_u:,.0f} / ₪{tv_i:,.0f} ({tp_pct:+.1f}%)")


if __name__ == "__main__":
    main()
