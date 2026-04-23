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



def _escape_md(text: str) -> str:
    """Escape characters that break Telegram Markdown V1 inside italic/bold."""
    # Replace underscores that aren't part of our formatting
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


_ERROR_MARKERS = ("[error", "[שגיאת", "API key not valid",
                  "INVALID_ARGUMENT", "Analysis unavailable")


def _is_error_text(s: str) -> bool:
    """True when a rationale / insight field holds a raw Gemini/API error blob."""
    if not s:
        return True
    head = s.strip()[:120]
    return any(m in head for m in _ERROR_MARKERS)


def _truncate_sentence_aware(text: str, cap: int = 1400) -> str:
    """Truncate `text` to <= cap chars at the nearest sentence / paragraph boundary."""
    if not text or len(text) <= cap:
        return text or ""
    window = text[:cap]
    for sep in ("\n\n", ". ", "! ", "? ", "׃ ", ".\n"):
        idx = window.rfind(sep)
        if idx >= int(cap * 0.6):
            return window[: idx + len(sep)].rstrip() + " …"
    return window.rstrip() + " …"


SCORE_LABELS_HE = {
    "quality":     "איכות",
    "valuation":   "שווי",
    "risk":        "סיכון",
    "macro":       "מאקרו",
    "sentiment":   "סנטימנט",
    "technical":   "מגמה",
    "insider":     "פנימיים",      # SEC Form 4 insider trades
    "smart_money": "קרנות ותיקות",  # 13F aggregates from top 10 funds
}

_DEFAULT_SCORE_WEIGHTS = {
    "quality": 25, "valuation": 22, "risk": 18,
    "macro": 12, "sentiment": 4, "technical": 4,
    "insider": 8, "smart_money": 7,
}


def _load_scoring_weights() -> dict:
    """Read user's scoring weights from settings.json (falls back to defaults)."""
    settings = _load_json(SETTINGS_PATH)
    return (settings or {}).get("scoring_weights") or _DEFAULT_SCORE_WEIGHTS


def _weighted_score(scores: dict, weights: dict, details: dict | None = None) -> int | None:
    """Weighted average of 0-100 scores, excluding placeholder "no data" factors.

    When `details` is provided, any factor whose detail text matches
    `_NO_DATA_MARKERS` is dropped from the average so a placeholder 50 does not
    drag the total toward the middle. Remaining weights are re-normalised
    automatically by dividing by the surviving total weight.

    Returns None when no factor has usable data — caller decides what to show.
    """
    if not scores:
        return None
    if details:
        keys = [k for k in scores if _factor_has_data(details.get(k))]
    else:
        keys = list(scores.keys())
    if not keys:
        return None
    total_w = sum(weights.get(k, 0) for k in keys) or 1
    return round(sum(scores[k] * weights.get(k, 0) for k in keys) / total_w)


def _score_hint(scores: dict) -> str:
    """Return ' · חזק: <factor>' or ' · חולשה: <factor>' when a factor stands out."""
    if not scores:
        return ""
    top_k, top_v = max(scores.items(), key=lambda kv: kv[1])
    bot_k, bot_v = min(scores.items(), key=lambda kv: kv[1])
    spread = top_v - bot_v
    if top_v >= 65 and spread >= 15:
        return f" · חזק: {SCORE_LABELS_HE.get(top_k, top_k)}"
    if bot_v <= 40 and spread >= 15:
        return f" · חולשה: {SCORE_LABELS_HE.get(bot_k, bot_k)}"
    return ""


# Markers that score_details uses to signal "no data for this factor".
# A factor matching any of these is excluded from the weighted score so a
# placeholder 50 doesn't pollute the total.
_NO_DATA_MARKERS = (
    "No fundamental data",
    "No valuation data",
    "No analyst coverage",
    "No social sentiment",
    "Insufficient price history",
)


