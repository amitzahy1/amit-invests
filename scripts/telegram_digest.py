#!/usr/bin/env python3
"""
Telegram digest — reads recommendations.json and pushes a rich summary to Telegram.

Sends up to N+2 messages:
  1. Holdings with scores and verdicts
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

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    "Fixed Income (Israel)": "Israel Bnd",
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


# ─── New Mentor Blocks ────────────────────────────────────────────────────

def _format_market_context() -> str:
    """Block 1: Today's market snapshot — S&P, Nasdaq, VIX, rates, USD/ILS."""
    try:
        sys.path.insert(0, str(_ROOT))
        from data_loader_macro import fetch_macro_snapshot
        m = fetch_macro_snapshot()
    except Exception:
        return ""
    if not m or not m.get("vix"):
        return ""

    lines = ["📊 *שוק היום*"]

    idx_parts = []
    if m.get("sp500_change") is not None:
        idx_parts.append(f"S&P 500 {m['sp500_change']:+.1f}%")
    if m.get("nasdaq_change") is not None:
        idx_parts.append(f"Nasdaq {m['nasdaq_change']:+.1f}%")
    if m.get("vix") is not None:
        fear = "פחד" if m["vix"] > 25 else "נמוך" if m["vix"] < 15 else "נורמלי"
        idx_parts.append(f"VIX {m['vix']:.0f} ({fear})")
    if idx_parts:
        lines.append("`" + "  ·  ".join(idx_parts) + "`")

    rate_parts = []
    if m.get("fed_rate") is not None:
        rate_parts.append(f"Fed {m['fed_rate']:.2f}%")
    if m.get("ten_year_yield") is not None:
        rate_parts.append(f"10Y {m['ten_year_yield']:.2f}%")
    if m.get("usd_ils") is not None:
        rate_parts.append(f"USD/ILS {m['usd_ils']:.3f}")
    if rate_parts:
        lines.append("`" + "  ·  ".join(rate_parts) + "`")

    return "\n".join(lines)


def _format_daily_lesson(recs: dict) -> str:
    """Block 2: Rotating daily financial lesson with portfolio examples."""
    lessons_path = _ROOT / "lessons.json"
    if not lessons_path.exists():
        return ""
    try:
        lessons = json.loads(lessons_path.read_text())
    except Exception:
        return ""
    if not lessons:
        return ""

    from datetime import datetime
    day = datetime.now().timetuple().tm_yday
    lesson = lessons[day % len(lessons)]

    title = lesson.get("title_he", "")
    body = lesson.get("body_he", "")
    if not title:
        return ""

    idx = lesson.get("id", day % len(lessons) + 1)
    lines = [
        f"📚 *שיעור יומי #{idx}: {title}*",
        f"{RLM}_{body}_",
    ]

    # Try to personalise with portfolio data
    example_tpl = lesson.get("example_template", "")
    if example_tpl:
        try:
            from data_loader_fundamentals import load_fundamentals_cache
            cache = load_fundamentals_cache()
            tickers_data = cache.get("tickers", {})
            relevant = lesson.get("tickers_relevant", [])
            for tk in relevant:
                if tk in tickers_data:
                    fd = tickers_data[tk]
                    example = example_tpl.format(
                        ticker=tk,
                        pe=fd.get("pe", "N/A"),
                        peg=fd.get("peg", "N/A"),
                        roe=fd.get("roe", "N/A"),
                        margin=fd.get("profit_margin", "N/A"),
                        sector_pe="22",
                    )
                    lines.append(f"{RLM}📌 _{example}_")
                    break
        except Exception:
            pass

    return "\n".join(lines)


def _format_changes(recs: dict) -> str:
    """Block 3: Verdict changes compared to previous run."""
    prev_path = _ROOT / "recommendations_prev.json"
    if not prev_path.exists():
        return ""
    try:
        prev = json.loads(prev_path.read_text())
    except Exception:
        return ""

    prev_map = {h["ticker"]: h for h in prev.get("holdings", [])}
    changes = []
    for h in recs.get("holdings", []):
        tk = h.get("ticker", "")
        if tk not in prev_map:
            continue
        old_v = (prev_map[tk].get("verdict") or "hold").lower()
        new_v = (h.get("verdict") or "hold").lower()
        old_c = prev_map[tk].get("conviction", 0)
        new_c = h.get("conviction", 0)
        if old_v != new_v or abs(old_c - new_c) >= 10:
            arrow = "⬆️" if new_c > old_c else "⬇️"
            changes.append(
                f"{arrow} `{tk}` {old_v.upper()} {old_c}% → {new_v.upper()} {new_c}%"
            )
    if not changes:
        return ""
    return "*🔄 שינויים מאתמול:*\n" + "\n".join(changes)


