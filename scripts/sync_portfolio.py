#!/usr/bin/env python3
"""
Sync portfolio from Yahoo Finance → portfolio.json.

Uses existing OSS: browser-use (https://github.com/browser-use/browser-use).
browser-use drives a real browser via an LLM. We give it Gemini credentials and
ask it to log in to Yahoo Finance, navigate to the user's portfolio page, and
extract holdings.

⚠️  SECURITY: This script ONLY receives Yahoo Finance credentials. It must NEVER
    be pointed at any broker (Extrade Pro, etc.) — Yahoo Finance is read-only
    and therefore safe to automate; a broker site can execute trades.

Environment:
    GEMINI_API_KEY       Google AI Studio key (https://aistudio.google.com/apikey)
    YAHOO_USERNAME       your Yahoo login email
    YAHOO_PASSWORD       your Yahoo password
    YAHOO_PORTFOLIO_URL  (optional) direct link to your portfolio, e.g.
                         https://finance.yahoo.com/portfolio/p_0/view/v1

Usage:
    python scripts/sync_portfolio.py --headful --once   # watch Gemini navigate
    python scripts/sync_portfolio.py --once             # headless, for cron
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = _ROOT / "portfolio.json"


def _load_portfolio() -> dict:
    return json.loads(PORTFOLIO_PATH.read_text()) if PORTFOLIO_PATH.exists() else {"holdings": [], "transactions": []}


def _build_task(url: str | None) -> str:
    target = url or "https://finance.yahoo.com/portfolios"
    return f"""\
You are a portfolio-extraction agent. Your ONLY goal is to extract Amit's
stock holdings from Yahoo Finance.

1. Open {target}
2. If not logged in, click Sign In. Use credentials from env vars YAHOO_USERNAME
   and YAHOO_PASSWORD. Handle 2FA if prompted (ask the human to approve).
3. Navigate to the user's personal portfolio (there may be one or many; pick the
   one with the most holdings, or one named "Amit").
4. For every holding in the table, extract:
     - symbol (e.g. GOOGL, VOO, NVDA)
     - company/fund name
     - quantity (number of shares)
     - current price (USD)
5. Return a STRICT JSON object (no prose, no markdown fences) with shape:
     {{"holdings": [{{"ticker": "GOOGL", "name": "Alphabet Inc", "quantity": 4.46, "current_price_usd": 170.12}}]}}
6. DO NOT navigate to any broker site. DO NOT click any "Trade" or "Buy" button
   even if Yahoo Finance shows one — the extraction is read-only.
"""


async def _run_browser_use(headful: bool, url: str | None) -> dict:
    try:
        from browser_use import Agent
        from browser_use.llm.google import ChatGoogle
    except ImportError as e:
        print(
            f"[error] browser-use not installed or incompatible: {e}\n"
            "        Install with:\n"
            "          pip install browser-use\n"
            "          playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(2)

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not gemini_key:
        print("[error] GEMINI_API_KEY not set (put it in .env)", file=sys.stderr)
        sys.exit(2)

    model_name = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    llm = ChatGoogle(model=model_name, api_key=gemini_key, temperature=0)

    agent = Agent(
        task=_build_task(url),
        llm=llm,
        use_vision=True,
    )

    result = await agent.run(max_steps=40 if headful else 30)

    # browser-use returns the last model output; try to pull JSON from it
    text = result.final_result() if hasattr(result, "final_result") else str(result)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"No JSON found in browser-use output:\n{text[:500]}")
    return json.loads(text[start:end + 1])


def _merge_into_portfolio(existing: dict, extracted: dict) -> dict:
    """
    Merge rules:
    - Quantities: always take the Yahoo value (source of truth).
    - Cost prices: keep existing (Yahoo doesn't store cost basis).
    - New tickers: add with current_price_usd as placeholder cost.
    - Missing tickers in Yahoo: keep in portfolio.json (user may still own them but
      has not added to Yahoo yet) — flag in notes.
    """
    by_ticker = {h["ticker"]: h for h in existing.get("holdings", [])}
    updated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    yahoo_tickers = set()
    for eh in extracted.get("holdings", []):
        tk = eh.get("ticker", "").upper()
        if not tk:
            continue
        yahoo_tickers.add(tk)
        qty = eh.get("quantity")
        price = eh.get("current_price_usd")

        if tk in by_ticker:
            h = by_ticker[tk]
            h["quantity"] = qty if qty is not None else h.get("quantity")
            # keep cost_price_usd; only overwrite if missing
            if not h.get("cost_price_usd") and price is not None:
                h["cost_price_usd"] = price
        else:
            # New holding — cost basis unknown; use current price as placeholder
            by_ticker[tk] = {
                "ticker": tk,
                "name": eh.get("name", tk),
                "quantity": qty,
                "cost_price_usd": price,
                "ai_recommendation": "-",
                "ai_rating": "-",
                "notes": "Auto-imported from Yahoo Finance. Cost price is placeholder — update with real basis when known.",
            }

    for tk, h in list(by_ticker.items()):
        if tk not in yahoo_tickers and not h.get("notes", "").startswith("Israeli ETF"):
            # Don't drop — just flag
            note = h.get("notes", "")
            if "(not in Yahoo portfolio)" not in note:
                h["notes"] = (note + " (not in Yahoo portfolio on last sync)").strip()

    existing["holdings"] = list(by_ticker.values())
    existing["last_updated"] = updated_at[:10]
    existing.setdefault("transactions", []).append({
        "date": updated_at[:10],
        "type": "sync",
        "description": f"Auto-synced from Yahoo Finance ({len(yahoo_tickers)} holdings)",
    })
    return existing


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run a single sync")
    ap.add_argument("--headful", action="store_true", help="Show the browser window (for debugging)")
    ap.add_argument("--url", help="Direct portfolio URL (optional)")
    ap.add_argument("--dry-run", action="store_true", help="Run browser-use but don't write portfolio.json")
    args = ap.parse_args()

    if not args.once:
        print("[error] pass --once (this script is a single-shot sync; the scheduler calls it daily)",
              file=sys.stderr)
        sys.exit(2)

    if args.headful:
        os.environ["BROWSER_USE_HEADLESS"] = "false"

    try:
        extracted = asyncio.run(_run_browser_use(args.headful, args.url))
    except Exception as e:
        print(f"[error] browser-use failed: {e}", file=sys.stderr)
        sys.exit(3)

    print(f"[info] extracted {len(extracted.get('holdings', []))} holdings from Yahoo Finance")

    if args.dry_run:
        print(json.dumps(extracted, indent=2))
        return

    existing = _load_portfolio()
    merged = _merge_into_portfolio(existing, extracted)
    PORTFOLIO_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    print(f"[ok] wrote {PORTFOLIO_PATH}")


if __name__ == "__main__":
    main()