def _factor_has_data(details_for_factor) -> bool:
    """True when scoring_engine had real data to score this factor."""
    if not details_for_factor:
        return False
    joined = " ".join(details_for_factor) if isinstance(details_for_factor, list) \
        else str(details_for_factor)
    return not any(m in joined for m in _NO_DATA_MARKERS)


def _score_triangle(score: int) -> str:
    """Colored chart-arrow for a 0-100 score (📈 good, ⚪ neutral, 📉 weak).

    📈 is "chart trending up" — rendered green in all Telegram clients.
    📉 is "chart trending down" — rendered red. Unlike 🔺/🔻 (both red) this
    pair actually carries color + direction together.
    """
    if score >= 65:
        return "📈"
    if score >= 40:
        return "⚪"
    return "📉"


def _pct_triangle(pct: float, neutral_band: float = 0.05) -> str:
    """📈 positive, 📉 negative, ⚪ near-zero."""
    if pct > neutral_band:
        return "📈"
    if pct < -neutral_band:
        return "📉"
    return "⚪"


def _format_holding_detail(h: dict, weights: dict) -> list[str]:
    """Mobile-first holding block — groups scores into 3 short Hebrew tiers.

    Layout (4-5 lines, no English prose to trip up RTL rendering):
        🟡 `TICKER` · HOLD 51% · ציון 58
        📈 חזקים: קרנות 100 · איכות 70 · סנטימנט 72
        ⚪ רגילים: שווי 50, סיכון 60, מאקרו 50, מגמה 42
        📉 חלשים: פנימיים 15

    The English detail text (e.g. "ROE 35.7% — excellent profitability") is
    intentionally dropped here — it stays available in the Streamlit modal.
    Mobile Telegram can't render long mixed-direction lines cleanly.
    """
    verdict = (h.get("verdict") or "hold").lower()
    conviction = h.get("conviction", 0)
    ticker = h.get("ticker", "")
    scores = h.get("scores", {})
    details = h.get("score_details", {}) or {}

    emoji = _holding_emoji(verdict, conviction)
    wscore = _weighted_score(scores, weights, details)
    score_display = f"ציון {wscore}" if wscore is not None else "אין מספיק דאטה"

    out = [f"{emoji} `{ticker}` · *{verdict.upper()}* {conviction}% · {score_display}"]

    # Bucket every *usable* factor (drop no-data placeholders entirely) by tier.
    strong, neutral, weak = [], [], []
    for key, val in scores.items():
        if not _factor_has_data(details.get(key)):
            continue
        label = SCORE_LABELS_HE.get(key, key)
        item = (val, label)
        if val >= 65:
            strong.append(item)
        elif val >= 40:
            neutral.append(item)
        else:
            weak.append(item)

    # Sort each tier by score so the highlight is the most salient on each row
    strong.sort(key=lambda x: -x[0])
    weak.sort(key=lambda x: x[0])
    neutral.sort(key=lambda x: -x[0])

    def _join(items):
        return " · ".join(f"{label} {val}" for val, label in items)

    if strong:
        out.append(f"{RLM}📈 *חזקים:* {_join(strong)}")
    if neutral:
        out.append(f"{RLM}⚪ *רגילים:* {_join(neutral)}")
    if weak:
        out.append(f"{RLM}📉 *חלשים:* {_join(weak)}")
    return out


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
    """Resolve sector dynamically: user overrides in config.py → yfinance cache →
    NEW_IDEA_SECTORS fallback → "Other"."""
    try:
        sys.path.insert(0, str(_ROOT))
        from ticker_metadata import get_sector as _dyn_sector
        sector = _dyn_sector(ticker)
        if sector and sector != "Other":
            return sector
    except Exception:
        pass
    # Last-resort fallback for new-idea suggestions the engine returns
    return NEW_IDEA_SECTORS.get(ticker, "Other")


# ─── New Mentor Blocks ────────────────────────────────────────────────────

