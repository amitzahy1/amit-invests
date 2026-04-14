"""
Interactive Telegram Bot — responds to commands for on-demand analysis.

Commands:
  /check NVDA     — instant analysis for any ticker
  /scores         — show current scores for all holdings
  /rebalance      — show drift from target + suggestions
  /performance    — portfolio performance summary
  /help           — list available commands

Run: python scripts/telegram_bot.py
Requires: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
RLM = "\u200f"


def _send(text: str) -> None:
    """Send a Telegram message."""
    requests.post(f"{API_BASE}/sendMessage", data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }, timeout=15, verify=False)


def _get_updates(offset: int = 0) -> list[dict]:
    """Poll for new messages."""
    try:
        resp = requests.get(f"{API_BASE}/getUpdates", params={
            "offset": offset, "timeout": 30,
        }, timeout=35, verify=False)
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except Exception:
        pass
    return []


# ─── Command handlers ────────────────────────────────────────────────────────

def _cmd_check(ticker: str) -> str:
    """Instant analysis for a ticker."""
    ticker = ticker.upper().strip()
    if not ticker:
        return "Usage: `/check NVDA`"

    try:
        from data_loader import fetch_live_quotes, fetch_historical_data
        from data_loader_fundamentals import fetch_fundamentals, fetch_news_headlines
        from data_loader_macro import fetch_macro_snapshot
        from scoring_engine import compute_all_scores, scores_to_verdict, explain_scores
        from config import DISPLAY_NAMES, ASSET_TYPE_MAP

        # Fetch data
        quotes_df = fetch_live_quotes([ticker])
        if quotes_df.empty:
            return f"Could not fetch data for `{ticker}`. Check the ticker symbol."

        quote = quotes_df.iloc[0].to_dict()
        hist = fetch_historical_data([ticker], period="1y")

        # Technicals
        from scripts.run_recommendations import _compute_technicals
        technicals = _compute_technicals(hist.get(ticker))

        # Fundamentals
        fund = fetch_fundamentals(ticker)

        # Macro
        macro = fetch_macro_snapshot()

        # News
        news = fetch_news_headlines(ticker)

        # Score
        scores = compute_all_scores(
            ticker, quote, technicals, fund, macro, news,
            0, 0, ASSET_TYPE_MAP.get(ticker, ""), 10)

        # Load settings for weights
        settings = json.loads((_ROOT / "settings.json").read_text()) if (_ROOT / "settings.json").exists() else {}
        weights = settings.get("scoring_weights")
        verdict, conviction = scores_to_verdict(scores, weights)

        # Explain
        details = explain_scores(scores, quote, technicals, fund, macro, 0, 0)

        # Format
        name = DISPLAY_NAMES.get(ticker, ticker)
        price = quote.get("price", 0)
        change = quote.get("daily_change_pct", 0)

        lines = [
            f"*{ticker}* — {name}",
            f"`${price:.2f}` ({change:+.1f}%)",
            "",
            f"*{verdict.upper()}* {conviction}%",
            "",
        ]

        LABELS = {"quality": "Quality", "valuation": "Valuation", "risk": "Risk",
                  "macro": "Macro", "sentiment": "Sentiment", "technical": "Trend"}
        for key in ["quality", "valuation", "risk", "macro", "sentiment", "technical"]:
            val = scores.get(key, 50)
            signal = "+" if val > 60 else "-" if val < 40 else "="
            lines.append(f"`{signal} {LABELS[key]:10s} {val:3d}`")
            # Add first explanation bullet
            reasons = details.get(key, [])
            if reasons:
                lines.append(f"  _{reasons[0]}_")

        return "\n".join(lines)

    except Exception as e:
        return f"Error analyzing `{ticker}`: {str(e)[:100]}"


def _cmd_scores() -> str:
    """Show current scores for all holdings."""
    recs_path = _ROOT / "recommendations.json"
    if not recs_path.exists():
        return "No recommendations available. Run analysis first."

    recs = json.loads(recs_path.read_text())
    lines = ["*Current Scores*", ""]

    for h in sorted(recs.get("holdings", []), key=lambda x: -x.get("conviction", 0)):
        tk = h.get("ticker", "")
        v = h.get("verdict", "hold").upper()
        c = h.get("conviction", 0)
        scores = h.get("scores", {})
        emoji = {"BUY": "🟢", "SELL": "🔴"}.get(v, "🟡")

        if scores:
            top3 = sorted(scores.items(), key=lambda x: -x[1])[:3]
            s_str = " ".join(f"{k[:3].upper()}{v}" for k, v in top3)
        else:
            s_str = ""

        lines.append(f"{emoji} `{tk}` *{v}* {c}% {s_str}")

    return "\n".join(lines)


def _cmd_rebalance() -> str:
    """Show rebalancing suggestions."""
    try:
        portfolio = json.loads((_ROOT / "portfolio.json").read_text())
        settings = json.loads((_ROOT / "settings.json").read_text()) if (_ROOT / "settings.json").exists() else {}

        from rebalancing import compute_drift, suggest_trades

        # Get current sector weights from latest snapshot
        snapshots_path = _ROOT / "snapshots.jsonl"
        sector_weights = {}
        if snapshots_path.exists():
            last_line = snapshots_path.read_text().strip().splitlines()[-1]
            snap = json.loads(last_line)
            sector_weights = snap.get("sector_weights", {})
            total_value = snap.get("value_usd", 0)
        else:
            total_value = 0

        drift = compute_drift(sector_weights)
        trades = suggest_trades(drift, total_value)

        if not trades:
            return "Portfolio is well-balanced. No trades needed."

        lines = ["*Rebalancing Suggestions*", ""]
        for t in trades[:5]:
            emoji = "🔴" if t["action"] == "SELL" else "🟢"
            amount = f" (~${t['amount_usd']:,.0f})" if t["amount_usd"] > 10 else ""
            lines.append(f"{emoji} *{t['action']}* {t['sector']}: {t['drift_pct']:+.1f}pp{amount}")
            lines.append(f"  {RLM}_{t['description_he']}_")

        return "\n".join(lines)

    except Exception as e:
        return f"Error: {str(e)[:100]}"


def _cmd_performance() -> str:
    """Portfolio performance summary."""
    snapshots_path = _ROOT / "snapshots.jsonl"
    if not snapshots_path.exists():
        return "No snapshots available."

    lines_raw = snapshots_path.read_text().strip().splitlines()
    if not lines_raw:
        return "No snapshots available."

    latest = json.loads(lines_raw[-1])
    val_usd = latest.get("value_usd", 0)
    val_ils = latest.get("value_ils", 0)
    pnl = latest.get("pnl_usd", 0)
    pnl_pct = latest.get("pnl_pct", 0)
    usd_ils = latest.get("usd_ils", 0)
    sign = "+" if pnl >= 0 else ""

    lines = [
        "*Portfolio Performance*",
        "",
        f"💰 `${val_usd:,.0f}` / `₪{val_ils:,.0f}`",
        f"📈 PnL: `{sign}${pnl:,.0f} ({sign}{pnl_pct:.1f}%)`",
        f"💱 USD/ILS: `{usd_ils:.3f}`",
    ]

    # Add daily change if we have 2+ snapshots
    if len(lines_raw) >= 2:
        prev = json.loads(lines_raw[-2])
        delta = val_usd - prev.get("value_usd", val_usd)
        delta_pct = (delta / prev["value_usd"] * 100) if prev.get("value_usd") else 0
        sign_d = "+" if delta >= 0 else ""
        lines.append(f"📊 Daily: `{sign_d}${delta:,.0f} ({sign_d}{delta_pct:.1f}%)`")

    return "\n".join(lines)


def _cmd_help() -> str:
    return (
        "*Available Commands*\n\n"
        "`/check NVDA` — instant analysis for any ticker\n"
        "`/scores` — current scores for all holdings\n"
        "`/rebalance` — drift from target + suggestions\n"
        "`/performance` — portfolio value + P&L\n"
        "`/help` — this message"
    )


# ─── Main loop ───────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("[error] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", file=sys.stderr)
        sys.exit(2)

    print(f"[info] Telegram bot started. Listening for commands…")
    offset = 0

    while True:
        updates = _get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = (msg.get("text") or "").strip()
            chat = str(msg.get("chat", {}).get("id", ""))

            if chat != CHAT_ID:
                continue  # ignore messages from other chats

            if not text.startswith("/"):
                continue

            parts = text.split(maxsplit=1)
            cmd = parts[0].lower().split("@")[0]  # handle /check@botname
            arg = parts[1] if len(parts) > 1 else ""

            print(f"[cmd] {cmd} {arg}")

            if cmd == "/check":
                response = _cmd_check(arg)
            elif cmd == "/scores":
                response = _cmd_scores()
            elif cmd == "/rebalance":
                response = _cmd_rebalance()
            elif cmd == "/performance":
                response = _cmd_performance()
            elif cmd == "/help" or cmd == "/start":
                response = _cmd_help()
            else:
                response = f"Unknown command: `{cmd}`\nType `/help` for available commands."

            _send(response)

        if not updates:
            time.sleep(1)


if __name__ == "__main__":
    main()
