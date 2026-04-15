#!/usr/bin/env python3
"""
Run AI-driven recommendations against the current portfolio + settings profile.

Wraps the existing OSS project: virattt/ai-hedge-fund (https://github.com/virattt/ai-hedge-fund)
Does NOT invent a new recommendation engine — it shells out to ai-hedge-fund's CLI,
parses its output, and writes `recommendations.json` for the Streamlit Recommendations page.

Usage:
    python scripts/run_recommendations.py --once          # one real run
    python scripts/run_recommendations.py --dry-run       # write mock data (no LLM calls)

Environment:
    AI_HEDGE_FUND_DIR   path to cloned ai-hedge-fund repo (default: ~/ai-hedge-fund)
    ANTHROPIC_API_KEY   Claude API key (preferred for recommendation quality)
    GEMINI_API_KEY      Gemini API key (cheaper, used for bulk work)
    OPENAI_API_KEY      OpenAI API key (fallback)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = _ROOT / "portfolio.json"
SETTINGS_PATH = _ROOT / "settings.json"
RECS_PATH = _ROOT / "recommendations.json"

# Load .env so GEMINI_API_KEY / GOOGLE_API_KEY are available
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass


# ─── Helpers ────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _build_profile_preamble(settings: dict) -> str:
    """Render the user personality profile into a system-prompt preamble injected into ai-hedge-fund."""
    lines = [
        f"PROFILE: {settings.get('profile_name', 'default')}",
        f"Trading style: {settings.get('style', 'conservative')}",
        f"Investment horizon: {settings.get('horizon_years', 3)} years",
        f"Risk level: {settings.get('risk_level', 'medium-low')}",
        f"Trading frequency: {settings.get('trading_frequency', 'bi-monthly')}",
        f"Contribution: {settings.get('contribution_ils', 0)} ILS every "
        f"{settings.get('contribution_frequency_days', 60)} days",
        f"Crypto cap: {settings.get('crypto_cap_pct', 10)}% of portfolio",
        "",
        "Preferred sectors (overweight): " + ", ".join(settings.get("preferred_sectors", [])),
        "Avoid sectors: " + ", ".join(settings.get("avoid_sectors", []) or ["(none)"]),
        "",
        "Investment theses the user holds:",
    ]
    for t in settings.get("theses", []):
        lines.append(f"  - {t}")
    lines.append("")
    lines.append(
        "Rationale language: WRITE ALL RATIONALE TEXT AND THE DAILY SUMMARY IN HEBREW. "
        "Keep ticker symbols and verdict words (BUY/HOLD/SELL) in English."
    )
    lines.append("Frame all recommendations around this profile. Market commentary only — not financial advice.")
    return "\n".join(lines)


def _tickers(portfolio: dict) -> list[str]:
    return [h["ticker"] for h in portfolio.get("holdings", []) if h.get("ticker")]


def _sector_of(ticker: str) -> str:
    # Lazy import to keep this file independent of Streamlit config during CLI use
    try:
        sys.path.insert(0, str(_ROOT))
        from config import SECTOR_MAP  # type: ignore
        return SECTOR_MAP.get(ticker, "")
    except Exception:
        return ""


# ─── Technical indicator helpers ──────────────────────────────────────────────

def _compute_technicals(df) -> dict:
    """Compute MA50, MA200, RSI(14) from a historical OHLCV DataFrame."""
    if df is None or len(df) < 20:
        return {}
    close = df["close"]
    result = {}
    if len(close) >= 50:
        result["ma50"] = round(close.rolling(50).mean().iloc[-1], 2)
    if len(close) >= 200:
        result["ma200"] = round(close.rolling(200).mean().iloc[-1], 2)
    # RSI(14) — handle flat-price edge case (no gain AND no loss → neutral)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    g = gain.iloc[-1]
    l = loss.iloc[-1]
    if (g is None or g == 0) and (l is None or l == 0):
        result["rsi14"] = 50.0  # flat price → neutral
    else:
        rs = (g or 0) / max(l or 1e-9, 1e-9)
        result["rsi14"] = round(100 - 100 / (1 + rs), 1)
    return result


def _build_market_data_block(ticker: str, quote: dict, technicals: dict) -> str:
    """Build a MARKET DATA text block from Yahoo Finance data for injection into prompts."""
    if not quote and not technicals:
        return ""
    lines = [f"MARKET DATA for {ticker} (live):"]
    price = quote.get("price")
    if price:
        lines.append(f"  Price: {price:.2f} | Daily change: {quote.get('daily_change_pct', 0):+.1f}%")
    hi52 = quote.get("fifty_two_week_high")
    lo52 = quote.get("fifty_two_week_low")
    if hi52 and lo52:
        lines.append(f"  52-week range: {lo52:.2f} — {hi52:.2f}")
    vol = quote.get("volume")
    if vol:
        lines.append(f"  Volume: {vol:,.0f}")
    ma50 = technicals.get("ma50")
    ma200 = technicals.get("ma200")
    if ma50:
        lines.append(f"  MA50: {ma50:.2f}" + (f" | MA200: {ma200:.2f}" if ma200 else ""))
    if price and ma200:
        pct_vs = ((price / ma200) - 1) * 100
        lines.append(f"  Price vs MA200: {pct_vs:+.1f}%")
    rsi = technicals.get("rsi14")
    if rsi:
        zone = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral"
        lines.append(f"  RSI(14): {rsi:.0f} ({zone})")
    return "\n".join(lines)


def _build_full_context(ticker: str, persona: str, quote: dict,
                        technicals: dict, fundamentals: dict | None,
                        macro: dict, news: list[str],
                        portfolio_weight: float = 0,
                        sector_weight: float = 0) -> str:
    """Build persona-specific market data block.

    Each persona gets only the data relevant to their analysis style.
    """
    parts: list[str] = []

    # Price data — everyone gets basic price info
    price = quote.get("price")
    if price:
        parts.append(f"PRICE: {price:.2f} | Daily: {quote.get('daily_change_pct', 0):+.1f}%")
        hi52 = quote.get("fifty_two_week_high")
        lo52 = quote.get("fifty_two_week_low")
        if hi52 and lo52:
            parts.append(f"52-week range: {lo52:.2f} — {hi52:.2f}")

    # Technical data — for technical, cathie_wood (momentum), peter_lynch
    tech_personas = {"technical_analyst", "cathie_wood", "peter_lynch", "michael_burry"}
    if persona in tech_personas or persona in {"warren_buffett", "charlie_munger",
                                                "fundamentals_analyst", "ben_graham", "valuation"}:
        ma50 = technicals.get("ma50")
        ma200 = technicals.get("ma200")
        rsi = technicals.get("rsi14")
        if persona == "technical_analyst":
            # Full technical data
            if ma50:
                parts.append(f"MA50: {ma50:.2f}" + (f" | MA200: {ma200:.2f}" if ma200 else ""))
            if price and ma200:
                parts.append(f"Price vs MA200: {((price/ma200)-1)*100:+.1f}%")
            if rsi:
                zone = "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral"
                parts.append(f"RSI(14): {rsi:.0f} ({zone})")
            vol = quote.get("volume")
            if vol:
                parts.append(f"Volume: {vol:,.0f}")
        else:
            # Brief technical summary for non-technical personas
            if ma50 and ma200 and price:
                if price > ma50 > ma200:
                    parts.append("Trend: Uptrend (Price > MA50 > MA200)")
                elif price < ma50 < ma200:
                    parts.append("Trend: Downtrend (Price < MA50 < MA200)")
                else:
                    parts.append(f"Trend: Mixed (MA50={ma50:.0f}, MA200={ma200:.0f})")

    # Fundamental data — for value/fundamental personas
    fund_personas = {"warren_buffett", "charlie_munger", "peter_lynch", "ben_graham",
                     "fundamentals_analyst", "valuation", "cathie_wood"}
    if persona in fund_personas and fundamentals:
        f_lines = []
        pe = fundamentals.get("pe")
        if pe is not None:
            f_lines.append(f"P/E: {pe:.1f}")
        peg = fundamentals.get("peg")
        if peg is not None:
            f_lines.append(f"PEG: {peg:.2f}")
        margin = fundamentals.get("profit_margin")
        if margin is not None:
            f_lines.append(f"Profit Margin: {margin:.1f}%")
        roe = fundamentals.get("roe")
        if roe is not None:
            f_lines.append(f"ROE: {roe:.1f}%")
        de = fundamentals.get("debt_equity")
        if de is not None:
            f_lines.append(f"Debt/Equity: {de:.2f}")
        eps = fundamentals.get("eps")
        if eps is not None:
            f_lines.append(f"EPS: {eps:.2f}")
        target = fundamentals.get("analyst_target")
        if target and price:
            upside = ((target / price) - 1) * 100
            f_lines.append(f"Analyst Target: {target:.0f} ({upside:+.0f}% vs price)")
        dy = fundamentals.get("dividend_yield")
        if dy is not None and dy > 0:
            f_lines.append(f"Dividend Yield: {dy:.2f}%")
        mcap = fundamentals.get("market_cap")
        if mcap:
            if mcap > 1e12:
                f_lines.append(f"Market Cap: ${mcap/1e12:.1f}T")
            elif mcap > 1e9:
                f_lines.append(f"Market Cap: ${mcap/1e9:.0f}B")
        if f_lines:
            parts.append("FUNDAMENTALS: " + " | ".join(f_lines))

    # Analyst consensus — for sentiment, fundamentals, valuation
    consensus_personas = {"sentiment", "fundamentals_analyst", "valuation",
                          "warren_buffett", "charlie_munger"}
    if persona in consensus_personas and fundamentals:
        ab = fundamentals.get("analyst_buy", 0)
        ah = fundamentals.get("analyst_hold", 0)
        asl = fundamentals.get("analyst_sell", 0)
        total = ab + ah + asl
        if total > 0:
            parts.append(f"ANALYST CONSENSUS: {ab} Buy / {ah} Hold / {asl} Sell "
                         f"({ab/total*100:.0f}% bullish)")

    # Macro data — for macro, risk_manager
    macro_personas = {"macro", "risk_manager", "warren_buffett", "michael_burry"}
    if persona in macro_personas and macro:
        m_parts = []
        if macro.get("fed_rate") is not None:
            m_parts.append(f"Fed Rate: {macro['fed_rate']:.2f}%")
        if macro.get("ten_year_yield") is not None:
            m_parts.append(f"10Y Yield: {macro['ten_year_yield']:.2f}%")
        if macro.get("vix") is not None:
            m_parts.append(f"VIX: {macro['vix']:.1f}")
        if macro.get("sp500_change") is not None:
            m_parts.append(f"S&P 500 today: {macro['sp500_change']:+.1f}%")
        if m_parts:
            parts.append("MACRO: " + " | ".join(m_parts))

    # News headlines — for sentiment, cathie_wood, michael_burry
    news_personas = {"sentiment", "cathie_wood", "michael_burry", "macro"}
    if persona in news_personas and news:
        parts.append("RECENT NEWS:\n  " + "\n  ".join(f"• {h}" for h in news[:3]))

    # Risk / portfolio context — for risk_manager
    if persona == "risk_manager":
        parts.append(f"PORTFOLIO: This holding = {portfolio_weight:.1f}% of portfolio. "
                     f"Sector = {sector_weight:.1f}% of portfolio.")

    if not parts:
        # Fallback to basic market data block
        return _build_market_data_block(ticker, quote, technicals)
    return f"MARKET DATA for {ticker}:\n" + "\n".join(parts)


# ─── Real run — direct Gemini calls, one per (ticker × persona) ─────────────

_DATA_RULE = "התבסס אך ורק על הנתונים שמסופקים בפרומפט. אל תמציא מספרים שלא ניתנו לך."

PERSONA_SYSTEM_PROMPTS = {
    "warren_buffett": (
        "אתה וורן באפט. מסגרת ההחלטות שלך:\n"
        "1. חפיר תחרותי (MOAT): מותג, עלויות מעבר, אפקט רשת, יתרון עלות. אם אין חפיר — SELL.\n"
        "2. הנהלה: רקורד הקצאת הון חכמה, יושרה, חשיבה ארוכת-טווח.\n"
        "3. תמחור: P/E < 20 לעסק איכותי, P/E < 15 לעסק ממוצע. מעל 30 — זהירות.\n"
        "4. שולי בטיחות: קנה רק אם המחיר נמוך מהערכת השווי. מחיר גבוה = HOLD.\n"
        "5. תזרים מזומנים: FCF חיובי ויציב. חברות שורפות מזומנים = SELL.\n"
        "6. חוב: Debt/Equity > 1.5 מדאיג. > 2.5 = SELL.\n"
        "7. נכסים ספקולטיביים (קריפטו, חברות ללא רווח): SELL.\n"
        "8. ETF מדדי (SPY/VOO): BUY כעוגן לכל משקיע. אג״ח ממשלתי: HOLD כהגנה.\n"
        f"{_DATA_RULE}"
    ),
    "charlie_munger": (
        "אתה צ'ארלי מנגר. מסגרת ההחלטות שלך:\n"
        "1. חשוב בהיפוך: מה יכול להרוס את העסק? אם התשובה 'הרבה דברים' — SELL.\n"
        "2. עסק איכותי במחיר הוגן > עסק בינוני במחיר זול.\n"
        "3. שולי בטיחות (Margin of Safety) הם העיקרון הכי חשוב.\n"
        "4. ROE > 15% באופן עקבי = עסק איכותי. ROE < 8% = בינוני.\n"
        "5. Profit Margin יציב לאורך שנים = חפיר. מרווחים יורדים = אזהרה.\n"
        "6. ספקולציה (קריפטו, meme stocks): 'אל תעשה שטויות' — SELL.\n"
        "7. כפילויות בתיק (QQQM + GOOGL + NVDA): מיותר — מכור את המיותר.\n"
        f"{_DATA_RULE}"
    ),
    "cathie_wood": (
        "את קתי ווד. מסגרת ההחלטות שלך:\n"
        "1. חדשנות דיסרפטיבית: AI, גנומיקה, רובוטיקה, פינטק, אנרגיה נקייה.\n"
        "2. ראייה ל-5 שנים קדימה: TAM (Total Addressable Market) גדול = BUY.\n"
        "3. צמיחת הכנסות > 25% שנתית = חיובי. האטה = אזהרה.\n"
        "4. P/E גבוה מקובל אם צמיחה מצדיקה. PEG < 2 = סביר.\n"
        "5. חברות ישנות/מסורתיות ללא חדשנות: לא מעניינות — HOLD במקרה הטוב.\n"
        "6. ביטחון, אג״ח, ביטוח: לא במוקד תזת החדשנות.\n"
        f"{_DATA_RULE}"
    ),
    "peter_lynch": (
        "אתה פיטר לינץ'. מסגרת ההחלטות שלך:\n"
        "1. 'Invest in what you know' — סיפור ברור שאפשר להסביר בדקה.\n"
        "2. GARP: PEG < 1.0 = BUY מובהק. PEG 1-2 = סביר. PEG > 2 = יקר.\n"
        "3. צמיחת EPS רבעונית עקבית = חיובי. האטה = אזהרה.\n"
        "4. P/E סביר ביחס לקצב הצמיחה. P/E > 40 ללא צמיחה מספקת = SELL.\n"
        "5. עסקים משעממים ורווחיים (ביטוח, ביטחון) = מצוינים.\n"
        "6. מדדים רחבים (SPY/VOO): אין 'סיפור' — מעדיף סיפורים ספציפיים.\n"
        f"{_DATA_RULE}"
    ),
    "michael_burry": (
        "אתה מייקל בורי. מסגרת ההחלטות שלך:\n"
        "1. קונטריאני: אם כולם אומרים BUY, חפש סיבות ל-SELL.\n"
        "2. בועות: P/E מנופח, ציפיות מוגזמות, leverage גבוה = SELL.\n"
        "3. עיוותי מחיר: חברות טובות שנענשו יתר על המידה = BUY.\n"
        "4. מאקרו: ריבית עולה, אינפלציה גבוהה, חוב ממשלתי = סיכון מערכתי.\n"
        "5. קריפטו: ספקולציה טהורה — SELL.\n"
        f"{_DATA_RULE}"
    ),
    "technical_analyst": (
        "אתה אנליסט טכני. כללים:\n"
        "1. מגמה: Price > MA50 > MA200 = uptrend = BUY. Price < MA50 < MA200 = downtrend = SELL.\n"
        "2. Golden Cross (MA50 חוצה מעל MA200) = BUY חזק. Death Cross = SELL חזק.\n"
        "3. RSI < 30 = oversold = הזדמנות כניסה. RSI > 70 = overbought = זהירות.\n"
        "4. RSI 40-60 = ניטרלי. אין איתות = HOLD.\n"
        "5. Volume עולה + מחיר עולה = אישור מגמה. Volume יורד = חולשה.\n"
        "6. Price מעל MA200 אבל מתחת MA50 = תיקון קצר, לא שינוי מגמה.\n"
        "7. אג״ח/קרנות סל ישראליות: תנודתיות נמוכה מאוד = HOLD כברירת מחדל.\n"
        f"{_DATA_RULE}"
    ),
    "fundamentals_analyst": (
        "אתה אנליסט פונדמנטלי. כללים:\n"
        "1. P/E: < 15 = זול, 15-25 = הוגן, > 25 = יקר (תלוי בצמיחה).\n"
        "2. Profit Margin: > 20% = מצוין, 10-20% = טוב, < 5% = בעייתי.\n"
        "3. ROE: > 20% = עסק איכותי, 10-20% = סביר, < 10% = חלש.\n"
        "4. Debt/Equity: < 0.5 = בריא, 0.5-1.5 = סביר, > 1.5 = מסוכן.\n"
        "5. EPS Growth: > 15% = צמיחה חזקה, 5-15% = יציב, < 0% = אזהרה.\n"
        "6. Analyst Target > Price+15% = BUY. Target < Price = SELL.\n"
        "7. ETF/אג״ח: בדוק TER (דמי ניהול) ותשואה לפדיון, לא P/E.\n"
        f"{_DATA_RULE}"
    ),
    "ben_graham": (
        "אתה בן גראהם. כללים:\n"
        "1. ערך עמוק בלבד: P/E < 15, P/B < 1.5.\n"
        "2. שולי בטיחות מוחלטים: קנה רק מתחת לערך פנימי.\n"
        "3. יציבות רווחים: 10 שנות רווחים חיוביים רצופים.\n"
        "4. דיבידנד: עדיף חברות שמחלקות.\n"
        "5. חברות צמיחה עם P/E > 25: מחוץ למעגל הכשירות.\n"
        f"{_DATA_RULE}"
    ),
    "risk_manager": (
        "אתה מנהל סיכונים. כללים:\n"
        "1. ריכוזיות: אחזקה > 15% מהתיק = overweight = SELL חלק. > 20% = SELL מיידי.\n"
        "2. ריכוזיות סקטורית: סקטור > 30% = סיכון. > 40% = SELL.\n"
        "3. קריפטו: אם חורג מתקרת הקריפטו של המשתמש = SELL.\n"
        "4. כפילויות: SPY + VOO + QQQM = חשיפה כפולה. צמצם.\n"
        "5. תנודתיות: Beta > 1.5 = תנודתי מדי לפרופיל שמרני.\n"
        "6. אג״ח ממשלתי / אחזקות הגנתיות: BUY/HOLD — מפחיתות סיכון כולל.\n"
        "7. התמקד בשימור הון, לא בתשואה מקסימלית.\n"
        f"{_DATA_RULE}"
    ),
    "valuation": (
        "אתה מעריך שווי. כללים:\n"
        "1. DCF: אמוד ערך פנימי מתזרים מזומנים. Price < Fair Value = BUY.\n"
        "2. EV/EBITDA: < 10 = זול לרוב הסקטורים. > 20 = יקר.\n"
        "3. P/E vs צמיחה: PEG < 1 = undervalued. PEG > 2 = overvalued.\n"
        "4. Analyst Target Price: משקף ציפיות קונצנזוס.\n"
        f"{_DATA_RULE}"
    ),
    "sentiment": (
        "אתה אנליסט סנטימנט. כללים:\n"
        "1. קונצנזוס אנליסטים: > 70% BUY = חיובי. > 50% SELL = שלילי.\n"
        "2. כותרות חדשות: חדשות שליליות + מחיר יורד = SELL. חדשות חיוביות = BUY.\n"
        "3. פער בין סנטימנט למחיר: אם הסנטימנט שלילי אבל המחיר עולה = חוזק.\n"
        "4. Fear & Greed: פחד קיצוני = הזדמנות. חמדנות קיצונית = זהירות.\n"
        f"{_DATA_RULE}"
    ),
    "macro": (
        "אתה אנליסט מאקרו. כללים:\n"
        "1. ריבית: Fed Rate עולה = לחץ על מניות צמיחה. יורדת = תמיכה.\n"
        "2. אינפלציה: CPI > 4% = סיכון. < 2% = סביבה תומכת מניות.\n"
        "3. VIX: < 15 = שוק רגוע. 15-25 = נורמלי. > 25 = פחד. > 35 = פאניקה.\n"
        "4. עקום תשואות הפוך (10Y < Fed Rate) = אות מיתון.\n"
        "5. אג״ח: ריבית עולה = פוגע באג״ח ארוך (F77 > F34). ריבית יורדת = טוב לאג״ח.\n"
        "6. דולר/שקל: שקל חלש = טוב לתיק דולרי. שקל חזק = שוחק תשואה.\n"
        f"{_DATA_RULE}"
    ),
}

PERSONA_DISPLAY_HE = {
    "warren_buffett": "וורן באפט (ערך)",
    "charlie_munger": "צ'ארלי מנגר (ערך)",
    "cathie_wood": "קתי ווד (חדשנות)",
    "peter_lynch": "פיטר לינץ' (צמיחה)",
    "michael_burry": "מייקל בורי (קונטרי)",
    "technical_analyst": "ניתוח טכני",
    "fundamentals_analyst": "ניתוח פונדמנטלי",
    "ben_graham": "בן גראהם (ערך עמוק)",
    "risk_manager": "מנהל סיכונים",
    "valuation": "הערכת שווי",
    "sentiment": "סנטימנט שוק",
    "macro": "מאקרו",
}

PER_TICKER_SCHEMA = (
    'החזר JSON בלבד בפורמט הבא (ללא markdown fences):\n'
    '{"verdict": "buy|hold|sell", "conviction": 0-100, "rationale": "2-3 משפטים בעברית"}'
)

# Brief asset descriptions injected into the Gemini prompt so the model
# knows what each ticker actually is — especially obscure Israeli ETFs.
TICKER_DESCRIPTIONS = {
    "GOOGL": "Alphabet Inc (Google) — US mega-cap tech, search & cloud & AI",
    "AMZN": "Amazon — US e-commerce + AWS cloud",
    "BAM": "Brookfield Asset Management — alternative asset manager",
    "BN": "Brookfield Corporation — parent holding of Brookfield ecosystem",
    "CPNG": "Coupang — South Korean e-commerce",
    "ETHA": "iShares Ethereum Trust ETF — spot Ethereum ETF",
    "XLV": "Health Care Select Sector SPDR — US healthcare sector ETF",
    "IBIT": "iShares Bitcoin Trust ETF — spot Bitcoin ETF",
    "QQQM": "Invesco Nasdaq 100 ETF — tracks the Nasdaq-100 index",
    "ITA": "iShares US Aerospace & Defense ETF",
    "NVDA": "Nvidia — GPU & AI chip maker",
    "SPY": "SPDR S&P 500 ETF Trust — tracks the S&P 500 index (USD)",
    "URNM": "Sprott Uranium Miners ETF — uranium mining companies",
    "NLR": "VanEck Uranium+Nuclear Energy ETF — nuclear energy sector",
    "VOO": "Vanguard S&P 500 ETF — tracks the S&P 500 index (USD)",
    "5108.TA": "TA-Insurance Index ETF — tracks the Tel Aviv Insurance sector index, traded on TASE in ILS",
    "KSM-F34.TA": "Israel Government Bond Fund (medium-term) — אג״ח ממשלתי שקלי, traded on TASE in agorot. NOT an equity fund.",
}


def _gemini() -> object:
    """Instantiate a Gemini chat client. Raises on missing key."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("[error] GEMINI_API_KEY (or GOOGLE_API_KEY) not set in .env", file=sys.stderr)
        sys.exit(2)
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
    except ImportError:
        print("[error] langchain-google-genai not installed. Run: "
              "pip install langchain-google-genai", file=sys.stderr)
        sys.exit(2)
    # Tiered model strategy (configurable via env vars):
    # - PRIMARY: best quality (e.g. gemini-3-flash-preview)
    # - FALLBACK: free tier with high limits (gemini-2.5-flash-lite, 1000 RPD)
    # On rate limit errors, _invoke_with_retry auto-falls back to fallback model.
    model_name = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    return ChatGoogleGenerativeAI(
        model=model_name, google_api_key=api_key,
        temperature=0.3, timeout=45, max_retries=0,  # we do our own retries below
    )