def _format_accuracy_summary() -> str:
    """Show recent hit rate if we have enough history."""
    try:
        sys.path.insert(0, str(_ROOT))
        from backtest_engine import get_or_compute_backtest
        result = get_or_compute_backtest(days_elapsed=30)
    except Exception:
        return ""
    if result.get("status") != "ok":
        return ""
    total = result.get("total", 0)
    if total < 5:
        return ""  # not enough history for meaningful stats

    hit_rate = result.get("hit_rate", 0)
    alpha = result.get("alpha_vs_spy_pct")
    buy_ret = result.get("buy_portfolio_avg_return_pct", 0)

    if hit_rate >= 65:
        hr_emoji = "🎯"
    elif hit_rate >= 55:
        hr_emoji = "📊"
    else:
        hr_emoji = "📉"

    hr_tri = _pct_triangle(hit_rate - 50, neutral_band=5)  # >55 📈, <45 📉
    # Mobile-friendly: one metric per line
    lines = [f"{hr_emoji} *Track Record* ({total} verdicts)"]
    lines.append(f"`Hit rate    {hit_rate:.0f}%` {hr_tri}")
    if alpha is not None:
        buy_tri = _pct_triangle(buy_ret)
        alpha_tri = _pct_triangle(alpha)
        alpha_sign = "+" if alpha >= 0 else ""
        lines.append(f"`BUY avg    {buy_ret:+.1f}%` {buy_tri}")
        lines.append(f"`Alpha vs SPY {alpha_sign}{alpha:.1f}%` {alpha_tri}")
    return "\n".join(lines)


def _format_social_sentiment(recs: dict) -> str:
    """Block — Twitter/X social sentiment highlights.

    Picks top 3 holdings with strongest social signal (most bullish OR most bearish).
    Skipped if no holdings have social_sentiment data.
    """
    holdings = recs.get("holdings", [])
    with_social = [
        h for h in holdings
        if h.get("social_sentiment") and h["social_sentiment"].get("sentiment_score") is not None
    ]
    if not with_social:
        return ""

    # Sort by |sentiment - 50| descending (strongest signal first)
    with_social.sort(key=lambda h: -abs(h["social_sentiment"]["sentiment_score"] - 50))
    top3 = with_social[:3]

    lines = ["🐦 *Social Sentiment (X + News)*"]
    for h in top3:
        tk = h.get("ticker", "")
        s = h["social_sentiment"]
        score = s.get("sentiment_score", 50)
        label = s.get("label", "neutral").lower()
        emoji = "🐂" if label == "bullish" else "🐻" if label == "bearish" else "⚖️"
        themes = s.get("top_themes", [])
        theme_str = f" · {themes[0][:40]}" if themes else ""
        lines.append(f"{emoji} `{tk}` {score}/100 ({label.upper()}){theme_str}")
    return "\n".join(lines)


def _format_uoa_alerts(recs: dict) -> str:
    """Scan portfolio tickers for Unusual Options Activity and render the top 3.

    Only US equities with real option chains (yfinance) are scanned — crypto
    ETFs and TASE tickers are skipped internally.
    """
    try:
        sys.path.insert(0, str(_ROOT))
        from data_loader_options import scan_portfolio_uoa, format_uoa_telegram
    except Exception:
        return ""
    tickers = [h.get("ticker") for h in recs.get("holdings", []) if h.get("ticker")]
    tickers += [i.get("ticker") for i in recs.get("new_ideas", []) if i.get("ticker")]
    # Only scan US tickers (chain endpoints don't work for .TA)
    tickers = [t for t in tickers if t and not t.endswith(".TA")]
    if not tickers:
        return ""
    # A single ticker's chain parse error must not brick the whole digest —
    # an earlier NaN-to-int crash in yfinance rows took down every message.
    try:
        hits = scan_portfolio_uoa(tickers[:10])  # cap scan to 10 tickers to stay polite
    except Exception as e:
        print(f"[warn] UOA scan failed: {e}", file=sys.stderr)
        return ""
    if not hits:
        return ""
    lines = ["⚡ *זרימה לא רגילה באופציות*"]
    for info in hits[:3]:
        try:
            block = format_uoa_telegram(info)
        except Exception:
            continue
        if block:
            lines.append(block)
    return "\n".join(lines)


