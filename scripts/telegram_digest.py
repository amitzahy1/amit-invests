#!/usr/bin/env python3
"""
Telegram digest — reads recommendations.json and pushes a rich summary to Telegram.

Sends up to three messages:
  1. Holdings with persona vote splits and dissenting opinions
  2. New ideas + portfolio dashboard with sector bar chart
  3. Portfolio value chart (PNG image, last 6 months)

Environment:
    TELEGRAM_BOT_TOKEN   from @BotFather
    TELEGRAM_CHAT_ID     your chat id

Usage:
    python scripts/telegram_digest.py --once         # send a one-shot digest
    python scripts/telegram_digest.py --strong-only  # only send if any STRONG verdict
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime
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

# Right-to-Left mark — forces correct bidi rendering for Hebrew after English
RLM = "\u200f"

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


def _load_snapshots(n: int = 0) -> list[dict]:
    """Read last N entries from snapshots.jsonl (0 = all)."""
    if not SNAPSHOTS_PATH.exists():
        return []
    text = SNAPSHOTS_PATH.read_text().strip()
    if not text:
        return []
    lines = text.split("\n")
    entries = [json.loads(line) for line in lines]
    return entries[-n:] if n > 0 else entries


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


def _sector_bar(sector_weights: dict, width: int = 10) -> str:
    """Build a Unicode bar chart from sector weights."""
    lines = []
    sorted_sectors = sorted(sector_weights.items(), key=lambda x: -x[1])
    for sector, weight in sorted_sectors:
        if weight < 2:
            continue
        short = SECTOR_SHORT.get(sector, sector)[:10].ljust(10)
        filled = max(1, round(weight / (100 / width))) if weight >= 2 else 0
        bar = "\u2588" * filled + "\u2591" * (width - filled)
        lines.append(f"`{short}` {bar} {weight:.0f}%")
    return "\n".join(lines)


# ─── Message Formatters ────────────────────────────────────────────────────

def _format_holdings_msg(recs: dict) -> str:
    """Message 1: header + data-driven summary + holdings with vote splits."""
    lines = []
    holdings = recs.get("holdings", [])
    new_ideas = recs.get("new_ideas", [])

    # Header
    date_str = recs.get("updated", "")[:10]
    lines.append(f"📊 *Portfolio Digest — {date_str}*")
    lines.append("")

    # Data-driven Key Takeaways (avoids RTL/LTR mixing)
    buy_count = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "buy")
    sell_count = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "sell")
    hold_count = len(holdings) - buy_count - sell_count

    lines.append("*סיכום*")
    lines.append(f"🟢 {buy_count} קנייה  ·  🔴 {sell_count} מכירה  ·  🟡 {hold_count} החזקה")

    # Highlight sells
    sells = [h for h in holdings if (h.get("verdict") or "").lower() == "sell"]
    if sells:
        sell_tickers = ", ".join(f"`{h['ticker']}`" for h in sells)
        lines.append(f"{RLM}⚠️ מכירה: {sell_tickers}")

    # Highlight strong buys (>=90%)
    strong_buys = [h for h in holdings
                   if (h.get("verdict") or "").lower() == "buy" and h.get("conviction", 0) >= 90]
    if strong_buys:
        sb_tickers = ", ".join(f"`{h['ticker']}`" for h in strong_buys)
        lines.append(f"{RLM}🔥 שכנוע גבוה: {sb_tickers}")

    # New ideas teaser
    if new_ideas:
        idea_tickers = ", ".join(f"`{i['ticker']}`" for i in new_ideas)
        lines.append(f"{RLM}💡 רעיונות חדשים: {idea_tickers}")

    lines.append("")

    # Holdings with vote split + skeptic
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
                # Use Hebrew display_name for RTL-friendly rendering
                d_display = dissenter.get("display_name", "")
                d_verdict_heb = {"buy": "קנייה", "sell": "מכירה", "hold": "החזקה"}.get(
                    (dissenter.get("verdict") or "hold").lower(), "החזקה"
                )
                d_conv = dissenter.get("conviction", 0)
                d_rationale = _truncate(dissenter.get("rationale", ""), 50)
                lines.append(
                    f"   {RLM}⚠️ _{d_display} ({d_verdict_heb} {d_conv}%): {d_rationale}_"
                )

    return "\n".join(lines)


def _format_dashboard_msg(recs: dict, snapshots: list[dict]) -> str:
    """Message 2: new ideas + portfolio dashboard + sector bar chart."""
    lines = []

    # New Ideas
    new_ideas = recs.get("new_ideas", [])
    if new_ideas:
        lines.append("*רעיונות חדשים*")
        for idea in new_ideas:
            ticker = idea.get("ticker", "")
            name = idea.get("name", "")
            conv = idea.get("conviction", 0)
            lines.append(f"🚀 `{ticker}` — {name} ({conv}%)")
            rationale = _truncate(idea.get("rationale", ""), 70)
            if rationale:
                lines.append(f"   {RLM}_{rationale}_")
        lines.append("")

    # Portfolio Dashboard (from snapshots)
    if snapshots:
        latest = snapshots[-1]
        lines.append("*תיק השקעות*")

        val_usd = latest.get("value_usd", 0)
        val_ils = latest.get("value_ils", 0)
        usd_ils = latest.get("usd_ils", 0)

        # Daily change
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
            lines.append("*סקטורים*")
            lines.append(_sector_bar(sector_weights))
            lines.append("")

    lines.append(f"{RLM}_סקירת שוק — אינה המלצה פיננסית._")
    return "\n".join(lines)


def _generate_chart(snapshots: list[dict]) -> bytes | None:
    """Generate a portfolio value chart PNG from snapshot history."""
    if len(snapshots) < 3:
        return None  # need at least 3 data points for a meaningful chart

    try:
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("[warn] matplotlib not installed — skipping chart", file=sys.stderr)
        return None

    dates = []
    values_usd = []
    values_ils = []

    for s in snapshots:
        try:
            d = datetime.strptime(s["date"], "%Y-%m-%d")
            dates.append(d)
            values_usd.append(s.get("value_usd", 0))
            values_ils.append(s.get("value_ils", 0))
        except (KeyError, ValueError):
            continue

    if len(dates) < 3:
        return None

    # Dark theme matching the app
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    fig.patch.set_facecolor("#0a0a0a")
    ax.set_facecolor("#0a0a0a")

    # Plot USD value
    ax.plot(dates, values_usd, color="#22c55e", linewidth=2, label="USD")
    ax.fill_between(dates, values_usd, alpha=0.15, color="#22c55e")

    # Labels and formatting
    ax.set_title("Portfolio Value (USD)", color="white", fontsize=14, fontweight="bold", pad=12)
    ax.tick_params(colors="#94a3b8", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#334155")
    ax.spines["bottom"].set_color("#334155")
    ax.grid(axis="y", color="#1e293b", linewidth=0.5)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=10))
    plt.xticks(rotation=30)

    # Latest value annotation
    if values_usd:
        latest_val = values_usd[-1]
        ax.annotate(
            f"${latest_val:,.0f}",
            xy=(dates[-1], latest_val),
            xytext=(10, 10),
            textcoords="offset points",
            color="#22c55e",
            fontsize=11,
            fontweight="bold",
        )

    fig.tight_layout()

    # Save to bytes
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


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


def send_telegram_photo(photo_bytes: bytes, caption: str = "") -> None:
    """Send a photo (PNG bytes) to Telegram via sendPhoto API."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return

    import urllib.request

    boundary = "----TelegramBoundary"
    body_parts = []

    # chat_id field
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
    body_parts.append(f"{chat_id}\r\n".encode())

    # caption field
    if caption:
        body_parts.append(f"--{boundary}\r\n".encode())
        body_parts.append(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
        body_parts.append(f"{caption}\r\n".encode())

    # photo file
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(b'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n')
    body_parts.append(b"Content-Type: image/png\r\n\r\n")
    body_parts.append(photo_bytes)
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())

    body = b"".join(body_parts)

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                print(f"[warn] sendPhoto returned {resp.status}", file=sys.stderr)
    except Exception as e:
        print(f"[warn] sendPhoto failed: {e}", file=sys.stderr)


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

    all_snapshots = _load_snapshots(0)  # all for chart
    recent_snapshots = all_snapshots[-2:] if len(all_snapshots) >= 2 else all_snapshots

    # Message 1: Holdings
    msg1 = _format_holdings_msg(recs)
    send_telegram(msg1)
    print("[ok] holdings message sent")

    # Message 2: Dashboard
    msg2 = _format_dashboard_msg(recs, recent_snapshots)
    send_telegram(msg2)
    print("[ok] dashboard message sent")

    # Message 3: Chart (only if enough history)
    chart_bytes = _generate_chart(all_snapshots)
    if chart_bytes:
        send_telegram_photo(chart_bytes, "📈 Portfolio — last 6 months")
        print("[ok] chart sent")
    else:
        print(f"[info] chart skipped — need ≥3 snapshots (have {len(all_snapshots)})")


if __name__ == "__main__":
    main()