def _gemini_fallback() -> object:
    """Fallback model — cheaper with higher free-tier limits (1000 RPD)."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        model_name = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")
        return ChatGoogleGenerativeAI(
            model=model_name, google_api_key=api_key,
            temperature=0.3, timeout=45, max_retries=0,
        )
    except Exception:
        return None


_fallback_llm_cache = [None]  # lazy-init fallback


def _invoke_with_retry(llm, messages, attempts: int = 3):
    """Invoke with smart fallback: on rate limit, switch to cheaper model.

    Strategy:
    1. Try primary model (e.g. gemini-3-flash-preview)
    2. On 429/quota → switch to fallback (gemini-2.5-flash-lite, 1000 RPD free)
    3. Retry on transient errors (5xx, timeouts)
    """
    import time
    last_err = None
    current_llm = llm
    used_fallback = False

    for i in range(attempts):
        try:
            return current_llm.invoke(messages)
        except Exception as e:
            err = str(e)
            last_err = e
            is_quota = any(s in err for s in ("429", "RESOURCE_EXHAUSTED", "quota"))
            is_transient = any(s in err for s in ("503", "502", "504", "UNAVAILABLE",
                                                    "timeout", "Timeout", "DEADLINE"))

            # On quota error, switch to fallback model immediately
            if is_quota and not used_fallback:
                if _fallback_llm_cache[0] is None:
                    _fallback_llm_cache[0] = _gemini_fallback()
                if _fallback_llm_cache[0] is not None:
                    print(f"  [fallback] primary model quota exceeded, "
                          f"switching to fallback model", file=sys.stderr)
                    current_llm = _fallback_llm_cache[0]
                    used_fallback = True
                    continue  # retry immediately with fallback

            if not (is_quota or is_transient) or i == attempts - 1:
                raise
            wait = min(15, (2 ** i) + 1)
            print(f"  [retry {i+1}/{attempts}] transient error, waiting {wait}s: {err[:100]}",
                  file=sys.stderr)
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("all retries exhausted")


def _parse_persona_json(text: str) -> dict | None:
    """Extract first JSON object from the model's response (Gemini sometimes wraps in ``` or prose)."""
    # Strip code fences if present
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _call_persona(llm, persona: str, ticker: str, display_name: str,
                   preamble: str, market_context: str = "") -> dict:
    """Single Gemini call for one (persona × ticker). Returns persona entry dict."""
    system = PERSONA_SYSTEM_PROMPTS.get(persona, "אתה אנליסט השקעות מקצועי.")
    desc = TICKER_DESCRIPTIONS.get(ticker, "")
    desc_line = f"\nAsset description: {desc}\n" if desc else "\n"
    mkt_block = f"\n{market_context}\n" if market_context else ""
    user = (
        f"{preamble}\n\n"
        f"עכשיו תן ניתוח לנכס: **{display_name} ({ticker})**.\n"
        f"{desc_line}"
        f"{mkt_block}"
        f"התבסס על הפרופיל של המשתמש ועל הנתונים למעלה. אל תמציא מספרים שלא ניתנו לך. "
        f"{PER_TICKER_SCHEMA}"
    )
    try:
        resp = _invoke_with_retry(llm, [("system", system), ("user", user)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        # Gemini sometimes returns a list of content parts (text + function calls).
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict) and "text" in part:
                    text_parts.append(part["text"])
                elif hasattr(part, "text"):
                    text_parts.append(part.text)
            content = "\n".join(text_parts) if text_parts else ""
        if not isinstance(content, str):
            content = str(content)
    except Exception as e:
        return {
            "name": persona,
            "display_name": PERSONA_DISPLAY_HE.get(persona, persona),
            "verdict": "hold", "conviction": 0,
            "rationale": f"[שגיאת Gemini: {str(e)[:120]}]",
        }

    parsed = _parse_persona_json(content) or {}
    verdict = (parsed.get("verdict") or "hold").lower()
    if verdict not in ("buy", "hold", "sell"):
        verdict = "hold"
    return {
        "name": persona,
        "display_name": PERSONA_DISPLAY_HE.get(persona, persona),
        "verdict": verdict,
        "conviction": int(parsed.get("conviction", 50)),
        "rationale": parsed.get("rationale") or content[:400],
    }


def _aggregate_verdict(persona_entries: list[dict]) -> tuple[str, int]:
    """Aggregate persona verdicts into a single (verdict, conviction).

    Uses weighted voting across ALL personas — dissenters pull conviction
    down, and a unanimity bonus rewards strong consensus.
    """
    scores = {"buy": 0, "hold": 0, "sell": 0}
    counts = {"buy": 0, "hold": 0, "sell": 0}
    for p in persona_entries:
        v = p.get("verdict", "hold")
        c = int(p.get("conviction", 0))
        scores[v] = scores.get(v, 0) + c
        counts[v] = counts.get(v, 0) + 1
    total_personas = max(1, sum(counts.values()))
    if sum(scores.values()) == 0:
        return "hold", 0
    top_verdict = max(scores, key=scores.get)
    # Average conviction across ALL personas (not just agreeing ones)
    avg_conviction = sum(scores.values()) / total_personas
    # Unanimity factor: 1.0 if all agree, lower if split
    unanimity = counts[top_verdict] / total_personas
    adjusted = int(avg_conviction * (0.7 + 0.3 * unanimity))
    return top_verdict, min(100, max(0, adjusted))


def run_real(settings: dict, portfolio: dict) -> dict:
    """Real run: calls Gemini directly for each (persona × ticker)."""
    tickers = _tickers(portfolio)
    if not tickers:
        print("[error] portfolio.json has no holdings", file=sys.stderr)
        sys.exit(2)

    preamble = _build_profile_preamble(settings)

    # Resolve display names from config for prompt clarity
    sys.path.insert(0, str(_ROOT))
    try:
        from config import DISPLAY_NAMES  # type: ignore
    except Exception:
        DISPLAY_NAMES = {}

    llm = _gemini()
    max_workers = int(os.environ.get("GEMINI_CONCURRENCY", "6"))
    print(f"[info] scoring {len(tickers)} holdings "
          f"(1 Gemini synthesis call each + summary + new ideas)")

    # ── Fetch ALL market data so personas get REAL numbers ──────────────
    print("[info] fetching market data (quotes, technicals, fundamentals, macro, news)…",
          file=sys.stderr, flush=True)

    # 1. Yahoo Finance: live quotes + historical OHLCV + technicals
    try:
        from data_loader import fetch_live_quotes, fetch_historical_data
        _quotes_df = fetch_live_quotes(tickers)
        _quotes = {idx: row.to_dict()
                   for idx, row in _quotes_df.iterrows()} if not _quotes_df.empty else {}
        _historical = fetch_historical_data(tickers, period="1y")
        _technicals = {tk: _compute_technicals(_historical.get(tk))
                       for tk in tickers}
    except Exception as e:
        print(f"[warn] YF data fetch failed ({e}); proceeding without",
              file=sys.stderr)
        _quotes, _technicals = {}, {}

    # 2. Alpha Vantage: fundamentals (P/E, margins, analyst targets)
    try:
        from data_loader_fundamentals import fetch_all_fundamentals, fetch_all_news
        _fundamentals = fetch_all_fundamentals(tickers)
        _news = fetch_all_news(tickers, max_items=3)
    except Exception as e:
        print(f"[warn] fundamentals/news fetch failed ({e}); proceeding without",
              file=sys.stderr)
        _fundamentals, _news = {}, {}

    # 3. Macro: FRED + Yahoo Finance (VIX, indices)
    try:
        from data_loader_macro import fetch_macro_snapshot
        _macro = fetch_macro_snapshot()
    except Exception as e:
        print(f"[warn] macro fetch failed ({e}); proceeding without",
              file=sys.stderr)
        _macro = {}

    # 3b. Social sentiment via Perplexity (optional — skips if PERPLEXITY_API_KEY unset)
    try:
        from data_loader_social import fetch_all_social_sentiment
        _social = fetch_all_social_sentiment(tickers)
    except Exception as e:
        print(f"[warn] social sentiment fetch failed ({e}); proceeding without",
              file=sys.stderr)
        _social = {}

    # 4. Compute portfolio weights for risk_manager persona
    try:
        _total_value = sum(
            (_quotes.get(tk, {}).get("price", 0) or 0) *
            (h.get("quantity", 0) or 0)
            for h in portfolio.get("holdings", [])
            for tk in [h.get("ticker", "")]
        )
        _weights = {
            h.get("ticker", ""): (
                (_quotes.get(h.get("ticker", ""), {}).get("price", 0) or 0)
                * (h.get("quantity", 0) or 0) / max(1, _total_value) * 100
            ) for h in portfolio.get("holdings", [])
        }
    except Exception:
        _weights = {}

    # Sector weights
    try:
        from config import SECTOR_MAP
        _sector_weights: dict[str, float] = {}
        for tk, w in _weights.items():
            sec = SECTOR_MAP.get(tk, "Other")
            _sector_weights[sec] = _sector_weights.get(sec, 0) + w
    except Exception:
        _sector_weights = {}

    # ── Compute algorithmic scores per ticker ──────────────────────────
    try:
        from scoring_engine import compute_all_scores as _score_all
        from config import ASSET_TYPE_MAP
    except ImportError:
        _score_all = None
        ASSET_TYPE_MAP = {}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    holdings_out = []
    for i, tk in enumerate(tickers, 1):
        display = DISPLAY_NAMES.get(tk, tk)
        tk_sector = _sector_of(tk)
        tk_weight = _weights.get(tk, 0)
        tk_sec_weight = _sector_weights.get(tk_sector, 0)

        # Scoring engine: compute 6 data-driven scores
        scores = {}
        if _score_all:
            try:
                scores = _score_all(
                    tk, _quotes.get(tk, {}), _technicals.get(tk, {}),
                    _fundamentals.get(tk), _macro, _news.get(tk, []),
                    tk_weight, tk_sec_weight,
                    ASSET_TYPE_MAP.get(tk, ""),
                    settings.get("crypto_cap_pct", 10),
                    social_sentiment=_social.get(tk))
            except Exception as e:
                print(f"  [warn] scoring failed for {tk}: {e}", file=sys.stderr)

        # Build market context for this ticker
        _mkt_ctx = _build_full_context(
            tk, "fundamentals_analyst",  # generic context
            _quotes.get(tk, {}), _technicals.get(tk, {}),
            _fundamentals.get(tk), _macro, _news.get(tk, []),
            tk_weight, tk_sec_weight)

        # ── SCORING ENGINE: algorithmic scores + 1 Gemini synthesis call ──
        _scoring_weights = settings.get("scoring_weights")
        if scores:
            from scoring_engine import scores_to_verdict as _s2v, explain_scores as _explain
            # Verdict AND conviction come 100% from the scoring engine
            # (weighted average of 6 scores, using user's strategy weights)
            algo_v, algo_c = _s2v(scores, _scoring_weights)
            # Gemini is ONLY used for the Hebrew rationale text — not the verdict
            synth = _scoring_synthesis_call(
                llm, tk, display, preamble, scores, _mkt_ctx)
            # Generate human-readable explanations per score
            details = _explain(scores, _quotes.get(tk, {}), _technicals.get(tk, {}),
                               _fundamentals.get(tk), _macro, tk_weight, tk_sec_weight)
            # Extract analyst consensus from fundamentals
            _f = _fundamentals.get(tk) or {}
            analyst_data = {
                "buy": _f.get("analyst_buy", 0),
                "hold": _f.get("analyst_hold", 0),
                "sell": _f.get("analyst_sell", 0),
                "target": _f.get("analyst_target"),
                "price": _quotes.get(tk, {}).get("price"),
            }
            # Position sizing + exit triggers
            try:
                from position_sizing import compute_position_size, compute_exit_triggers
                _wavg = sum(scores[k] * _scoring_weights.get(k, 0) for k in scores) / max(
                    1, sum(_scoring_weights.values()))
                _strategy = settings.get("scoring_strategy", "conservative_longterm")
                position_rec = compute_position_size(
                    scores, _wavg, scores.get("risk", 50),
                    current_weight=tk_weight, sector_weight=tk_sec_weight,
                    is_crypto="Crypto" in (ASSET_TYPE_MAP.get(tk, "") or ""),
                    crypto_cap=settings.get("crypto_cap_pct", 10),
                    strategy=_strategy, is_new_position=False,
                )
                exit_triggers = compute_exit_triggers(
                    algo_v, _wavg,
                    _quotes.get(tk, {}).get("price", 0),
                    _technicals.get(tk, {}).get("ma200"),
                    _fundamentals.get(tk), strategy=_strategy,
                )
            except Exception:
                position_rec, exit_triggers = {}, {}

            holding = {
                "ticker": tk, "verdict": algo_v, "conviction": algo_c,
                "scores": scores,
                "score_details": details,
                "analyst_consensus": analyst_data,
                "social_sentiment": _social.get(tk),  # Twitter/X data from Perplexity
                "position_sizing": position_rec,
                "exit_triggers": exit_triggers,
                "rationale": synth.get("rationale", ""),
                "personas": [],
            }
        else:
            # Fallback if scoring failed: return neutral HOLD.
            # We never trust Gemini for verdict/conviction — it only writes text.
            synth = _scoring_synthesis_call(
                llm, tk, display, preamble, {}, _mkt_ctx)
            holding = {
                "ticker": tk,
                "verdict": "hold",       # safe default — no scoring = no signal
                "conviction": 30,         # low conviction — we have no data
                "rationale": synth.get("rationale", "") or "Insufficient data for analysis.",
                "personas": [],
            }

        holdings_out.append(holding)
        scores_str = " ".join(f"{k[:3].upper()}={v}" for k, v in scores.items()) if scores else ""
        print(f"  [{i}/{len(tickers)}] {tk}: {holding['verdict'].upper()} "
              f"{holding['conviction']}%  {scores_str}",
              flush=True)

    # ── Candidate search → score → filter to actionable ideas ─────────────
    # Minimum score to actually recommend an idea. Below this, the idea is noise.
    MIN_IDEA_SCORE = 60
    TARGET_IDEAS = 3
    MAX_ROUNDS = 2
    _strategy = settings.get("scoring_strategy", "conservative_longterm")

    scored_candidates = []  # (score, idea_dict)
    rejected_tickers = set(tickers)  # don't suggest these again

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"[info] new-ideas search round {round_num}: asking Gemini for 10 candidates…",
              flush=True)
        candidates = _generate_new_ideas_candidates(
            llm, preamble, list(rejected_tickers),
            strategy=_strategy, n_candidates=10)
        if not candidates:
            break

        # Score all candidates
        cand_tickers = [c["ticker"] for c in candidates]
        try:
            from data_loader import fetch_live_quotes as _flq, fetch_historical_data as _fhd
            _iq_df = _flq(cand_tickers)
            _iq = {idx: row.to_dict()
                   for idx, row in _iq_df.iterrows()} if not _iq_df.empty else {}
            _ih = _fhd(cand_tickers, period="1y")
            _it = {tk: _compute_technicals(_ih.get(tk)) for tk in cand_tickers}
            try:
                from data_loader_fundamentals import fetch_all_fundamentals as _faf
                from data_loader_fundamentals import fetch_all_news as _fan
                _if = _faf(cand_tickers)
                _inews = _fan(cand_tickers)
            except Exception:
                _if, _inews = {}, {}
            from scoring_engine import (
                scores_to_verdict as _s2v_idea,
                explain_scores as _iexplain,
            )
            _idea_weights = settings.get("scoring_weights")

            for cand in candidates:
                itk = cand["ticker"]
                try:
                    i_scores = _score_all(
                        itk, _iq.get(itk, {}), _it.get(itk, {}),
                        _if.get(itk), _macro, _inews.get(itk, []),
                        0, 0, "", settings.get("crypto_cap_pct", 10))
                    _, idea_c = _s2v_idea(i_scores, _idea_weights)

                    # Calculate the weighted average directly for filtering
                    wavg = (sum(i_scores[k] * _idea_weights.get(k, 0) for k in i_scores)
                            / max(1, sum(_idea_weights.values())))

                    cand["scores"] = i_scores
                    cand["conviction"] = idea_c
                    cand["suggested_price"] = _iq.get(itk, {}).get("price", 0)
                    cand["score_details"] = _iexplain(
                        i_scores, _iq.get(itk, {}), _it.get(itk, {}),
                        _if.get(itk), _macro, 0, 0)
                    _fi = _if.get(itk) or {}
                    cand["analyst_consensus"] = {
                        "buy": _fi.get("analyst_buy", 0),
                        "hold": _fi.get("analyst_hold", 0),
                        "sell": _fi.get("analyst_sell", 0),
                        "target": _fi.get("analyst_target"),
                        "price": _iq.get(itk, {}).get("price"),
                    }
                    print(f"  candidate {itk}: wavg={wavg:.0f} conviction={idea_c}%",
                          flush=True)

                    if wavg >= MIN_IDEA_SCORE:
                        scored_candidates.append((wavg, cand))
                    rejected_tickers.add(itk)
                except Exception as e:
                    print(f"  [warn] scoring failed for {itk}: {e}", file=sys.stderr)
                    rejected_tickers.add(itk)
        except Exception as e:
            print(f"[warn] idea data fetch failed: {e}", file=sys.stderr)

        # If we have enough qualifying ideas, stop
        if len(scored_candidates) >= TARGET_IDEAS:
            break

    # Keep only the top N qualifying ideas
    scored_candidates.sort(key=lambda x: -x[0])
    new_ideas = [c for _, c in scored_candidates[:TARGET_IDEAS]]

    if not new_ideas:
        print(f"[info] no new ideas passed score ≥{MIN_IDEA_SCORE} threshold this run",
              flush=True)
    else:
        print(f"[ok] {len(new_ideas)} new ideas qualified: "
              f"{', '.join(i['ticker'] for i in new_ideas)}", flush=True)

    # Generate a 2-4 sentence Hebrew daily summary from the aggregate
    summary = _generate_summary(llm, preamble, holdings_out, new_ideas)

    # Generate SMART portfolio insights — one call to the smart model
    smart_insights = {}
    try:
        from smart_analysis import generate_smart_insights, get_smart_llm
        smart_llm = get_smart_llm()
        if smart_llm is not None:
            print("[info] generating smart portfolio insights (1 call)…", flush=True)
            smart_insights = generate_smart_insights(
                smart_llm,
                {"holdings": holdings_out, "new_ideas": new_ideas},
                _macro, settings)
            print(f"[ok] smart insights: {smart_insights.get('headline', '')[:60]}",
                  flush=True)
    except Exception as e:
        print(f"[warn] smart insights failed: {e}", file=sys.stderr)

    return {
        "updated": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "profile_name": settings.get("profile_name", ""),
        "summary": summary,
        "smart_insights": smart_insights,
        "holdings": holdings_out,
        "new_ideas": new_ideas,
        "dry_run": False,
    }