def _format_earnings_alerts(recs: dict) -> str:
    """Block — upcoming earnings in the next 14 days for holdings or ideas."""
    try:
        sys.path.insert(0, str(_ROOT))
        from earnings_calendar import get_upcoming_earnings
    except Exception:
        return ""

    holdings_tk = [h.get("ticker") for h in recs.get("holdings", []) if h.get("ticker")]
    ideas_tk = [i.get("ticker") for i in recs.get("new_ideas", []) if i.get("ticker")]
    all_tickers = [t for t in holdings_tk + ideas_tk if not t.endswith(".TA")]
    if not all_tickers:
        return ""

    try:
        upcoming = get_upcoming_earnings(all_tickers, days_ahead=14)
    except Exception:
        return ""
    if not upcoming:
        return ""

    lines = ["📅 *Upcoming Earnings (next 14 days)*"]
    for e in upcoming[:5]:
        tk = e.get("ticker", "")
        date = e.get("report_date", "?")
        est_eps = e.get("estimated_eps", "")
        est_str = f" · est EPS ${est_eps}" if est_eps and est_eps != "None" else ""
        lines.append(f"⚠️ `{tk}` — {date}{est_str}")
    return "\n".join(lines)


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

    # Mobile-friendly: one metric per line. Each line fits under ~30 chars so
    # no wrapping on narrow phones.
    lines = ["📊 *שוק היום*"]

    if m.get("sp500_change") is not None:
        sp = m["sp500_change"]
        lines.append(f"`S&P 500   {sp:+.1f}%` {_pct_triangle(sp)}")
    if m.get("nasdaq_change") is not None:
        nq = m["nasdaq_change"]
        lines.append(f"`Nasdaq    {nq:+.1f}%` {_pct_triangle(nq)}")
    if m.get("vix") is not None:
        vix_val = m["vix"]
        if vix_val > 25:
            vix_tri, fear = "📉", "פחד"
        elif vix_val < 15:
            vix_tri, fear = "📈", "רגוע"
        else:
            vix_tri, fear = "⚪", "רגיל"
        lines.append(f"`VIX       {vix_val:.0f}` {vix_tri} ({fear})")
    if m.get("fed_rate") is not None:
        lines.append(f"`Fed       {m['fed_rate']:.2f}%`")
    if m.get("ten_year_yield") is not None:
        lines.append(f"`10Y       {m['ten_year_yield']:.2f}%`")
    if m.get("usd_ils") is not None:
        lines.append(f"`USD/ILS   {m['usd_ils']:.3f}`")

    return "\n".join(lines)


