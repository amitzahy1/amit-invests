#!/usr/bin/env python3
"""
Telegram digest — reads recommendations.json and pushes a rich summary to Telegram.

Sends two messages:
  1. Holdings with persona vote splits and dissenting opinions
  2. New ideas + portfolio dashboard with sector bar chart

Environment:
    TELEGRAM_BOT_TOKEN   from @BotFather
    TELEGRAM_CHAT_ID     your chat id

Usage:
    python scripts/telegram_digest.py --once         # send a one-shot digest
    python scripts/telegram_digest.py --strong-only  # only send if any STRONG verdict
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass  # .env is optional

_ROOT = Path(__file__).resolve().parent.parent
RECS_PATH = _ROOT / "recommendations.json"
SETTINGS_PATH = _ROOT / "settings.json"
SNAPSHOTS_PATH = _ROOT / "snapshots.jsonl"

VERDICT_EMOJI = {"buy": "🟢", "sell": "🔴", "hold": "🟡"}

# Short names for sectors (must fit ~10 chars for bar chart alignment)
SECTOR_SHORT = {
    "Broad Market": "S&P/Nasdaq",
    "Broad Market (Israel)": "Israel Bnd",
    "Aerospace & Defense": "Defense",
    "Energy / Uranium": "Uranium",
    "Energy / Nuclear": "Nuclear",
    "Consumer Discretionary": "Consumer",
    "Insurance (Israel)": "Insurance",
}


# ─── Helpers ────────────────────────────────────────────────────────────────

def _load_json(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def _load_snapshots(n: int = 2) -> list[dict]:
    """Read last N entries from snapshots.jsonl."""
    if not SNAPSHOTS_PATH.exists():
        return []
    text = SNAPSHOTS_PATH.read_text().strip()
    if not text:
        return []
    lines = text.split("\n")
    return [json.loads(line) for line in lines[-n:]]


def _vote_split(personas: list[dict]) -> tuple[int, int, int]:
    """Count buy/hold/sell votes from persona list → (buy, hold, sell)."""
    b = h = s = 0
    for p in personas:
        v = (p.get("verdict") or "hold").lower()
        if v == "buy":
            b += 1
        elif v == "sell":
            s += 1
        else:
            h += 1
    return b, h, s


def _find_dissenter(personas: list[dict], majority: str) -> dict | None:
    """Find the highest-conviction persona who disagrees with the majority."""
    dissenters = [
        p for p in personas
        if (p.get("verdict") or "hold").lower() != majority.lower()
    ]
    if not dissenters:
        return None
    return max(dissenters, key=lambda p: p.get("conviction", 0))


def _truncate(text: str, max_len: int = 55) -> str:
    """Truncate text to fit one Telegram line."""
    if not text or len(text) <= max_len:
        return text or ""
    cut = text[:max_len].rfind(" ")
    if cut < 15:
        cut = max_len
    return text[:cut] + "…"


def _holding_emoji(verdict: str, conviction: int) -> str:
    """Emoji with conviction-aware nuance: weak BUY gets 🟡."""
    v = verdict.lower()
    if v == "sell":
        return "🔴"
    if v == "buy" and conviction >= 80:
        return "🟢"
    return "🟡"


def _dissenter_short_name(name: str) -> str:
    """Shorten persona name for the skeptic line."""
    short = {
        "warren_buffett": "Buffett",
        "charlie_munger": "Munger",
        "cathie_wood": "C. Wood",
        "peter_lynch": "Lynch",
        "michael_burry": "Burry",
        "risk_manager": "Risk Mgr",
        "technical_analyst": "Technical",
        "fundamentals_analyst": "Fundament.",
        "sentiment": "Sentiment",
        "macro": "Macro",
        "ben_graham": "Graham",
        "valuation": "Valuation",
    }
    return short.get(name, name)


def _sector_bar(sector_weights: dict, width: int = 10) -> str:
    """Build a Unicode bar chart from sector weights."""
    lines = []
    sorted_sectors = sorted(sector_weights.items(), key=lambda x: -x[1])
    for sector, weight in sorted_sectors:
        if weight < 2:
            continue  # skip tiny sectors
        short = SECTOR_SHORT.get(sector, sector)[:10].ljust(10)
        filled = max(1, round(weight / (100 / width))) if weight >= 2 else 0
        bar = "\u2588" * filled + "\u2591" * (width - filled)
        lines.append(f"`{short}` {bar} {weight:.0f}%")
    return "\n".join(lines)


def _summary_bullets(summary: str, max_bullets: int = 3) -> list[str]:
    """Split the Hebrew summary paragraph into short bullet points."""
    if not summary:
        return []
    # Split on period followed by space (Hebrew sentence boundary)
    parts = [s.strip() for s in summary.replace(". ", ".\n").split("\n") if s.strip()]
    bullets = []
    for p in parts[:max_bullets]:
        # Remove trailing period for cleaner bullet
        p = p.rstrip(".")
        bullets.append(f" • {_truncate(p, 80)}")
    return bullets


# ─── Message Formatters ────────────────────────────────────────────────────

def _format_holdings_msg(recs: dict) -> str:
    """Message 1: header + key takeaways + holdings with vote splits."""
    lines = []

    # Header
    date_str = recs.get("updated", "")[:10]
    lines.append(f"📊 *Portfolio Digest — {date_str}*")
    lines.append("")

    # Key Takeaways (summary → bullets)
    summary = recs.get("summary", "")
    bullets = _summary_bullets(summary)
    if bullets:
        lines.append("*Key Takeaways*")
        lines.extend(bullets)
        lines.append("")

    # Holdings with vote split + skeptic
    holdings = recs.get("holdings", [])
    if holdings:
        lines.append(f"*Holdings* ({len(holdings)})")
        for h in holdings:
            verdict = (h.get("verdict") or "hold").lower()
            conviction = h.get("conviction", 0)
            ticker = h.get("ticker", "")
            personas = h.get("personas", [])

            emoji = _holding_emoji(verdict, conviction)

            if personas:
                b, ho, s = _vote_split(personas)
                vote_str = f" · {b}-{ho}-{s}"
            else:
                vote_str = ""

            lines.append(
                f"{emoji} `{ticker}` *{verdict.upper()}* {conviction}%{vote_str}"
            )

            # Skeptic line: only when not unanimous
            if personas and (b < len(personas) and ho < len(personas) and s < len(personas)):
                dissenter = _find_dissenter(personas, verdict)
                if dissenter:
                    d_name = _dissenter_short_name(dissenter.get("name", ""))
                    d_verdict = (dissenter.get("verdict") or "hold").upper()
                    d_conv = dissenter.get("conviction", 0)
                    d_rationale = _truncate(dissenter.get("rationale", ""), 50)
                    lines.append(
                        f"   ⚠️ _{d_name} ({d_verdict} {d_conv}%): {d_rationale}_"
                    )

    return "\n".join(lines)


def _format_dashboard_msg(recs: dict, snapshots: list[dict]) -> str:
    """Message 2: new ideas + portfolio dashboard + sector bar chart."""
    lines = []

    # New Ideas
    new_ideas = recs.get("new_ideas", [])
    if new_ideas:
        lines.append("*New Ideas*")
        for idea in new_ideas:
            ticker = idea.get("ticker", "")
            name = idea.get("name", "")
            conv = idea.get("conviction", 0)
            lines.append(f"🚀 `{ticker}` — {name} ({conv}%)")
            rationale = _truncate(idea.get("rationale", ""), 70)
            if rationale:
                lines.append(f"   _{rationale}_")
        lines.append("")

    # Portfolio Dashboard (from snapshots)
    if snapshots:
        latest = snapshots[-1]
        lines.append("*Portfolio*")

        val_usd = latest.get("value_usd", 0)
        val_ils = latest.get("value_ils", 0)
        usd_ils = latest.get("usd_ils", 0)

        # Daily change (compare to previous snapshot)
        if len(snapshots) >= 2:
            prev = snapshots[-2]
            delta_usd = val_usd - prev.get("value_usd", val_usd)
            delta_pct = (delta_usd / prev["value_usd"] * 100) if prev.get("value_usd") else 0
            sign = "+" if delta_usd >= 0 else ""
            lines.append(f"💰 `${val_usd:,.0f}` ({sign}${delta_usd:,.0f} / {sign}{delta_pct:.1f}%)")
        else:
            lines.append(f"💰 `${val_usd:,.0f}`")

        lines.append(f"   `₪{val_ils:,.0f}` · USD/ILS {usd_ils:.3f}")

        # P&L
        pnl_usd = latest.get("pnl_usd", 0)
        pnl_pct = latest.get("pnl_pct", 0)
        sign = "+" if pnl_usd >= 0 else ""
        lines.append(f"📈 `PnL: {sign}${pnl_usd:,.0f} ({sign}{pnl_pct:.1f}%)`")
        lines.append("")

        # Sector bar chart
        sector_weights = latest.get("sector_weights", {})
        if sector_weights:
            lines.append("*Sectors*")
            lines.append(_sector_bar(sector_weights))
            lines.append("")

    lines.append("_Market commentary — not financial advice._")
    return "\n".join(lines)


# ─── Send ───────────────────────────────────────────────────────────────────

def _should_send(recs: dict, strong_only: bool) -> bool:
    if not strong_only:
        return True
    for h in recs.get("holdings", []):
        if (h.get("verdict") or "").lower() in ("buy", "sell") and int(h.get("conviction", 0)) >= 75:
            return True
    return False


def send_telegram(text: str) -> None:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        print("[error] TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set "
              "(via environment or .env)", file=sys.stderr)
        sys.exit(2)

    import urllib.request
    import urllib.parse

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode()

    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
            if resp.status != 200:
                print(f"[error] Telegram API returned {resp.status}: {body}", file=sys.stderr)
                sys.exit(3)
    except Exception as e:
        print(f"[error] Telegram request failed: {e}", file=sys.stderr)
        sys.exit(3)


# ─── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Send one digest and exit")
    ap.add_argument("--strong-only", action="store_true",
                    help="Only send if any verdict is BUY/SELL with >=75% conviction")
    args = ap.parse_args()

    recs = _load_json(RECS_PATH)
    if not recs:
        print(f"[error] {RECS_PATH} not found — run scripts/run_recommendations.py first",
              file=sys.stderr)
        sys.exit(2)

    settings = _load_json(SETTINGS_PATH)
    tg_cfg = (settings or {}).get("telegram", {})
    if not tg_cfg.get("enabled", True):
        print("[info] telegram.enabled=false in settings.json — skipping")
        return

    if not _should_send(recs, args.strong_only):
        print("[info] no strong verdicts — skipping (use without --strong-only to force)")
        return

    snapshots = _load_snapshots(2)

    msg1 = _format_holdings_msg(recs)
    msg2 = _format_dashboard_msg(recs, snapshots)

    send_telegram(msg1)
    print("[ok] holdings message sent")
    send_telegram(msg2)
    print("[ok] dashboard message sent")


if __name__ == "__main__":
    main()