def _scoring_synthesis_call(llm, ticker: str, display_name: str,
                            preamble: str, scores: dict,
                            market_context: str) -> dict:
    """Single Gemini call to synthesize algorithmic scores into a Hebrew verdict.

    Single Gemini call to synthesize algorithmic scores into a Hebrew verdict.
    """
    scores_block = "\n".join(
        f"  {k.title():12s} {v}/100 {'(strong)' if v > 70 else '(weak)' if v < 30 else ''}"
        for k, v in scores.items()
    )
    avg = sum(scores.values()) / max(1, len(scores))
    system = (
        "אתה אנליסט השקעות מקצועי. אתה מקבל ציונים אלגוריתמיים (0-100) לכל קטגוריה "
        "ונתוני שוק אמיתיים. תפקידך: לסנתז את הכל לדירוג סופי + נימוק בעברית. "
        "אל תמציא מספרים — השתמש רק בנתונים שמסופקים."
    )
    desc = TICKER_DESCRIPTIONS.get(ticker, "")
    user = (
        f"{preamble}\n\n"
        f"נכס: **{display_name} ({ticker})**\n"
        f"{'Asset: ' + desc if desc else ''}\n\n"
        f"ALGORITHMIC SCORES:\n{scores_block}\n"
        f"  Average: {avg:.0f}/100\n\n"
        f"{market_context}\n\n"
        f"בהתבסס על הציונים והנתונים, תן ניתוח. {PER_TICKER_SCHEMA}"
    )
    try:
        resp = _invoke_with_retry(llm, [("system", system), ("user", user)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "") if isinstance(p, dict)
                else p.text if hasattr(p, "text") else str(p)
                for p in content
            )
        if not isinstance(content, str):
            content = str(content)
    except Exception as e:
        return {"verdict": "hold", "conviction": 50,
                "rationale": f"[שגיאת Gemini: {str(e)[:120]}]"}

    parsed = _parse_persona_json(content) or {}
    verdict = (parsed.get("verdict") or "hold").lower()
    if verdict not in ("buy", "hold", "sell"):
        verdict = "hold"
    return {
        "verdict": verdict,
        "conviction": int(parsed.get("conviction", 50)),
        "rationale": parsed.get("rationale") or content[:400],
    }