# One-line "why it matters" hints per lesson topic (kept generic so they slot
# under any lesson body without sounding repetitive).
_LESSON_TOPIC_HINTS = {
    "valuation": "💡 *למה זה חשוב?* שילוב הערכת שווי נכונה מפחית את הסיכון לקנות בשיא ומאפשר לזהות הזדמנויות כשהשוק מגזים בפחד.",
    "risk": "💡 *למה זה חשוב?* ניהול סיכון הוא מה שמבדיל בין משקיע רציני ומי שמהמר — גם רעיון מבריק יכול להרוס תיק אם הפוזיציה גדולה מדי.",
    "quality": "💡 *למה זה חשוב?* חברות איכות מתפקדות טוב יותר במשברים, והן אלה שמצליחות לייצר תשואה עקבית לאורך זמן.",
    "technical": "💡 *למה זה חשוב?* אינדיקטורים טכניים עוזרים לתזמון כניסה ויציאה — לא מנבאים את העתיד, אבל מגלים איפה הכוח בשוק.",
    "behavioral": "💡 *למה זה חשוב?* רוב ההפסדים בהשקעות לא מגיעים מטעויות אנליטיות אלא מהטיות התנהגותיות — מודעות היא הגנה.",
    "macro": "💡 *למה זה חשוב?* ריבית, אינפלציה ותעסוקה קובעים את הרקע הכללי — לפעמים חשובים יותר מהחברה עצמה.",
    "sentiment": "💡 *למה זה חשוב?* סנטימנט של השוק הוא אינדיקטור סותר (contrarian) — כאשר כולם אופטימיים, הזמן להיות זהיר.",
    "israeli": "💡 *למה זה חשוב?* השוק הישראלי מתנהג אחרת מארה\"ב — מס, מט\"ח, נזילות ומיסוי פנסיוני דורשים התייחסות ספציפית.",
    "portfolio": "💡 *למה זה חשוב?* גיוון נכון מחסן מפני טעות בודדת — לא מגן מפני מפולת שוק, אבל מקטין את הכאב.",
    "strategy": "💡 *למה זה חשוב?* האסטרטגיה שאתה בוחר חייבת להתאים לאופי שלך — אסטרטגיה שמעולה במספרים אבל לא מתאימה לך תנטוש אותה ברגע הלא נכון.",
}


