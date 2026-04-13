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


# ─── Real run — direct Gemini calls, one per (ticker × persona) ─────────────

PERSONA_SYSTEM_PROMPTS = {
    "warren_buffett": (
        "אתה וורן באפט, הכלל שלך: השקע רק בעסקים עם חפיר תחרותי עמוק, הנהלה איכותית, "
        "ותזרים מזומנים יציב. אתה שונא חברות ללא ערך פנימי ניתן להערכה (DCF). "
        "מעריך יציבות על פני זוהר."
    ),
    "charlie_munger": (
        "אתה צ'ארלי מנגר: חשוב בהיפוך, עסקים איכותיים במחיר הוגן עדיפים על עסקים בינוניים במחיר זול. "
        "שולי בטיחות (Margin of Safety) הוא העיקרון הכי חשוב. אתה שונא ספקולציה."
    ),
    "cathie_wood": (
        "את קתי ווד: מחפשת חברות חדשניות בתחומי AI, גנומיקה, פינטק, רובוטיקה. "
        "רואה מגמות 5 שנים קדימה. טרייד-אוף איכות-מחיר פחות קריטי לך מקצב חדשנות."
    ),
    "peter_lynch": (
        "אתה פיטר לינץ': 'Invest in what you know'. GARP (Growth At Reasonable Price). "
        "מחפש צמיחה רבעונית עקבית, סיפורים ברורים, PE סביר יחסית לקצב הצמיחה."
    ),
    "michael_burry": (
        "אתה מייקל בורי: מחפש פוזיציות קונטריאניות, בועות, מחירים מעוותים. "
        "אתה רואה מה שאחרים מפספסים; קצר על חברות מנופחות."
    ),
    "technical_analyst": (
        "אתה ניתוח טכני טהור. אין לך עניין בפונדמנטלים. אתה מסתכל על: "
        "מגמה (MA50/MA200), RSI, תמיכה והתנגדות, תבניות (גביע-וידית, ראש-וכתפיים), מומנטום."
    ),
    "fundamentals_analyst": (
        "אתה ניתוח פונדמנטלי: P/E, P/B, EPS growth, דיבידנדים, יחסי חוב, ROE, "
        "מרווחים גולמיים ותפעוליים. אין עניין בטכני או בסנטימנט."
    ),
    "ben_graham": (
        "אתה בן גראהם: ערך עמוק, שולי בטיחות מוחלטים, Net-Nets. "
        "PE מתחת ל-15, P/B מתחת ל-1.5, חברות יציבות."
    ),
    "risk_manager": (
        "אתה מנהל סיכונים: המטרה שלך להגן על התיק מחשיפה מרוכזת, תנודתיות יתר, "
        "וחריגה ממדיניות המשקיע (תקרות סקטור, תקרת קריפטו). לא מעניין אותך אלפא, "
        "אלא שימור הון."
    ),
    "valuation": "אתה מעריך שווי: DCF, EV/EBITDA, SOTP. רק מחירים מדברים אליך.",
    "sentiment": "אתה סנטימנט שוק: רגש, סוציאל, חדשות, סנטימנט אנליסטים.",
    "macro": "אתה מאקרו: ריבית, אינפלציה, צמיחה עולמית, גיאופוליטיקה.",
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
    # gemini-flash-latest: auto-aliased by Google to their newest stable Flash.
    # When Gemini 3 Flash goes GA, we get it automatically — no code change.
    # Override with GEMINI_MODEL env var (e.g. gemini-2.5-pro for higher quality,
    # gemini-3-flash-preview for preview models).
    model_name = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    return ChatGoogleGenerativeAI(
        model=model_name, google_api_key=api_key,
        temperature=0.3, timeout=45, max_retries=0,  # we do our own retries below
    )


def _invoke_with_retry(llm, messages, attempts: int = 5):
    """Invoke with explicit backoff on 503/429/RESOURCE_EXHAUSTED."""
    import time
    last_err = None
    for i in range(attempts):
        try:
            return llm.invoke(messages)
        except Exception as e:
            err = str(e)
            last_err = e
            # Retry on 5xx, rate limits, timeouts
            retriable = any(s in err for s in ("503", "502", "504", "429", "RESOURCE_EXHAUSTED",
                                               "UNAVAILABLE", "timeout", "Timeout", "DEADLINE"))
            if not retriable or i == attempts - 1:
                raise
            wait = min(30, (2 ** i) + 1)  # 2, 3, 5, 9, 17s
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


def _call_persona(llm, persona: str, ticker: str, display_name: str, preamble: str) -> dict:
    """Single Gemini call for one (persona × ticker). Returns persona entry dict."""
    system = PERSONA_SYSTEM_PROMPTS.get(persona, "אתה אנליסט השקעות מקצועי.")
    user = (
        f"{preamble}\n\n"
        f"עכשיו תן ניתוח לחברה: **{display_name} ({ticker})**.\n"
        f"התבסס על הפרופיל של המשתמש למעלה. {PER_TICKER_SCHEMA}"
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
    """Aggregate persona verdicts into a single (verdict, conviction) using weighted voting."""
    scores = {"buy": 0, "hold": 0, "sell": 0}
    total_weight = 0
    for p in persona_entries:
        v = p.get("verdict", "hold")
        c = int(p.get("conviction", 0))
        scores[v] = scores.get(v, 0) + c
        total_weight += c
    if total_weight == 0:
        return "hold", 0
    top = max(scores.items(), key=lambda kv: kv[1])
    avg = int(top[1] / max(1, len([p for p in persona_entries if p["verdict"] == top[0]])))
    return top[0], min(100, avg)


def run_real(settings: dict, portfolio: dict) -> dict:
    """Real run: calls Gemini directly for each (persona × ticker)."""
    tickers = _tickers(portfolio)
    if not tickers:
        print("[error] portfolio.json has no holdings", file=sys.stderr)
        sys.exit(2)

    personas = list(settings.get("personas_active") or [
        "warren_buffett", "cathie_wood", "technical_analyst",
        "fundamentals_analyst", "risk_manager",
    ])
    # ALWAYS include technical + fundamentals analysts so the Recommendations page
    # can render the joint consensus view, regardless of user's chosen persona set.
    for required in ("technical_analyst", "fundamentals_analyst"):
        if required not in personas:
            personas.append(required)
    preamble = _build_profile_preamble(settings)

    # Resolve display names from config for prompt clarity
    sys.path.insert(0, str(_ROOT))
    try:
        from config import DISPLAY_NAMES  # type: ignore
    except Exception:
        DISPLAY_NAMES = {}

    llm = _gemini()
    n_calls = len(tickers) * len(personas)
    # Parallelism: run multiple persona calls concurrently. Gemini Flash
    # tolerates ~8 concurrent requests per key before 429s; we stay conservative.
    max_workers = int(os.environ.get("GEMINI_CONCURRENCY", "6"))
    print(f"[info] calling Gemini {n_calls} times "
          f"({len(tickers)} holdings × {len(personas)} personas) "
          f"— up to {max_workers} in parallel")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    holdings_out = []
    for i, tk in enumerate(tickers, 1):
        display = DISPLAY_NAMES.get(tk, tk)
        persona_entries = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_call_persona, llm, p, tk, display, preamble): p
                for p in personas
            }
            for fut in as_completed(futures):
                persona_entries.append(fut.result())
        # Preserve the user's configured persona order in the output
        _order = {p: idx for idx, p in enumerate(personas)}
        persona_entries.sort(key=lambda e: _order.get(e.get("name", ""), 99))
        agg_v, agg_c = _aggregate_verdict(persona_entries)
        holdings_out.append({
            "ticker": tk, "verdict": agg_v, "conviction": agg_c,
            "personas": persona_entries,
        })
        print(f"  [{i}/{len(tickers)}] {tk}: {agg_v.upper()} {agg_c}%", flush=True)

    # Ask Gemini for 2-3 new ideas
    new_ideas = _generate_new_ideas(llm, preamble, tickers)

    # Generate a 2-4 sentence Hebrew daily summary from the aggregate
    summary = _generate_summary(llm, preamble, holdings_out, new_ideas)

    return {
        "updated": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "profile_name": settings.get("profile_name", ""),
        "summary": summary,
        "holdings": holdings_out,
        "new_ideas": new_ideas,
        "dry_run": False,
    }