def _format_ideas_scorecard() -> str:
    """Block 4: Performance of past suggested ideas."""
    hist_path = _ROOT / "ideas_history.json"
    if not hist_path.exists():
        return ""
    try:
        history = json.loads(hist_path.read_text())
    except Exception:
        return ""
    if not history:
        return ""

    import requests
    lines = ["*💡 כרטיס ציון — רעיונות קודמים:*"]
    hits = 0
    total = 0
    for idea in history[-6:]:  # last 6 ideas
        tk = idea.get("ticker", "")
        suggested_price = idea.get("suggested_price", 0)
        date = idea.get("suggested_date", "?")
        if not tk or not suggested_price:
            continue
        # Fetch current price
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}",
                params={"range": "1d", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8, verify=False,
            )
            if r.status_code == 200:
                current = r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
                pct = ((current / suggested_price) - 1) * 100
                emoji = "✅" if pct > 0 else "❌"
                lines.append(f"{emoji} `{tk}` ({date}): {pct:+.1f}%")
                total += 1
                if pct > 0:
                    hits += 1
        except Exception:
            continue

    if total == 0:
        return ""
    rate = hits / total * 100
    lines.append(f"\n{RLM}_Hit rate: {hits}/{total} ({rate:.0f}%)_")
    return "\n".join(lines)


# ─── Yahoo Finance (lightweight, for chart data) ───────────────────────────