def _generate_new_ideas_candidates(llm, preamble: str, existing_tickers: list[str],
                                     strategy: str = "conservative_longterm",
                                     n_candidates: int = 10) -> list[dict]:
    """Ask Gemini for MANY candidates pre-filtered by our scoring criteria.

    The prompt tells Gemini EXACTLY what we look for so it doesn't waste suggestions
    on tickers we'd reject anyway.
    """
    existing_str = ", ".join(existing_tickers)

    # Tell Gemini our exact scoring criteria
    criteria_by_strategy = {
        "conservative_longterm": (
            "• Quality (30% weight): ROE > 15%, profit margin > 20%, debt/equity < 0.5, "
            "revenue growth > 10%\n"
            "• Valuation (25%): P/E below sector average, PEG < 1.5, analyst upside > 10%\n"
            "• Risk (20%): market leader, low volatility (beta 0.7-1.3)\n"
            "• Macro (15%): benefits from current rate environment\n"
            "• Sentiment (5%): >70% analyst BUY consensus\n"
            "• Trend (5%): price above MA200, RSI 40-70"
        ),
        "value": (
            "• P/E < 15 AND P/B < 2.5 (Graham criteria)\n"
            "• Profit margin > 15% AND ROE > 12% (quality at cheap prices)\n"
            "• Debt/equity < 0.5 (financial safety)\n"
            "• Analyst target suggests 20%+ upside\n"
            "• Revenue growth positive (not declining)"
        ),
        "growth": (
            "• Revenue growth > 25% (high growth)\n"
            "• EPS growth > 20%\n"
            "• Strong momentum: price > MA50 > MA200, RSI 50-75\n"
            "• Analyst BUY consensus > 65%\n"
            "• Disruptive industry (AI, fintech, biotech, robotics)"
        ),
        "income": (
            "• Dividend yield > 2.5%\n"
            "• Low beta (<1.2) — stable\n"
            "• ROE > 12%, debt/equity < 1.0\n"
            "• Mature, profitable businesses"
        ),
        "balanced": (
            "• P/E near sector average\n"
            "• ROE > 12%, margins > 15%\n"
            "• Analyst BUY > 55%\n"
            "• Technical uptrend (above MA200)"
        ),
    }
    criteria = criteria_by_strategy.get(strategy, criteria_by_strategy["conservative_longterm"])

    system = (
        "You are a senior equity research analyst. Your job: find US-listed tickers "
        "that match SPECIFIC quantitative criteria. Only suggest stocks you're highly "
        "confident will meet ALL the thresholds below. Quality over quantity."
    )

    user = (
        f"{preamble}\n\n"
        f"User already holds: {existing_str}\n\n"
        f"CRITERIA (a stock must match most of these to be a good fit):\n{criteria}\n\n"
        f"Find {n_candidates} US-listed tickers (NYSE/Nasdaq only, NO .TA or foreign) "
        f"that are HIGHLY LIKELY to pass these criteria based on known financial data. "
        f"Prefer mega-cap and large-cap stocks with extensive analyst coverage (>15 analysts).\n\n"
        f"Avoid:\n"
        f"- Tickers already in portfolio\n"
        f"- Meme stocks, penny stocks, SPACs\n"
        f"- Sectors in the user's avoid list\n"
        f"- Speculative crypto plays beyond the user's crypto cap\n\n"
        f"Return JSON only:\n"
        '{"ideas": [{"ticker": "SYM", "name": "Company", "rationale": "2 sentences in Hebrew '
        'explaining WHY this ticker likely meets the criteria"}, ...]}'
    )

    try:
        resp = _invoke_with_retry(llm, [("system", system), ("user", user)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        if isinstance(content, list):
            content = "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        if not isinstance(content, str):
            content = str(content)
    except Exception as e:
        print(f"[warn] new_ideas call failed: {e}", file=sys.stderr)
        return []

    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    ideas = data.get("ideas") or []
    cleaned = []
    for i in ideas[:n_candidates]:
        tk = i.get("ticker")
        if not tk:
            continue
        tk = tk.upper().strip()
        if tk in existing_tickers:
            continue
        if tk.endswith(".TA"):
            continue  # no Israeli (data limits)
        cleaned.append({
            "ticker": tk,
            "name": i.get("name", tk),
            "rationale": i.get("rationale", ""),
        })
    return cleaned


def _generate_new_ideas(llm, preamble: str, existing_tickers: list[str]) -> list[dict]:
    """Backward-compat wrapper. Returns up to 3 candidates (will be filtered later)."""
    return _generate_new_ideas_candidates(llm, preamble, existing_tickers, n_candidates=10)


def _generate_summary(llm, preamble: str, holdings: list[dict], new_ideas: list[dict]) -> str:
    """Ask Gemini for a 2-4 sentence Hebrew digest of the day's verdicts."""
    strong_buys = [h["ticker"] for h in holdings
                   if h["verdict"] == "buy" and h["conviction"] >= 75]
    strong_sells = [h["ticker"] for h in holdings
                    if h["verdict"] == "sell" and h["conviction"] >= 75]
    bullet_lines = "\n".join(
        f"- {h['ticker']}: {h['verdict'].upper()} ({h['conviction']}%)"
        for h in holdings
    )
    ideas_str = ", ".join(i["ticker"] for i in new_ideas)
    user = (
        f"{preamble}\n\n"
        f"תוצאות ההרצה היום:\n{bullet_lines}\n\n"
        f"רעיונות חדשים: {ideas_str or '(אין)'}\n\n"
        "כתוב סיכום יומי של 2-4 משפטים בעברית, ענייני ומועיל לפעולה. "
        "החזר טקסט גולמי בלבד, ללא JSON, ללא כותרות."
    )
    try:
        resp = _invoke_with_retry(llm, [("system", "אתה כותב סיכומי שוק קצרים."), ("user", user)])
        content = resp.content if hasattr(resp, "content") else str(resp)
        if isinstance(content, list):
            content = "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        if not isinstance(content, str):
            content = str(content)
        return content.strip()
    except Exception:
        parts = [f"סיכום יומי — {len(holdings)} החזקות נסקרו."]
        if strong_buys:
            parts.append(f"קניות חזקות: {', '.join(strong_buys)}.")
        if strong_sells:
            parts.append(f"מכירות חזקות: {', '.join(strong_sells)}.")
        return " ".join(parts)


# ─── Dry-run mock (Hebrew rationales, mock scores, settings-aware) ──────────

# Persona display names in Hebrew
PERSONA_NAMES_HE = {
    "warren_buffett": "וורן באפט (ערך)",
    "charlie_munger": "צ'ארלי מנגר (ערך)",
    "cathie_wood": "קתי ווד (חדשנות)",
    "peter_lynch": "פיטר לינץ' (צמיחה)",
    "michael_burry": "מייקל בורי (קונטרי)",
    "risk_manager": "מנהל סיכונים",
    "technical_analyst": "ניתוח טכני",
    "fundamentals_analyst": "ניתוח פונדמנטלי",
    "sentiment": "סנטימנט שוק",
    "valuation": "הערכת שווי",
    "macro": "מאקרו",
}


def _persona_rationale(persona: str, ticker: str, verdict: str, settings: dict) -> str:
    """Return a Hebrew 1-sentence rationale per persona, tuned to the ticker + user settings."""
    crypto_cap = settings.get("crypto_cap_pct", 10)
    horizon = settings.get("horizon_years", 3)
    style = settings.get("style", "conservative")
    avoid = set(settings.get("avoid_sectors", []))
    sector = _sector_of(ticker)

    # Detailed (2-3 sentence) Hebrew rationales per persona × ticker
    base = {
        "warren_buffett": {
            "GOOGL": "אלפבית היא עסק עם חפיר תחרותי עמוק: מונופול דה-פקטו בחיפוש, מערכת הפעלה דומיננטית (Android), ו-YouTube שמייצרים תזרים מזומנים עצום. הסיכון העיקרי הוא קניבליזציה של החיפוש ע״י AI, אך גוגל עצמה מובילה את מרוץ ה-AI עם Gemini ו-TPU. המחיר הנוכחי סביר יחסית לאיכות — מועמדת מובהקת לתיק ערך לטווח ארוך.",
            "AMZN":  "AWS הוא נכס איכותי מהשורה הראשונה — מרווחים תפעוליים של 35%+ ושוק שצומח. הקמעונאות מוסיפה רעש ומרווחים דקים, אך הופכת יחסית ליותר רווחית. התמחור לא זול, אך האיכות הכוללת גבוהה מאוד — החזקה מוצדקת.",
            "BN":    "ברוקפילד קורפ מחזיקה ב-75%+ מברוקפילד Asset Management ובנכסים ריאליים (תשתיות, נדל״ן, אנרגיה מתחדשת) — חפיר תחרותי בהפצה ובגודל. מודל ההכנסות משילוב דמי ניהול ועמלות הצלחה יציב לאורך מחזורים. דיבידנד צומח באופן עקבי — עסק איכותי לטווח ארוך.",
            "SPY":   "אני ממליץ לכל משקיע פרטי להחזיק מדד S&P 500 זול ולשכוח ממנו — זה התיק הכי טוב לרוב האנשים. גם לאמית, זהו עוגן התיק. הבעיה היחידה: SPY יקר יותר מ-VOO בדמי ניהול (0.09% מול 0.03%).",
            "VOO":   "עדיפה על SPY — אותו מוצר בדיוק, אבל דמי ניהול נמוכים יותר פי 3. לאורך 30 שנה ההבדל הזה מצטבר לאלפי דולרים. עבור תיק שנבנה בתרומות חודשיות קבועות, VOO היא הבחירה הרציונלית.",
            "XLV":   "סקטור הבריאות הוא הגנתי, לא-מחזורי, וביקושו גדל עם הזדקנות האוכלוסייה. החפיר התחרותי מורכב מפטנטים, רגולציה (FDA), ורשתות הפצה. התשואה ההיסטורית עומדת על 10%+ שנתית עם תנודתיות נמוכה יחסית — התאמה מצוינת לפרופיל שמרני.",
            "NVDA":  "חברה נהדרת ויצרנית ה-GPU הדומיננטית לעידן ה-AI, אבל P/E של 50+ משאיר שולי בטיחות דקים מאוד. הסיכון: כל כשל בציפיות צמיחה יפגע במחיר חזק. אני מעדיף להמתין לתיקון של 20-30% לפני שאגדיל פוזיציה.",
            "CPNG":  "השוק הקוריאני ה-e-commerce צפוף: נאבר, שופלייבה, אלי אקספרס, קאוופאנג. חפיר תחרותי לא ברור ומרווחים דקים. ללא יתרון מובהק, לא עומדת בסטנדרט הערך שלי — לעבור הלאה.",
            "IBIT":  "ביטקוין הוא נכס ספקולטיבי שאינו מייצר תזרים מזומנים. אני לא משקיע במשהו שאיני יכול להעריך בו ערך פנימי (DCF). מבחינת הפרופיל של אמית — תקרת הקריפטו של %d%% כבר חורגת; הגיוני להקטין." % crypto_cap,
            "ETHA":  "אותו עיקרון כמו IBIT — נכס ללא תזרים מזומנים מסחרי. את׳ריום אולי מיועד לאפליקציות DeFi, אך עדיין ספקולציה טכנולוגית ולא השקעה בעסק. לא מתאים לפרופיל ערך.",
            "ITA":   "חברות הביטחון האמריקאיות (LMT, RTX, NOC, GD) יציבות, עם חוזים ממשלתיים ארוכי-טווח ותזרים מזומנים צפוי. התקציבים הביטחוניים בעלייה — סביבה מקרו תומכת. פסיבית, שקטה, מצוינת לפרופיל שמרני.",
            "URNM":  "אורניום מחזורי מאוד — אם מחירי הספוט עולים, ההכנסות של חברות הכרייה מזנקות. ביקוש מאושש מצד datacenters של AI ומדיניות 'חזרה לגרעין'. תנודתיות גבוהה — לא לגעת מעל 5% משקל.",
            "NLR":   "דומה ל-URNM אך מגוון יותר — כולל גם שירותי חשמל גרעיניים (NextEra, Constellation). יציבות גבוהה יותר בזכות רגולציה מורכבת שיוצרת חפיר תחרותי. התאמה טובה לפרופיל.",
            "QQQM":  "חשיפה לנאסד״ק 100 — חברות איכות גבוהה אך בתמחור מתוח מאוד. חופף משמעותית עם החזקות GOOGL, NVDA, AMZN שכבר קיימות בתיק. מיותר — SPY/VOO מספיקים כעוגן.",
            "KSM-F34.TA": "קרן האג״ח הממשלתי הישראלי (F34) — אג״ח ממשלתי שקלי לטווח בינוני. יציבות גבוהה, סיכון נמוך, חשיפה שקלית הגנתית שמפחיתה סיכון מט״ח בתיק דולרי. מתאימה לפרופיל שמרני.",
            "KSM-F77.TA": "קרן האג״ח הממשלתי הישראלי (F77) — אג״ח ממשלתי שקלי לטווח ארוך. ריבית קופון יציבה, תנודתיות נמוכה מאוד. חשיפה שקלית הגנתית — נכס ליבה להפחתת סיכון מטבע.",
        },
        "charlie_munger": {
            "GOOGL": "עסק מעולה במחיר הוגן — הסוג שאני וורן מחפשים לאורך עשורים. האיכות של המותג 'גוגל' לא ניתנת לשכפול. הסכנה היחידה היא אפקט הרגולציה — האנטי-טרסט האמריקאי מתקדם, אבל גם אם יחלקו את החברה, השברים יהיו שווים יותר מהשלם.",
            "BN":    "ניק דקאס (המנכ״ל) הוא אחד המנהלים הטובים בתחום הנכסים האלטרנטיביים. ברוקפילד קורפ היא דוגמא קלאסית של 'עסק איכותי במחיר הוגן' — חפיר בהפצה, אנשים חכמים, תמחור סביר.",
            "XLV":   "אני אוהב סקטורים שאני יכול להבין ולהחזיק בשקט לעשור. סקטור הבריאות עומד בכל הקריטריונים: ביקוש לא-מחזורי, מרווחים יציבים, אינרציה דמוגרפית. 'ההחזקה השקטה' הקלאסית.",
            "SPY":   "פשוט, זול, אפקטיבי — נכס ליבה לכל משקיע רציונלי. אני לא יכול לנצח את השוק בהחזקה לטווח ארוך, אז למה לנסות? מדד S&P 500 הוא הדרך הכי חכמה להיות 'טיפש' בחוכמה.",
            "VOO":   "אותו הדבר כמו SPY אבל זול יותר — לא צריך חוכמה מיוחדת לבחור. Vanguard חוסכת לך כסף כל שנה, זה פשוט מצטבר.",
            "NVDA":  "חברה נהדרת, אבל המחיר מבקש ממך להיות צודק לחלוטין לשנים קדימה. שולי בטיחות (Margin of Safety) הם העיקרון הכי חשוב — וכאן אין. זה המקום שאני חוזר ל-Poor Charlie's Almanack שלי ונזכר: 'אל תעשה שטויות'.",
            "CPNG":  "עסק בינוני במחיר שלא מצדיק את הזמן שלי. יש מאות עסקים איכותיים יותר בעולם — למה להתעסק עם אחד בינוני?",
            "IBIT":  "ספקולציה נטו — אני לא משקיע במה שאני לא יכול לשים בו ערך. ורן ואני הסכמנו על הנקודה הזו עוד בעידן הזהב; האמיתות הישנות עדיין נכונות.",
            "ETHA":  "כנ״ל. חדשנות טכנולוגית לא מעניקה שימוש רציונלי להשקעה.",
            "ITA":   "ביטחון הוא סקטור שאני מעריך — קשיי כניסה גבוהים, חוזים ארוכים, מנהלים טובים. לא מרגש, אבל רווחי.",
            "AMZN":  "AWS הוא עסק נפלא. הקמעונאות לא. ההשקעה פה היא על AWS — ואני חושב שזה משתלם, למרות הסחת הדעת.",
            "URNM":  "מחזוריות גבוהה היא לא הסגנון שלי, אבל אני רואה את התזה: מחסור במכרות חדשים + ביקוש AI-datacenter. אם אמית רוצה חשיפה, משקל 5% מקסימום.",
            "NLR":   "יותר יציב מ-URNM. שירותי חשמל גרעיניים עם חפיר רגולטורי עמוק — מתאים ליותר הרבה.",
            "QQQM":  "למה להחזיק נאסד״ק 100 כשיש לך כבר את רוב המניות המרכיבות אותו? כפילות בתיק היא בזבוז קוגניטיבי.",
            "KSM-F34.TA": "אג״ח ממשלתי ישראלי — שקט, יציב, תשואה צפויה. אני לא מבין לעומק בשוק הישראלי, אבל אג״ח ממשלתי הוא אג״ח ממשלתי בכל מדינה.",
            "KSM-F77.TA": "כנ״ל — אג״ח ממשלתי ישראלי לטווח ארוך. שקט ויציב יותר מ-KSM-F34.",
        },
        "cathie_wood": {
            "GOOGL": "גוגל היא מוביל ה-AI הגנרטיבי עם Gemini, Waymo (רובוטקסי), ו-TPU (צ'יפים ייחודיים). היא לא 'מותקפת' ע״י ChatGPT — היא בונה את המרכבה הבאה. אנחנו רואים צמיחה של פי 5 בחישוב ה-AI שלהם ב-3 השנים הקרובות — תזת חדשנות חזקה.",
            "NVDA":  "מונופול וירטואלי על GPU לעידן ה-AI — CUDA הוא חפיר תוכנה עצום. עקומת ההכנסות עדיין מצויה רק בתחילתה: רק 15% מהדאטה-סנטרים עברו ל-AI-accelerated. ריצה ארוכה קדימה.",
            "AMZN":  "AWS מוביל בחישוב ענן, הבת Anthropic שייכת להם חלקית, ורובוטיקה עתידית (Zoox + Kiva). עסק 'שלוש-בחברה-אחת' עם חדשנות בכל שכבה.",
            "SPY":   "חשיפה פסיבית לא מנצחת חדשנות ממוקדת לטווח ארוך. אני בנויה לבחור מנצחים ספציפיים, לא להחזיק את הממוצע. ל-Amit יש SPY — זה בסדר כעוגן, אבל לא למקסם תשואה.",
            "VOO":   "אותה הערה כמו SPY — עוגן סביר, לא מקסימיזציה של תשואה.",
            "ITA":   "ביטחון הוא לא במוקד תזת החדשנות שלי. יש התקדמות במזלים אוטונומיים, אבל יותר מדי מהסקטור הוא חברות ישנות.",
            "CPNG":  "חדשנות במודל לוגיסטי, אבל רחוקה ממוקד התזה של ARK — לא מדד AI/Genomics/Fintech.",
            "IBIT":  "חדשנות פיננסית אמיתית — ביטקוין הוא נכס המפלט של העשור הבא. אבל התקרת קריפטו של %d%% אצל אמית חוסמת התרחבות — כבוד לחוקים שלו." % crypto_cap,
            "ETHA":  "את׳ריום חשוב יותר מביטקוין אפילו — זה הבסיס ל-DeFi ול-NFT. אבל שוב, התקרה של %d%% מגבילה." % crypto_cap,
            "URNM":  "אורניום הוא חלק מתזת האנרגיה הנקייה — דאטה-סנטרים של AI צורכים עשרות גיגה-וואטים, ורק גרעין יכול לענות. תזה מעולה ל-5 שנים.",
            "NLR":   "אותו סיפור כמו URNM — חדשנות אנרגיה גרעינית (SMR - Small Modular Reactors) בדרך.",
            "BN":    "תשתיות ואנרגיה מתחדשת תומכות בתזת החדשנות — BN חשופה לזה דרך Brookfield Renewable Partners.",
            "XLV":   "ביו-טק הוא חלק מהחדשנות, אבל XLV הוא מדד רחב — הגנתי מדי לסגנון שלי.",
            "QQQM":  "נאסד״ק 100 כולל את כל המנצחים שלי, אבל גם הרבה בלבול. אני מעדיפה בחירה ספציפית.",
            "KSM-F34.TA": "אג״ח ממשלתי ישראלי לא במוקד תזת החדשנות שלי.",
            "KSM-F77.TA": "כנ״ל.",
        },
        "peter_lynch": {
            "GOOGL": "חברה עם צמיחה ברורה רבעון-אחר-רבעון ב-Cloud (30%+) ו-AI. היא עדיין בתמחור סביר של 22x P/E — GARP (Growth At Reasonable Price) קלאסי. 'Invest in what you know' — כולם משתמשים בגוגל.",
            "AMZN":  "'Two stocks in one' — AWS הוא עסק מהיר-צמיחה עם מרווחים גבוהים, וה-retail הוא גדל איטי אבל יציב. המודל הזה הוא בדיוק מה שאני מחפש.",
            "BN":    "צמיחת NAV של 15%+ שנתית, דיבידנד צומח — סיפור 'מצטבר' קלאסי. לא מרגש, אבל עם הריבית המצטברת מעל 10 שנים זה ירוויח יותר מרוב המניות.",
            "NVDA":  "קצב צמיחת ההכנסות יוצא דופן — אבל המחיר כבר משקף זאת לחלוטין. P/E של 50 מחייב המשך צמיחה מושלמת. זהירות — זה לא 'החבא בעיניים' שאני מחפש.",
            "CPNG":  "הצמיחה מאטה ב-CPNG, והתחרות מואצת. אני מחפש 'סיפורים צומחים', לא 'סיפורים מאטים' — לוותר.",
            "SPY":   "מדד רחב — אני מעדיף סיפורים ספציפיים שאני מבין. ל-SPY אין 'סיפור' לספר.",
            "VOO":   "אותה הערה כמו SPY.",
            "URNM":  "סיפור צמיחה ברור: מחסור באורניום + ביקוש ענק מ-AI-datacenters + חזרה לגרעין בסקנדינביה. צמיחה שצפויה להיות לא-ליניארית.",
            "NLR":   "תזה דומה ל-URNM אבל עם פרופיל סיכון יותר יציב. אני אוהב שתיהן.",
            "ITA":   "ביטחון בצמיחה עקבית — תקציבים ממשלתיים עולים, חוזים ארוכי-טווח. סיפור 'משעמם ורווחי' — בדיוק מה שאני מחפש.",
            "XLV":   "בריאות היא סיפור צמיחה דמוגרפי — האוכלוסייה מזדקנת, ההוצאות עולות. יציב ויפה.",
            "AMZN_2": "",
            "IBIT":  "אני מעדיף עסקים שמייצרים הכנסות ורווחים. ביטקוין לא.",
            "ETHA":  "אותה הערה כמו IBIT.",
            "BN_2": "",
            "QQQM":  "מדד רחב — אני מעדיף להחזיק את החברות הספציפיות.",
            "KSM-F34.TA": "אני לא מכיר לעומק את שוק האג״ח הישראלי. אם אמית מאמין, שיחזיק — אג״ח ממשלתי בכל מדינה הוא בדרך כלל אחזקה הגנתית סבירה.",
            "KSM-F77.TA": "כנ״ל — אחזקה שקלית הגנתית.",
        },
        "technical_analyst": {
            "GOOGL": "שבירת התנגדות ארוכת-טווח מעל $175. ממוצע נע 50 יום עולה חד; RSI סביב 60 — לא במצב קניית-יתר, אבל מומנטום חיובי. פריצה נוספת מעל $185 תאותת כניסה חזקה.",
            "NVDA":  "לאחר ריצה של 250%+ השנה, נמצאת בהתכנסות ליד $140-150. RSI נרגע מ-75 ל-55 — קירור בריא. אין עדיין איתות כניסה; אני מחכה לשבירת תבנית.",
            "AMZN":  "נעה בתוך טווח צדדי רחב של $175-200 מזה 6 חודשים. חוצה את MA50 למעלה ומטה ללא מגמה ברורה. טכנית ניטרלית — אין איתות.",
            "SPY":   "מגמה עולה יציבה — מחיר מעל MA50 מעל MA200. יחס טכני בריא, ללא סטיות שליליות. המשך מגמה עולה — להמשיך לצבור בתרומות חודשיות.",
            "VOO":   "זהה ל-SPY — מגמה עולה עקבית. אותה מסקנה טכנית.",
            "BN":    "מגמה עולה לאורך שנה שלמה. ירידות קטנות נתמכות בעקביות ע״י MA50. התבנית היא 'מדרגות עולות' קלאסית.",
            "XLV":   "לאחר תקופה ארוכה של תת-ביצועים, מתחיל להופיע בסיס טכני. MA50 מתחיל להתיישר, RSI מטפס בעקביות — אפשרות להתהפכות מגמה.",
            "URNM":  "שובר שיאים של 52 שבועות ברצף. התבנית מציגה higher-highs ו-higher-lows עקביים — מגמה עולה חזקה. איתות קנייה טכני מובהק.",
            "NLR":   "פריצה לאחרונה מעל רמת ההתנגדות של $90. תבנית גביע-וידית הושלמה — מטרה טכנית סביב $110.",
            "ITA":   "מומנטום חיובי עקבי — מעלה את שיאי 52 השבועות. תנועה שקטה ואיטית, אבל מגמה עולה מובהקת.",
            "CPNG":  "מתחת ל-MA200 כבר 6 חודשים. מגמה יורדת ברורה. RSI ליד 30 — מבצע 'oversold bounce' אפשרי, אבל המגמה הראשית שלילית. איתות טכני לסל״ב.",
            "IBIT":  "תלוי במחלוטין בהתנהגות ביטקוין. תנודתיות יומית של 4-6% — לא איתות טכני ברור. מגמה ארוכת-טווח עדיין עולה אך עם תיקונים חדים.",
            "ETHA":  "מתחת ל-MA50 מזה 2 חודשים. ביצועי חסר מול ביטקוין. טכנית חלש.",
            "QQQM":  "מגמה עולה יציבה, דומה לנאסד״ק 100 — המשך מגמה. לא מוסיף ערך מעל SPY/VOO טכנית.",
            "KSM-F34.TA": "מגמה עולה ארוכת-טווח, תנודתיות נמוכה מאוד. ללא איתות שינוי. המשך החזקה.",
            "KSM-F77.TA": "מגמה עולה עקבית. תנודתיות נמוכה מאוד — מאפייני אג״ח. המשך החזקה.",
            "AMZN_2": "",
        },
        "fundamentals_analyst": {
            "GOOGL": "מרווח גולמי 56%, מרווח תפעולי 30%, צמיחת EPS דו-ספרתית (13%+), יחס חוב/הון נמוך במיוחד. הפונדמנטלים מהטובים ב-S&P 500 — דיבידנד התחיל, מה שמעיד על בשלות.",
            "NVDA":  "מרווחים יוצאי דופן (75%+ גולמי), אבל P/E של 50+. רווחי העתיד חייבים להצדיק את המחיר — ציפיות של 30%+ צמיחת EPS ב-5 שנים. סיכון רמת הציפיות.",
            "AMZN":  "מרווח תפעולי של AWS לבד הוא 35%+ — זה המנוע. הקמעונאות ברווחיות חיובית אך נמוכה. מבנה חוב בריא, יחס חוב/EBITDA סביב 2x.",
            "BN":    "NAV של ברוקפילד קורפ צומח ב-15%+ שנתית. הכנסות ממדיניות חלוקה יציבות וצפויות. יחס תשואה להון (ROE) גבוה מהממוצע בענף.",
            "SPY":   "חשיפה לפונדמנטלים של 500 החברות המובילות — ROE ממוצע של 18%, P/E של 22, שיעור צמיחה של 8%. רמת איכות 'ממוצעת גבוהה' — זה המצב הטבעי של כלכלה אמריקאית.",
            "VOO":   "זהה ל-SPY בדיוק בפונדמנטלים. ההבדל: דמי ניהול נמוכים יותר (0.03% vs 0.09%) משפרים תשואה נטו — עדיפה פונדמנטלית.",
            "XLV":   "רווחיות יציבה לאורך מחזורים, דיבידנדים עקביים, יחס חוב סביר. שיעור צמיחת EPS 8-10% עקבי — לא מרגש, אבל אמין.",
            "ITA":   "חברות הביטחון האמריקאיות מציגות הכנסות צפויות ב-85% (גיבוי חוזים). רווחיות יציבה, דיבידנדים הולכים וגדלים. יחס חוב סביר.",
            "URNM":  "תלוי מאוד במחירי הספוט של אורניום. היום מחירים עולים — מה שתרגם להכפלת הכנסות של חברות הכרייה. סיכון: אם המחירים יירדו, הרווחיות תצנח.",
            "NLR":   "יציבות הכנסות גבוהה יותר מ-URNM — כולל שירותי חשמל גרעיניים (רגולציה מורכבת יוצרת חפיר). דיבידנדים מסוימים; פונדמנטלים יציבים.",
            "CPNG":  "מרווחים דקים (2-3% תפעולי), תחרות עזה שלוחצת על המחירים. איכות פונדמנטלית בינונית. חוב נוח, אבל בלי מנוע צמיחה ברור.",
            "IBIT":  "ללא פונדמנטלים קלאסיים — אין EPS, אין מרווח, אין דיבידנד. עוקב אחר מחיר ביטקוין, שאין לו ערך פנימי ניתן להערכה בשיטות DCF.",
            "ETHA":  "זהה ל-IBIT — ללא ערך פנימי ניתן להערכה. ספקולציה על שיעור אימוץ טכנולוגי.",
            "QQQM":  "חשיפה לחברות איכות גבוהה (AAPL, MSFT, NVDA, GOOGL, META) — אבל בתמחור מתוח. P/E ממוצע של 28 vs 22 של SPY. כפילות עם החזקות קיימות.",
            "KSM-F34.TA": "אג״ח ממשלתי ישראלי F34 — תשואה לפדיון יציבה, duration בינוני. ללא חוב תאגידי — סיכון אשראי אפסי. פונדמנטלים יציבים.",
            "KSM-F77.TA": "אג״ח ממשלתי ישראלי F77 — תשואה לפדיון יציבה, duration ארוך יותר. רגישות גבוהה יותר לשינויי ריבית, אך סיכון אשראי אפסי.",
            "AMZN_2": "",
        },
        "risk_manager": {
            "GOOGL": "משקל סביר בתיק (8%), תנודתיות מתונה (25% שנתית), קורלציה עם השוק 0.9. אין דגל אדום — ניתן להגדיל עד 10% משקל.",
            "NVDA":  "תורמת משמעותית לתנודתיות התיק (40% שנתית!). משקל נוכחי 5% כבר מציע חשיפה חזקה. לא להגדיל מעל 7-8% משקל — הסיכון למחיקה חד-פעמית גבוה.",
            "AMZN":  "קורלציה גבוהה עם QQQ/SPY (0.85+) — היזהר מהכפלת חשיפה טכנולוגית. התנודתיות בסדר (28%).",
            "BN":    "גיוון נאה מחוץ לטכנולוגיה — קורלציה נמוכה יחסית עם השוק (0.65). תורמת ליציבות התיק הכללית.",
            "SPY":   "עוגן התיק — המשך לבנות משקל בתרומות החודשיות. קטגוריית 'סיכון נמוך' של הפרופיל.",
            "VOO":   "עוגן חלופי ל-SPY — באותו מעמד בסיכון. יש הגיון להחליף הדרגתית את SPY ב-VOO בגלל דמי ניהול.",
            "XLV":   "נכס הגנתי — מפחית drawdown בתקופות משבר (בטא 0.7). ממליץ להגדיל בתקופות חוסר ודאות.",
            "ITA":   "חשיפה לסקטור לא-מקורלל עם השוק הרחב. תורמת חיובית לפרופיל סיכון/תשואה.",
            "URNM":  "תנודתיות גבוהה מאוד (45%+). לא להגדיל מעל 5% משקל — סיכון למחיקות של 30-40%. לאמית: נראה שאתה בגבול התקרה.",
            "NLR":   "יותר יציב מ-URNM (תנודתיות 30%), אבל עדיין סקטור צר. מקסימום 5% משקל.",
            "CPNG":  "פוזיציה במינוס ללא תרומה ברורה לתזת התיק. תורמת לסיכון מבלי לתרום לתשואה הצפויה. המלצה: לסגור ולהעביר ל-VOO/GOOGL.",
            "IBIT":  f"חריגה מתקרת קריפטו של {crypto_cap}%. הפרופיל הנוכחי אוסר 'Crypto' בסקטורים המנועים — יש להקטין ב-50%+ לפחות.",
            "ETHA":  f"אותה הערה כמו IBIT — חריגה מהתקרה. להקטין מיד.",
            "QQQM":  "כפילות עם SPY/VOO (המניות המרכזיות חוזרות) + החזקות פרטניות של NVDA/GOOGL/AMZN. הסיכון: חשיפה מרוכזת יתר.",
            "KSM-F34.TA": "נכס שקלי הגנתי — אג״ח ממשלתי ישראלי מפחית סיכון מט״ח בתיק דולרי. תורם חיובית לגיוון.",
            "KSM-F77.TA": "כנ״ל — נכס שקלי הגנתי ל-duration ארוך. מפחית קורלציה עם שוק המניות.",
        },
    }

    by_persona = base.get(persona, {})
    text = by_persona.get(ticker)
    if text:
        return text

    # Fallbacks per verdict
    if verdict == "buy":
        return "התאמה טובה לפרופיל המשתמש — לשקול הגדלת משקל."
    if verdict == "sell":
        return "ללא התאמה לתזות הנוכחיות — לשקול הקטנה."
    return "סימנים מעורבים — החזקה, ולהעריך מחדש בהרצה הבאה."


def _dry_run(settings: dict, tickers: list[str], note: str = "") -> dict:
    """Produce a realistic-looking recommendations.json without calling any LLM."""
    crypto_cap = settings.get("crypto_cap_pct", 10)
    avoid = set(settings.get("avoid_sectors", []))
    horizon = settings.get("horizon_years", 3)
    style = settings.get("style", "conservative")

    # Per-ticker (verdict, conviction) tuned to current settings
    profile_hints = {
        "GOOGL": ("buy", 84),
        "NVDA":  ("hold", 62),
        "SPY":   ("buy", 80),
        "VOO":   ("buy", 82),
        "AMZN":  ("hold", 58),
        "BN":         ("buy", 74),
        "CPNG":       ("sell", 78),
        "IBIT":       ("sell", 80) if "Crypto" in avoid or crypto_cap <= 5 else ("hold", 55),
        "ETHA":       ("sell", 82) if "Crypto" in avoid or crypto_cap <= 5 else ("hold", 55),
        "XLV":        ("buy", 72),
        "QQQM":       ("hold", 55),
        "ITA":        ("buy", 76),
        "URNM":       ("buy", 73),
        "NLR":        ("buy", 72),
        "KSM-F34.TA": ("hold", 68),
    }

    personas_active = settings.get("personas_active") or [
        "warren_buffett", "cathie_wood", "technical_analyst", "fundamentals_analyst", "risk_manager"
    ]

    holdings_out = []
    for tk in tickers:
        v, c = profile_hints.get(tk, ("hold", 50))

        # Per-persona opinions (each may differ slightly from the aggregate verdict)
        persona_entries = []
        for p in personas_active:
            # Simple heuristic: each persona's verdict tilts slightly around the aggregate
            pv = v
            pc = c
            # Technical analyst disagrees with fundamentals on NVDA (buy vs. hold)
            if p == "technical_analyst" and tk in ("URNM", "NLR", "GOOGL", "ITA"):
                pv, pc = "buy", min(95, c + 5)
            if p == "fundamentals_analyst" and tk == "NVDA":
                pv, pc = "hold", max(50, c - 5)
            if p == "fundamentals_analyst" and tk in ("IBIT", "ETHA"):
                pv, pc = "sell", 70
            if p == "cathie_wood" and tk in ("GOOGL", "NVDA"):
                pv, pc = "buy", min(95, c + 8)
            if p == "warren_buffett" and tk in ("IBIT", "ETHA", "CPNG"):
                pv, pc = "sell", max(c, 75)
            if p == "risk_manager" and tk in ("IBIT", "ETHA") and ("Crypto" in avoid or crypto_cap <= 5):
                pv, pc = "sell", 85

            persona_entries.append({
                "name": p,
                "display_name": PERSONA_NAMES_HE.get(p, p),
                "verdict": pv,
                "conviction": pc,
                "rationale": _persona_rationale(p, tk, pv, settings),
            })

        # Generate plausible mock scores based on verdict
        if v == "buy":
            _mock_scores = {"valuation": 65, "technical": 70, "risk": 60,
                            "sentiment": 68, "macro": 55, "quality": 72}
        elif v == "sell":
            _mock_scores = {"valuation": 30, "technical": 35, "risk": 25,
                            "sentiment": 32, "macro": 45, "quality": 28}
        else:
            _mock_scores = {"valuation": 50, "technical": 52, "risk": 55,
                            "sentiment": 48, "macro": 50, "quality": 50}
        # Add some variance per ticker
        import hashlib
        _h = int(hashlib.md5(tk.encode()).hexdigest()[:4], 16) % 15
        _mock_scores = {k: max(0, min(100, sc + _h - 7))
                        for k, sc in _mock_scores.items()}

        # Generate mock score details for dry-run
        def _mock_reasons(s: int, category: str) -> list[str]:
            if category == "quality":
                if s > 60: return ["High ROE (>15%) indicates strong profitability", "Stable profit margins over past years", "Low debt burden"]
                elif s < 40: return ["Below-average ROE", "Thin profit margins", "Elevated debt load"]
                return ["Average business quality metrics"]
            elif category == "valuation":
                if s > 60: return ["P/E below sector average", "Favorable PEG ratio (<1.5)", "Analyst targets suggest upside"]
                elif s < 40: return ["P/E above sector average", "PEG ratio indicates overvaluation", "Price near analyst target"]
                return ["Fairly valued vs sector"]
            elif category == "risk":
                if s > 60: return ["Well-sized position in portfolio", "Beta near market (1.0)", "Low sector concentration"]
                elif s < 40: return ["Overweight position (>15%)", "High beta (>1.5) — volatile", "Crypto cap at risk"]
                return ["Moderate portfolio risk contribution"]
            elif category == "macro":
                if s > 60: return ["VIX low, calm markets", "Yield curve normal", "Rate environment favorable"]
                elif s < 40: return ["Elevated VIX", "Rate pressure on sector", "Yield curve inverted"]
                return ["Neutral macro environment"]
            elif category == "sentiment":
                if s > 60: return ["Strong analyst BUY consensus (>70%)", "Positive recent coverage"]
                elif s < 40: return ["Significant SELL pressure", "Negative news flow"]
                return ["Mixed analyst views"]
            elif category == "technical":
                if s > 60: return ["Price above MA50 and MA200", "Uptrend confirmed", "RSI in healthy zone"]
                elif s < 40: return ["Price below MA200", "Downtrend signals", "RSI overbought/oversold"]
                return ["Sideways price action"]
            return ["No data"]

        _score_details = {k: _mock_reasons(v, k) for k, v in _mock_scores.items()}

        # Derive verdict/conviction from the SCORES (not from hardcoded hints)
        # This matches the real pipeline behavior
        try:
            from scoring_engine import scores_to_verdict as _s2v
            _w = settings.get("scoring_weights", {
                "quality": 30, "valuation": 25, "risk": 20,
                "macro": 15, "sentiment": 5, "technical": 5,
            })
            derived_v, derived_c = _s2v(_mock_scores, _w)
        except Exception:
            derived_v, derived_c = v, c

        # Mock analyst consensus based on verdict
        if derived_v == "buy":
            _mock_analyst = {"buy": 25, "hold": 8, "sell": 2}
        elif derived_v == "sell":
            _mock_analyst = {"buy": 3, "hold": 10, "sell": 18}
        else:
            _mock_analyst = {"buy": 12, "hold": 15, "sell": 5}
        # Variance per ticker
        _mock_analyst["target"] = None
        _mock_analyst["price"] = None

        # Mock position sizing + exit triggers
        _mock_position = {}
        _mock_exit = {}
        try:
            from position_sizing import compute_position_size, compute_exit_triggers
            _w = settings.get("scoring_weights", {
                "quality": 30, "valuation": 25, "risk": 20,
                "macro": 15, "sentiment": 5, "technical": 5,
            })
            _wavg = sum(_mock_scores[k] * _w.get(k, 0) for k in _mock_scores) / max(
                1, sum(_w.values()))
            _strat = settings.get("scoring_strategy", "conservative_longterm")
            import hashlib
            _mock_cur_weight = (int(hashlib.md5(tk.encode()).hexdigest()[:4], 16) % 15) + 2
            _mock_position = compute_position_size(
                _mock_scores, _wavg, _mock_scores.get("risk", 50),
                current_weight=_mock_cur_weight, sector_weight=15, is_crypto=False,
                crypto_cap=settings.get("crypto_cap_pct", 10),
                strategy=_strat, is_new_position=False,
            )
            _mock_exit = compute_exit_triggers(
                derived_v, _wavg, 150.0,
                140.0, None, strategy=_strat,
            )
        except Exception:
            pass

        holdings_out.append({
            "ticker": tk,
            "verdict": derived_v,
            "conviction": derived_c,
            "scores": _mock_scores,
            "score_details": _score_details,
            "analyst_consensus": _mock_analyst,
            "position_sizing": _mock_position,
            "exit_triggers": _mock_exit,
            "personas": persona_entries,
        })

    def _idea_details(scores: dict) -> dict:
        return {k: _mock_reasons(v, k) for k, v in scores.items()}

    # Dry-run new ideas — high-scoring candidates that pass the ≥60 filter
    new_ideas = [
        {"ticker": "MSFT", "name": "Microsoft", "conviction": 74,
         "rationale": "מוביל בגל ה-AI (Azure + OpenAI + Copilot). מרווחים 36%, ROE 38%, דיבידנד צומח. קונצנזוס אנליסטים חזק (BUY 80%+).",
         "scores": {"quality": 88, "valuation": 65, "risk": 75, "macro": 55, "sentiment": 82, "technical": 72}},
        {"ticker": "LLY", "name": "Eli Lilly", "conviction": 72,
         "rationale": "מובילה ב-GLP-1 (Mounjaro, Zepbound). צמיחת הכנסות 40%+, ROE 50%+, pipeline עשיר. הגנתי מפני מחזוריות.",
         "scores": {"quality": 85, "valuation": 55, "risk": 72, "macro": 60, "sentiment": 78, "technical": 70}},
        {"ticker": "V", "name": "Visa", "conviction": 68,
         "rationale": "תשתית תשלומים דומיננטית. מרווחים 65%+, ROE 40%+. עלייה בתשלומים דיגיטליים — moat עצום.",
         "scores": {"quality": 92, "valuation": 58, "risk": 80, "macro": 55, "sentiment": 75, "technical": 65}},
    ]
    for idea in new_ideas:
        idea["score_details"] = _idea_details(idea["scores"])
        # Derive conviction from scores using user's weights
        try:
            from scoring_engine import scores_to_verdict as _s2v
            _w = settings.get("scoring_weights", {
                "quality": 30, "valuation": 25, "risk": 20,
                "macro": 15, "sentiment": 5, "technical": 5,
            })
            _, idea_c = _s2v(idea["scores"], _w)
            idea["conviction"] = idea_c
        except Exception:
            pass

    # Dynamic Hebrew summary — inspects the actual verdicts we just produced
    strong_buys = [h["ticker"] for h in holdings_out
                   if h["verdict"] == "buy" and h["conviction"] >= 75]
    strong_sells = [h["ticker"] for h in holdings_out
                    if h["verdict"] == "sell" and h["conviction"] >= 75]
    crypto_tickers = [h["ticker"] for h in holdings_out
                      if h["ticker"] in ("IBIT", "ETHA")]
    nvda_hold = any(h["ticker"] == "NVDA" and h["verdict"] == "hold" for h in holdings_out)
    new_ideas_list = [i["ticker"] for i in new_ideas[:2]]

    summary_parts = [
        f"סיכום יומי לפרופיל '{settings.get('profile_name', 'ברירת מחדל')}' "
        f"(אופק {horizon} שנים, סגנון {style})."
    ]
    if strong_buys:
        summary_parts.append(f"נקודות חזקות לקנייה: {', '.join(strong_buys)} — מעל 75% קונבישן.")
    if strong_sells:
        summary_parts.append(f"נקודות חזקות למכירה: {', '.join(strong_sells)}.")
    if crypto_tickers and ("Crypto" in avoid or crypto_cap <= 5):
        summary_parts.append(
            f"החשיפה הקריפטו ({', '.join(crypto_tickers)}) חורגת מתקרת {crypto_cap}% "
            f"ומגדרת סקטור נמנע — מומלץ להקטין."
        )
    if nvda_hold:
        summary_parts.append("NVDA בהחזקה — איכות גבוהה אך מחיר מתוח; לחכות לנקודת כניסה טובה יותר.")
    if new_ideas_list:
        summary_parts.append(f"רעיונות חדשים שצוותו לפרופיל: {', '.join(new_ideas_list)}.")

    summary = " ".join(summary_parts)
    if note:
        summary = f"[{note}] " + summary

    mock_insights = {
        "headline": "תיק מאוזן עם חשיפה חזקה ל-AI וסקטורים דפנסיביים",
        "insights": (
            "**Portfolio Health** — התיק שלך מציג פיזור טוב עם דגש על איכות עסקית. "
            "GOOGL ו-NVDA מהווים את העוגן הטכנולוגי, ו-SPY/VOO כעוגן מדד רחב.\n\n"
            "**Hidden Risks** — יש חשיפה כפולה דרך QQQM ו-SPY (שניהם מחזיקים GOOGL, NVDA, AMZN). "
            "שקול לצמצם את QQQM.\n\n"
            "**Market Context** — VIX סביב 18, ריבית Fed 3.64%, עקום תשואות נורמלי. "
            "סביבה תומכת במניות איכות אך זהירות מפני תמחור מתוח.\n\n"
            "**Opportunities** — CPNG עם ציון Technical נמוך אך Valuation גבוה — "
            "חוסר התאמה שמצריך מחקר.\n\n"
            "**Action Items** — 1. לצמצם IBIT/ETHA אם הם חוצים את תקרת הקריפטו. "
            "2. לעקוב אחר RSI של CPNG לתיקון אפשרי.\n\n"
            "_סקירת שוק — אינה המלצה פיננסית._"
        ),
        "updated": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    return {
        "updated": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "profile_name": settings.get("profile_name", ""),
        "summary": summary,
        "smart_insights": mock_insights,
        "holdings": holdings_out,
        "new_ideas": new_ideas,
        "dry_run": True,
    }


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run ai-hedge-fund once and exit (real run)")
    ap.add_argument("--dry-run", action="store_true", help="Produce mock recommendations without LLM calls")
    args = ap.parse_args()

    settings = _load_json(SETTINGS_PATH)
    portfolio = _load_json(PORTFOLIO_PATH)
    if not portfolio:
        print(f"[error] {PORTFOLIO_PATH} not found or empty", file=sys.stderr)
        sys.exit(2)

    if args.dry_run or not args.once:
        recs = _dry_run(settings, _tickers(portfolio))
    else:
        recs = run_real(settings, portfolio)

    # Save previous recommendations for change tracking (Phase 2)
    _prev_path = _ROOT / "recommendations_prev.json"
    if RECS_PATH.exists():
        try:
            import shutil
            shutil.copy2(RECS_PATH, _prev_path)
        except Exception:
            pass

    # Save new ideas to history for scorecard tracking (Phase 2)
    _ideas_hist_path = _ROOT / "ideas_history.json"
    try:
        _hist = json.loads(_ideas_hist_path.read_text()) if _ideas_hist_path.exists() else []
    except Exception:
        _hist = []
    _existing_ideas = {e.get("ticker") for e in _hist}
    for idea in recs.get("new_ideas", []):
        tk = idea.get("ticker", "")
        if tk and tk not in _existing_ideas:
            _hist.append({
                "ticker": tk,
                "name": idea.get("name", ""),
                "suggested_date": datetime.now().strftime("%Y-%m-%d"),
                "suggested_price": idea.get("suggested_price", 0),
                "conviction": idea.get("conviction", 0),
            })
    _ideas_hist_path.write_text(json.dumps(_hist, indent=2, ensure_ascii=False))

    # Record verdicts for accuracy tracking
    try:
        from accuracy_tracker import record_verdicts
        record_verdicts(recs)
    except Exception:
        pass

    # Record score history for trend analysis
    try:
        from score_history import record_scores
        record_scores(recs)
    except Exception:
        pass

    RECS_PATH.write_text(json.dumps(recs, indent=2, ensure_ascii=False))
    print(f"[ok] wrote {RECS_PATH} ({len(recs.get('holdings', []))} holdings, "
          f"{len(recs.get('new_ideas', []))} new ideas)")


if __name__ == "__main__":
    main()