def _format_daily_lesson(recs: dict) -> str:
    """Block 2: Rotating daily financial lesson with expanded context + portfolio example."""
    lessons_path = _ROOT / "lessons.json"
    if not lessons_path.exists():
        return ""
    try:
        lessons = json.loads(lessons_path.read_text())
    except Exception:
        return ""
    if not lessons:
        return ""

    day = datetime.now().timetuple().tm_yday
    lesson = lessons[day % len(lessons)]

    title = lesson.get("title_he", "")
    body = lesson.get("body_he", "")
    topic = lesson.get("topic", "")
    if not title:
        return ""

    idx = lesson.get("id", day % len(lessons) + 1)
    lines = [
        f"📚 *שיעור יומי #{idx}: {title}*",
        f"{RLM}_{_escape_md(body)}_",
    ]

    # Topic-level "why it matters" line — adds depth without rewriting 30 lessons.
    hint = _LESSON_TOPIC_HINTS.get(topic)
    if hint:
        lines.append("")
        lines.append(f"{RLM}{hint}")

    # Portfolio tie-in: up to 2 relevant tickers from the lesson's list that
    # actually exist in the portfolio, so the user sees concrete examples.
    portfolio_tickers = {
        h.get("ticker") for h in recs.get("holdings", []) if h.get("ticker")
    }
    relevant = [
        tk for tk in lesson.get("tickers_relevant", [])
        if tk in portfolio_tickers
    ]
    example_tpl = lesson.get("example_template", "")
    if example_tpl and relevant:
        try:
            from data_loader_fundamentals import load_fundamentals_cache
            cache = load_fundamentals_cache()
            tickers_data = cache.get("tickers", {})
            examples_added = 0
            for tk in relevant:
                if tk in tickers_data and examples_added < 2:
                    fd = tickers_data[tk]
                    example = example_tpl.format(
                        ticker=tk,
                        pe=fd.get("pe", "N/A"),
                        peg=fd.get("peg", "N/A"),
                        roe=fd.get("roe", "N/A"),
                        margin=fd.get("profit_margin", "N/A"),
                        sector_pe="22",
                    )
                    if examples_added == 0:
                        lines.append("")
                        lines.append(f"{RLM}📌 *בתיק שלך:*")
                    lines.append(f"{RLM}_{_escape_md(example)}_")
                    examples_added += 1
        except Exception:
            pass
    elif relevant:
        # No template but we do have relevant tickers in the portfolio
        tickers_str = ", ".join(f"`{t}`" for t in relevant[:3])
        lines.append("")
        lines.append(f"{RLM}📌 _רלוונטי בתיק שלך: {tickers_str}_")

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
    lines = ["*📈 מעקב אחר המלצות קודמות*"]
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

    # AI track record / hit rate (shown once we have ≥5 historical verdicts)
    acc_summary = _format_accuracy_summary()
    if acc_summary:
        lines.append(acc_summary)
        lines.append("")

    # Earnings alerts (next 14 days) — surface BEFORE the news dumps into noise
    earnings_alerts = _format_earnings_alerts(recs)
    if earnings_alerts:
        lines.append(earnings_alerts)
        lines.append("")

    # Unusual Options Activity — only shows if any portfolio ticker has
    # meaningful UOA hits (volume > 3× OI, premium > $25k, DTE 7-90).
    uoa_block = _format_uoa_alerts(recs)
    if uoa_block:
        lines.append(uoa_block)
        lines.append("")

    # Social sentiment highlights (top 3 strongest signals from X/news)
    social_block = _format_social_sentiment(recs)
    if social_block:
        lines.append(social_block)
        lines.append("")

    # Smart Analyst Brief — monthly cadence (1st of each month only, to reduce noise)
    today_day = datetime.now().day
    if today_day == 1:
        insights = recs.get("smart_insights", {})
        body = (insights.get("insights") or "") if insights else ""
        if body and not _is_error_text(body):
            headline = insights.get("headline", "")
            lines.append("🧠 *Smart Analyst Brief* — סקירה חודשית")
            if headline and not _is_error_text(headline):
                lines.append(f"{RLM}*{_escape_md(headline)}*")
            body_tg = _truncate_sentence_aware(body.replace("**", "*"), cap=1400)
            lines.append(f"{RLM}_{_escape_md(body_tg)}_")
            lines.append("")
    elif recs.get("summary"):
        # Fallback: show the short summary
        summary = recs.get("summary", "")
        sentences = [s.strip() for s in summary.replace(". ", ".\n").split("\n") if s.strip()]
        short_summary = ". ".join(sentences[:2]).rstrip(".").replace("..", ".")
        lines.append(f"{RLM}_{_escape_md(short_summary)}._")
        lines.append("")

    # Data-driven Key Takeaways (compact summary)
    buy_count = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "buy")
    sell_count = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "sell")
    hold_count = len(holdings) - buy_count - sell_count

    lines.append("*סיכום*")
    lines.append(f"🟢 {buy_count} קנייה  ·  🔴 {sell_count} מכירה  ·  🟡 {hold_count} החזקה")

    # New-ideas teaser
    if new_ideas:
        idea_tickers = ", ".join(f"`{i['ticker']}`" for i in new_ideas)
        lines.append(f"{RLM}💡 רעיונות חדשים: {idea_tickers}")

    lines.append("")

    # Daily lesson (new: Phase 2)
    lesson = _format_daily_lesson(recs)
    if lesson:
        lines.append(lesson)
        lines.append("")

    # Detailed picks: show BUY + SELL in full. Fallback: top 3 by weighted score.
    weights = _load_scoring_weights()
    buys = [h for h in holdings if (h.get("verdict") or "").lower() == "buy"]
    sells = [h for h in holdings if (h.get("verdict") or "").lower() == "sell"]
    # Sort each bucket by conviction (highest first)
    buys.sort(key=lambda h: -h.get("conviction", 0))
    sells.sort(key=lambda h: -h.get("conviction", 0))
    picks = buys + sells
    if picks:
        lines.append("*🎯 המלצות להיום*")
    else:
        # No actionable picks today → show the 3 highest-scoring holdings.
        # None (no usable data) sorts to the bottom via (has_score, score).
        def _rank_key(h):
            s = _weighted_score(
                h.get("scores", {}), weights, h.get("score_details") or {}
            )
            return (s is None, -(s or 0))
        picks = sorted(holdings, key=_rank_key)[:3]
        lines.append(f"{RLM}_אין המלצות קנייה או מכירה היום — להלן 3 הציונים הגבוהים בתיק:_")
        lines.append("*⭐ מובילים בתיק*")
    lines.append("")

    for h in picks:
        lines.extend(_format_holding_detail(h, weights))
        lines.append("")

    # Legend — once per message, at the bottom
    lines.append(
        f"{RLM}_ציון משוקלל מ-8 מדדים · 📈 ≥65 · ⚪ 40-64 · 📉 <40_"
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
            if rationale and not _is_error_text(rationale):
                lines.append(f"{RLM}_{_escape_md(rationale)}_")
            elif rationale:
                lines.append(f"{RLM}_רציונל לא זמין כעת — ננסה שוב מחר_")
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

    # Today's recommendations summary — compact reminder so the user always
    # sees "today's picks" before the past-tracking scorecard.
    holdings = recs.get("holdings", [])
    buys = [h for h in holdings if (h.get("verdict") or "").lower() == "buy"]
    sells = [h for h in holdings if (h.get("verdict") or "").lower() == "sell"]
    if buys or sells:
        lines.append("*🎯 המלצות להיום*")
        if buys:
            tix = ", ".join(f"`{h['ticker']}` {h.get('conviction', 0)}%"
                            for h in sorted(buys, key=lambda h: -h.get("conviction", 0)))
            lines.append(f"{RLM}🟢 קנייה: {tix}")
        if sells:
            tix = ", ".join(f"`{h['ticker']}` {h.get('conviction', 0)}%"
                            for h in sorted(sells, key=lambda h: -h.get("conviction", 0)))
            lines.append(f"{RLM}🔴 מכירה: {tix}")
        lines.append("")
    else:
        # Fallback: list top-3 by weighted score so the user still sees
        # "today's picks" even when no BUY/SELL verdict is produced.
        weights = _load_scoring_weights()

        def _rank_key(h):
            s = _weighted_score(
                h.get("scores", {}), weights, h.get("score_details") or {}
            )
            return (s is None, -(s or 0))

        ranked = sorted(holdings, key=_rank_key)
        # Keep only holdings with a usable score for this teaser
        ranked = [
            h for h in ranked
            if _weighted_score(h.get("scores", {}), weights,
                               h.get("score_details") or {}) is not None
        ]
        top3 = ranked[:3]
        if top3:
            def _score_str(h):
                s = _weighted_score(
                    h.get("scores", {}), weights, h.get("score_details") or {}
                )
                return f"`{h['ticker']}` (ציון {s})"
            tix = ", ".join(_score_str(h) for h in top3)
            lines.append("*🎯 המלצות להיום*")
            lines.append(f"{RLM}_אין BUY/SELL — 3 הציונים הגבוהים:_ {tix}")
            lines.append("")

    # Past ideas scorecard — comes AFTER today's picks so the user always sees
    # both "what to do today" and "how past calls performed".
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
    if rationale and not _is_error_text(rationale):
        # Keep rationale short enough that the full caption stays under Telegram's
        # 1024-char sendPhoto limit (reserve ~350 chars for the technical section).
        rationale_short = _truncate_sentence_aware(rationale, cap=600)
        lines.append(f"{RLM}💡 *למה {verdict.upper()}?*")
        lines.append(f"{RLM}_{_escape_md(rationale_short)}_")
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

    caption = "\n".join(lines)
    # Telegram sendPhoto caption limit is 1024 chars; keep a small safety margin
    if len(caption) > 1020:
        caption = caption[:1020].rstrip() + " …"
    return caption


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
    import urllib.error

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
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace") if hasattr(e, "read") else ""
        print(f"[error] Telegram HTTPError {e.code}: {err_body}", file=sys.stderr)
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
    import urllib.error

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
                resp_body = resp.read().decode(errors="replace")
                print(f"[warn] sendPhoto returned {resp.status}: {resp_body}",
                      file=sys.stderr)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace") if hasattr(e, "read") else ""
        print(f"[warn] sendPhoto HTTPError {e.code}: {err_body}", file=sys.stderr)
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