def _fetch_ohlcv(ticker: str, range_: str = "6mo") -> dict | None:
    """Fetch OHLCV data from Yahoo Finance for candlestick charts."""
    import requests
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"range": range_, "interval": "1d"}
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                         params=params, timeout=15, verify=False)
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

    # Market context (new: Phase 2)
    mkt_ctx = _format_market_context()
    if mkt_ctx:
        lines.append(mkt_ctx)
        lines.append("")

    # Smart Insights from senior analyst (1 Gemini call/day — deep analysis)
    insights = recs.get("smart_insights", {})
    if insights and insights.get("insights"):
        headline = insights.get("headline", "")
        body = insights.get("insights", "")
        lines.append("🧠 *Smart Analyst Brief*")
        if headline:
            lines.append(f"{RLM}*{headline}*")
        # Convert **bold** markers and truncate for Telegram
        body_tg = body.replace("**", "*")
        # Keep first ~500 chars for Telegram readability
        if len(body_tg) > 800:
            body_tg = body_tg[:800].rsplit(".", 1)[0] + "..."
        lines.append(f"{RLM}_{body_tg}_")
        lines.append("")
    elif recs.get("summary"):
        # Fallback: show the short summary
        summary = recs.get("summary", "")
        sentences = [s.strip() for s in summary.replace(". ", ".\n").split("\n") if s.strip()]
        short_summary = ". ".join(sentences[:2]).rstrip(".").replace("..", ".")
        lines.append(f"{RLM}_{short_summary}._")
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

    # Daily lesson (new: Phase 2)
    lesson = _format_daily_lesson(recs)
    if lesson:
        lines.append(lesson)
        lines.append("")

    # Sort holdings: 🟢 BUY >=80% → 🟡 BUY <80% / HOLD → 🔴 SELL
    def _sort_key(h):
        v = (h.get("verdict") or "hold").lower()
        c = h.get("conviction", 0)
        if v == "buy" and c >= 80:
            return (0, -c)   # green first, highest conviction first
        if v == "sell":
            return (2, -c)   # red last
        return (1, -c)       # yellow in middle

    holdings = sorted(holdings, key=_sort_key)

    # Holdings with scores
    lines.append(f"*Holdings* ({len(holdings)})")
    for h in holdings:
        verdict = (h.get("verdict") or "hold").lower()
        conviction = h.get("conviction", 0)
        ticker = h.get("ticker", "")
        scores = h.get("scores", {})

        emoji = _holding_emoji(verdict, conviction)

        # Show top 3 scores inline
        if scores:
            top3 = sorted(scores.items(), key=lambda x: -x[1])[:3]
            score_str = " · " + " ".join(f"{k[:3].upper()}{v}" for k, v in top3)
        else:
            score_str = ""

        lines.append(
            f"{emoji} `{ticker}` *{verdict.upper()}* {conviction}%{score_str}"
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

    # Change tracking (new: Phase 2)
    changes = _format_changes(recs)
    if changes:
        lines.append(changes)
        lines.append("")

    # Ideas scorecard (new: Phase 2)
    scorecard = _format_ideas_scorecard()
    if scorecard:
        lines.append(scorecard)
        lines.append("")

    lines.append(f"{RLM}_סקירת שוק — אינה המלצה פיננסית._")
    return "\n".join(lines)


def _generate_candlestick(ticker: str, name: str, conviction: int,
                          verdict: str = "BUY",
                          rationale: str = "") -> tuple[bytes | None, str]:
    """Generate a professional candlestick chart + Hebrew analysis caption.

    Returns (png_bytes, hebrew_caption). png_bytes is None if chart fails.
    """
    # Fetch 1Y data (need 200+ trading days for MA200)
    data = _fetch_ohlcv(ticker, range_="1y")
    if not data:
        return None, ""

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import pandas as pd
        import numpy as np
    except ImportError:
        return None, ""

    timestamps = data.get("timestamp", [])
    quote = data.get("indicators", {}).get("quote", [{}])[0]
    if not timestamps or not quote.get("close"):
        return None, ""

    dates = pd.to_datetime(timestamps, unit="s")
    df = pd.DataFrame({
        "open": quote.get("open", []),
        "high": quote.get("high", []),
        "low": quote.get("low", []),
        "close": quote.get("close", []),
        "volume": quote.get("volume", []),
    }, index=dates).dropna(subset=["close"])

    if len(df) < 20:
        return None, ""

    # ─── Technical Indicators (computed on full 1Y data) ───────────
    df["ma50"] = df["close"].rolling(50).mean()
    df["ma200"] = df["close"].rolling(200).mean()

    # RSI (14-day)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # Performance stats
    last_price = df["close"].iloc[-1]
    chg_1d = ((last_price / df["close"].iloc[-2]) - 1) * 100 if len(df) >= 2 else 0
    chg_1m = ((last_price / df["close"].iloc[-22]) - 1) * 100 if len(df) >= 22 else 0
    chg_6m = ((last_price / df["close"].iloc[-126]) - 1) * 100 if len(df) >= 126 else 0

    # Trim to last 6 months for display (keep indicators from 1Y calc)
    display_days = min(126, len(df))
    df_disp = df.iloc[-display_days:]

    # ─── Chart (light theme, 3 panels) ─────────────────────────────
    BG = "#fafbfc"
    GRID = "#e5e7eb"
    TEXT = "#1f2937"
    MUTE = "#6b7280"
    GREEN = "#16a34a"
    RED = "#dc2626"

    fig, (ax_price, ax_rsi, ax_vol) = plt.subplots(
        3, 1, figsize=(10, 7.5), dpi=150,
        gridspec_kw={"height_ratios": [4, 1.5, 1]}, sharex=True,
    )
    fig.patch.set_facecolor(BG)

    for ax in (ax_price, ax_rsi, ax_vol):
        ax.set_facecolor(BG)
        ax.tick_params(colors=MUTE, labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(GRID)
        ax.spines["bottom"].set_color(GRID)
        ax.grid(axis="y", color=GRID, linewidth=0.4)

    # ── Price panel: candles + MA50 + MA200 ──
    width = 0.6
    up = df_disp[df_disp["close"] >= df_disp["open"]]
    down = df_disp[df_disp["close"] < df_disp["open"]]

    ax_price.bar(up.index, up["close"] - up["open"], width, bottom=up["open"],
                 color=GREEN, edgecolor=GREEN, linewidth=0.5)
    ax_price.vlines(up.index, up["low"], up["high"], color=GREEN, linewidth=0.5)
    ax_price.bar(down.index, down["close"] - down["open"], width, bottom=down["open"],
                 color=RED, edgecolor=RED, linewidth=0.5)
    ax_price.vlines(down.index, down["low"], down["high"], color=RED, linewidth=0.5)

    # Moving averages
    if df_disp["ma50"].notna().sum() > 0:
        ax_price.plot(df_disp.index, df_disp["ma50"], color="#2563eb",
                      linewidth=1.3, label="MA50")
    if df_disp["ma200"].notna().sum() > 0:
        ax_price.plot(df_disp.index, df_disp["ma200"], color="#d97706",
                      linewidth=1.3, label="MA200")

    # Price label
    ax_price.annotate(
        f"${last_price:,.2f}",
        xy=(df_disp.index[-1], last_price),
        xytext=(8, 8), textcoords="offset points",
        color=TEXT, fontsize=10, fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor=GRID),
    )

    # Title
    change_sign_6m = "+" if chg_6m >= 0 else ""
    ax_price.set_title(
        f"{ticker} — {name}   |   {verdict.upper()} {conviction}%",
        color=TEXT, fontsize=13, fontweight="bold", pad=10, loc="left",
    )

    # Performance stats (top-right, colored, bigger)
    def _fmt_c(v):
        s = "+" if v >= 0 else ""
        return f"{s}{v:.1f}%", (GREEN if v >= 0 else RED)

    stats_items = [("1D", chg_1d), ("1M", chg_1m), ("6M", chg_6m)]
    x_pos = 0.99
    for label, val in reversed(stats_items):
        txt, clr = _fmt_c(val)
        ax_price.text(x_pos, 1.03, txt, transform=ax_price.transAxes,
                      ha="right", va="bottom", fontsize=11, fontweight="bold",
                      color=clr, fontfamily="monospace")
        ax_price.text(x_pos - 0.001, 1.10, label, transform=ax_price.transAxes,
                      ha="right", va="bottom", fontsize=7, color=MUTE)
        x_pos -= 0.12

    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.8,
                    facecolor="white", edgecolor=GRID, labelcolor=TEXT)
    ax_price.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    # ── RSI panel ──
    rsi_valid = df_disp["rsi"].notna()
    ax_rsi.plot(df_disp.index[rsi_valid], df_disp["rsi"][rsi_valid],
                color="#7c3aed", linewidth=1.2)
    ax_rsi.axhline(70, color=RED, linewidth=0.7, linestyle="--", alpha=0.6)
    ax_rsi.axhline(30, color=GREEN, linewidth=0.7, linestyle="--", alpha=0.6)
    ax_rsi.fill_between(df_disp.index[rsi_valid], 30, df_disp["rsi"][rsi_valid],
                        where=df_disp["rsi"][rsi_valid] < 30, alpha=0.15, color=GREEN)
    ax_rsi.fill_between(df_disp.index[rsi_valid], 70, df_disp["rsi"][rsi_valid],
                        where=df_disp["rsi"][rsi_valid] > 70, alpha=0.15, color=RED)
    ax_rsi.set_ylabel("RSI", color=MUTE, fontsize=8)
    ax_rsi.set_ylim(10, 90)

    last_rsi = df["rsi"].dropna().iloc[-1] if df["rsi"].notna().sum() > 0 else 50
    rsi_color = GREEN if last_rsi < 35 else (RED if last_rsi > 65 else MUTE)
    ax_rsi.text(0.98, 0.85, f"RSI {last_rsi:.0f}",
                transform=ax_rsi.transAxes, ha="right", va="top",
                color=rsi_color, fontsize=9, fontweight="bold")

    # ── Volume panel ──
    vol_colors = [GREEN if c >= o else RED
                  for c, o in zip(df_disp["close"], df_disp["open"])]
    ax_vol.bar(df_disp.index, df_disp["volume"], width, color=vol_colors, alpha=0.4)
    ax_vol.set_ylabel("Vol", color=MUTE, fontsize=8)
    ax_vol.yaxis.set_major_formatter(plt.FuncFormatter(
        lambda x, _: f"{x/1e6:.0f}M" if x >= 1e6 else f"{x/1e3:.0f}K"
    ))

    ax_vol.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax_vol.xaxis.set_major_locator(mdates.MonthLocator())
    plt.xticks(rotation=0)

    fig.tight_layout()
    fig.subplots_adjust(hspace=0.06)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    png = buf.read()

    # ─── Generate Hebrew analysis caption ──────────────────────────
    caption = _build_analysis_caption(ticker, name, conviction, verdict,
                                      last_price, chg_6m, last_rsi, df,
                                      rationale)
    return png, caption


