#!/usr/bin/env python3
"""
Telegram digest — reads recommendations.json and pushes a rich summary to Telegram.

Sends up to N+2 messages:
  1. Holdings with persona vote splits and dissenting opinions
  2. New ideas (full rationale) + portfolio dashboard with sector bar chart
  3+. Candlestick chart for each new-idea ticker (OHLCV + MA20/MA50)

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

# Sectors that are "boring" index funds — excluded from high-conviction highlight
BROAD_MARKET_SECTORS = {"Broad Market", "Broad Market (Israel)"}

# Sector map for new-idea tickers (not in config.py since they're suggestions)
NEW_IDEA_SECTORS = {
    "MSFT": "Technology",
    "CEG": "Energy / Nuclear",
    "UNH": "Healthcare",
    "PLTR": "Technology",
    "LLY": "Healthcare",
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


def _get_sector(ticker: str) -> str:
    """Get sector for a ticker (from config.py or local fallback)."""
    try:
        sys.path.insert(0, str(_ROOT))
        from config import SECTOR_MAP
        return SECTOR_MAP.get(ticker, NEW_IDEA_SECTORS.get(ticker, "Other"))
    except Exception:
        return NEW_IDEA_SECTORS.get(ticker, "Other")


# ─── Yahoo Finance (lightweight, for chart data) ───────────────────────────

def _fetch_ohlcv(ticker: str, range_: str = "6mo") -> dict | None:
    """Fetch OHLCV data from Yahoo Finance for candlestick charts."""
    import requests
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"range": range_, "interval": "1d"}
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                         params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            result = data.get("chart", {}).get("result")
            if result:
                return result[0]
    except Exception as e:
        print(f"[warn] failed to fetch {ticker} OHLCV: {e}", file=sys.stderr)
    return None


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

    # Data-driven Key Takeaways
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

    # Top picks: BUY >=80% — exclude broad market ETFs (index funds aren't picks)
    top_picks = [
        h for h in holdings
        if (h.get("verdict") or "").lower() == "buy"
        and h.get("conviction", 0) >= 80
        and _get_sector(h.get("ticker", "")) not in BROAD_MARKET_SECTORS
    ]
    if top_picks:
        tp_tickers = ", ".join(f"`{h['ticker']}`" for h in top_picks)
        lines.append(f"{RLM}🎯 Top Picks: {tp_tickers}")

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
    """Message 2: new ideas (full rationale) + portfolio dashboard."""
    lines = []

    # New Ideas — FULL rationale, not truncated
    new_ideas = recs.get("new_ideas", [])
    if new_ideas:
        lines.append("*רעיונות חדשים*")
        for idea in new_ideas:
            ticker = idea.get("ticker", "")
            name = idea.get("name", "")
            conv = idea.get("conviction", 0)
            lines.append(f"🚀 `{ticker}` — {name} ({conv}%)")
            rationale = idea.get("rationale", "")
            if rationale:
                lines.append(f"{RLM}_{rationale}_")
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


def _generate_candlestick(ticker: str, name: str, conviction: int,
                          verdict: str = "BUY") -> bytes | None:
    """Generate a professional candlestick chart with MA20/MA50 for a ticker."""
    data = _fetch_ohlcv(ticker, range_="6mo")
    if not data:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import pandas as pd
        import numpy as np
    except ImportError:
        return None

    timestamps = data.get("timestamp", [])
    quote = data.get("indicators", {}).get("quote", [{}])[0]
    if not timestamps or not quote.get("close"):
        return None

    dates = pd.to_datetime(timestamps, unit="s")
    df = pd.DataFrame({
        "open": quote.get("open", []),
        "high": quote.get("high", []),
        "low": quote.get("low", []),
        "close": quote.get("close", []),
        "volume": quote.get("volume", []),
    }, index=dates).dropna(subset=["close"])

    if len(df) < 20:
        return None

    # Moving averages
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()

    # ─── Chart ──────────────────────────────────────────────────────
    fig, (ax_price, ax_vol) = plt.subplots(
        2, 1, figsize=(10, 6), dpi=150,
        gridspec_kw={"height_ratios": [3, 1]}, sharex=True,
    )
    fig.patch.set_facecolor("#0f1117")

    for ax in (ax_price, ax_vol):
        ax.set_facecolor("#0f1117")
        ax.tick_params(colors="#94a3b8", labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#1e293b")
        ax.spines["bottom"].set_color("#1e293b")
        ax.grid(axis="y", color="#1e293b", linewidth=0.4)

    # Candlesticks
    width = 0.6  # days
    up = df[df["close"] >= df["open"]]
    down = df[df["close"] < df["open"]]

    # Up candles (green)
    ax_price.bar(up.index, up["close"] - up["open"], width, bottom=up["open"],
                 color="#22c55e", edgecolor="#22c55e", linewidth=0.5)
    ax_price.vlines(up.index, up["low"], up["high"], color="#22c55e", linewidth=0.5)

    # Down candles (red)
    ax_price.bar(down.index, down["close"] - down["open"], width, bottom=down["open"],
                 color="#ef4444", edgecolor="#ef4444", linewidth=0.5)
    ax_price.vlines(down.index, down["low"], down["high"], color="#ef4444", linewidth=0.5)

    # Moving averages
    ax_price.plot(df.index, df["ma20"], color="#3b82f6", linewidth=1.2, label="MA20", alpha=0.9)
    if df["ma50"].notna().sum() > 0:
        ax_price.plot(df.index, df["ma50"], color="#f59e0b", linewidth=1.2, label="MA50", alpha=0.9)

    # Price annotation
    last_price = df["close"].iloc[-1]
    ax_price.annotate(
        f"${last_price:,.2f}",
        xy=(df.index[-1], last_price),
        xytext=(8, 8), textcoords="offset points",
        color="white", fontsize=10, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#1e293b", edgecolor="#334155"),
    )

    # Period change
    first_price = df["close"].iloc[0]
    change_pct = ((last_price / first_price) - 1) * 100
    change_color = "#22c55e" if change_pct >= 0 else "#ef4444"
    change_sign = "+" if change_pct >= 0 else ""

    ax_price.set_title(
        f"{ticker} — {name}   |   {change_sign}{change_pct:.1f}% (6M)   |   {verdict.upper()} {conviction}%",
        color="white", fontsize=13, fontweight="bold", pad=10, loc="left",
    )
    # Color the change in title
    ax_price.text(
        0.98, 1.02, f"{change_sign}{change_pct:.1f}%",
        transform=ax_price.transAxes, ha="right", va="bottom",
        color=change_color, fontsize=12, fontweight="bold",
    )

    ax_price.legend(loc="upper left", fontsize=8, framealpha=0.3,
                    facecolor="#1e293b", edgecolor="#334155", labelcolor="white")
    ax_price.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    # Volume bars
    vol_colors = ["#22c55e" if c >= o else "#ef4444"
                  for c, o in zip(df["close"], df["open"])]
    ax_vol.bar(df.index, df["volume"], width, color=vol_colors, alpha=0.5)
    ax_vol.set_ylabel("Volume", color="#64748b", fontsize=8)
    ax_vol.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: f"{x/1e6:.0f}M" if x >= 1e6 else f"{x/1e3:.0f}K"
    ))

    ax_vol.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_vol.xaxis.set_major_locator(mdates.MonthLocator())
    plt.xticks(rotation=0)

    fig.tight_layout()
    fig.subplots_adjust(hspace=0.05)

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

    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(b'Content-Disposition: form-data; name="chat_id"\r\n\r\n')
    body_parts.append(f"{chat_id}\r\n".encode())

    if caption:
        body_parts.append(f"--{boundary}\r\n".encode())
        body_parts.append(b'Content-Disposition: form-data; name="caption"\r\n\r\n')
        body_parts.append(f"{caption}\r\n".encode())

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

    recent_snapshots = _load_snapshots(2)

    # Message 1: Holdings
    msg1 = _format_holdings_msg(recs)
    send_telegram(msg1)
    print("[ok] holdings message sent")

    # Message 2: Dashboard + New Ideas (full rationale)
    msg2 = _format_dashboard_msg(recs, recent_snapshots)
    send_telegram(msg2)
    print("[ok] dashboard message sent")

    # Messages 3+: Candlestick charts
    # 1) Existing holdings with BUY >=80% (excluding broad market ETFs + Israeli)
    chart_items = []
    holdings = recs.get("holdings", [])
    for h in holdings:
        v = (h.get("verdict") or "").lower()
        c = h.get("conviction", 0)
        tk = h.get("ticker", "")
        sector = _get_sector(tk)
        if (v == "buy" and c >= 80
                and sector not in BROAD_MARKET_SECTORS
                and not tk.endswith(".TA")):
            chart_items.append((tk, h.get("name", tk), c, "BUY"))

    # 2) New ideas
    for idea in recs.get("new_ideas", []):
        tk = idea.get("ticker", "")
        if tk not in {ci[0] for ci in chart_items}:  # avoid duplicates
            chart_items.append((tk, idea.get("name", tk), idea.get("conviction", 0), "BUY"))

    if chart_items:
        print(f"[info] generating {len(chart_items)} charts…")
    for ticker, name, conv, verdict in chart_items:
        print(f"  {ticker}…", end=" ", flush=True)
        chart = _generate_candlestick(ticker, name, conv, verdict)
        if chart:
            send_telegram_photo(chart, f"📊 {ticker} — {name} | {verdict} {conv}%")
            print("sent")
        else:
            print("skipped (no data)")


if __name__ == "__main__":
    main()