def _generate_new_ideas(llm, preamble: str, existing_tickers: list[str]) -> list[dict]:
    """Ask Gemini for 2-3 new ticker ideas outside the existing portfolio."""
    existing_str = ", ".join(existing_tickers)
    user = (
        f"{preamble}\n\n"
        f"המשתמש כבר מחזיק: {existing_str}.\n"
        f"הצע 3 מניות חדשות שאינן בתיק, שמתאימות לפרופיל שלו.\n"
        "החזר JSON בלבד בפורמט:\n"
        '{"ideas": [{"ticker": "SYM", "name": "Company", "conviction": 0-100, "rationale": "2-3 משפטים בעברית"}, ...]}'
    )
    try:
        resp = _invoke_with_retry(llm, [("system", "אתה אנליסט השקעות."), ("user", user)])
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
    for i in ideas[:3]:
        if not i.get("ticker"):
            continue
        cleaned.append({
            "ticker": i["ticker"].upper(),
            "name": i.get("name", i["ticker"]),
            "conviction": int(i.get("conviction", 60)),
            "rationale": i.get("rationale", ""),
        })
    return cleaned


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


# ─── Dry-run mock (Hebrew rationales, 5 personas, settings-aware) ───────────

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
        "KSM-F77.TA": ("hold", 66),
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

        holdings_out.append({
            "ticker": tk,
            "verdict": v,
            "conviction": c,
            "personas": persona_entries,
        })

    new_ideas = [
        {"ticker": "MSFT", "name": "Microsoft", "conviction": 82,
         "rationale": "מוביל נוסף בגל ה-AI (Azure + OpenAI + Copilot) — משלים את גוגל בצד ה-enterprise. מרווחים גבוהים, דיבידנד צומח, פונדמנטלים מעולים."},
        {"ticker": "TSM", "name": "Taiwan Semiconductor", "conviction": 78,
         "rationale": "משחק 'מכוש ואת' על AI — חברת הייצור המובילה בעולם. סיכון גיאופוליטי, אך חפיר תחרותי גדול מאוד."},
        {"ticker": "META", "name": "Meta Platforms", "conviction": 70,
         "rationale": "השקעות ענק ב-AI, מודלי Llama הופכים סטנדרט קוד-פתוח; פונדמנטלים חזקים והפחתת הוצאות משמעותית."},
    ]

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

    return {
        "updated": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "profile_name": settings.get("profile_name", ""),
        "summary": summary,
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

    RECS_PATH.write_text(json.dumps(recs, indent=2, ensure_ascii=False))
    print(f"[ok] wrote {RECS_PATH} ({len(recs.get('holdings', []))} holdings, "
          f"{len(recs.get('new_ideas', []))} new ideas)")


if __name__ == "__main__":
    main()