def _build_analysis_caption(ticker: str, name: str, conviction: int, verdict: str,
                            price: float, change_6m: float, rsi: float,
                            df, rationale: str = "") -> str:
    """Build a Hebrew caption: AI rationale first, then technical context."""

    lines = [f"📊 *{ticker}* — {name} | {verdict.upper()} {conviction}%"]
    lines.append("")

    # ── AI rationale (the WHY — fundamentals, not chart) ──
    if rationale:
        lines.append(f"{RLM}💡 *למה {verdict.upper()}?*")
        lines.append(f"{RLM}_{rationale}_")
        lines.append("")

    # ── Technical context (supporting data from chart) ──
    lines.append(f"{RLM}📉 *ניתוח טכני:*")

    ma50 = df["ma50"].dropna().iloc[-1] if df["ma50"].notna().sum() > 0 else None
    ma200 = df["ma200"].dropna().iloc[-1] if df["ma200"].notna().sum() > 0 else None

    if ma50 and ma200:
        if price > ma50 > ma200:
            lines.append(f"{RLM}• מגמה עולה — מעל MA50 ו-MA200 (Golden Cross)")
        elif price > ma50 and price < ma200:
            lines.append(f"{RLM}• התאוששות — חצה MA50 למעלה, עדיין מתחת MA200")
        elif price < ma50 < ma200:
            lines.append(f"{RLM}• מגמה יורדת — מתחת ל-MA50 ו-MA200")
        elif price < ma50 and price > ma200:
            lines.append(f"{RLM}• תיקון קצר — מתחת MA50 אך מעל MA200")
    elif ma50:
        pos = "מעל" if price > ma50 else "מתחת"
        lines.append(f"{RLM}• המחיר {pos} MA50")

    # RSI — short
    if rsi < 30:
        lines.append(f"{RLM}• RSI {rsi:.0f} — מכירת יתר (Oversold) ⟵ נקודת כניסה")
    elif rsi < 40:
        lines.append(f"{RLM}• RSI {rsi:.0f} — קרוב למכירת יתר")
    elif rsi > 70:
        lines.append(f"{RLM}• RSI {rsi:.0f} — קניית יתר (Overbought) ⟵ זהירות")
    else:
        lines.append(f"{RLM}• RSI {rsi:.0f} — אזור ניטרלי")

    # 6M — short
    sign = "+" if change_6m >= 0 else ""
    lines.append(f"{RLM}• שינוי 6M: {sign}{change_6m:.1f}%")

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

    # Messages 3+: Top 3 candlestick charts (smart selection)
    # Priority: new ideas first → then best existing BUY holdings
    MAX_CHARTS = 3

    # New ideas (highest priority — you don't own these yet)
    chart_items = []  # (ticker, name, conviction, verdict, rationale)
    for idea in recs.get("new_ideas", []):
        chart_items.append((
            idea.get("ticker", ""),
            idea.get("name", ""),
            idea.get("conviction", 0),
            "BUY",
            idea.get("rationale", ""),
        ))

    # Fill remaining from existing BUY >=80%, ranked by conviction × unanimity
    if len(chart_items) < MAX_CHARTS:
        holdings = recs.get("holdings", [])
        scored = []
        for h in holdings:
            v = (h.get("verdict") or "").lower()
            c = h.get("conviction", 0)
            tk = h.get("ticker", "")
            sector = _get_sector(tk)
            if (v != "buy" or c < 80
                    or sector in BROAD_MARKET_SECTORS
                    or tk.endswith(".TA")):
                continue
            # Score for chart priority: conviction (higher = charted first)
            score = c
            top_rationale = h.get("rationale", "")
            scored.append((score, tk, h.get("name", tk), c, top_rationale))

        scored.sort(reverse=True)
        seen = {ci[0] for ci in chart_items}
        for _, tk, nm, c, rat in scored:
            if tk not in seen and len(chart_items) < MAX_CHARTS:
                chart_items.append((tk, nm, c, "BUY", rat))
                seen.add(tk)

    if chart_items:
        print(f"[info] generating {len(chart_items)} charts (max {MAX_CHARTS})…")
    for ticker, name, conv, verdict, rationale in chart_items[:MAX_CHARTS]:
        print(f"  {ticker}…", end=" ", flush=True)
        chart_bytes, caption = _generate_candlestick(ticker, name, conv, verdict, rationale)
        if chart_bytes:
            send_telegram_photo(chart_bytes, caption)
            print("sent")
        else:
            print("skipped (no data)")


if __name__ == "__main__":
    main()
