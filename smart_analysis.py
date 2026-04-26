"""
Smart Analysis Layer — ONE Gemini call per day that:
1. Reviews all scores + market data
2. Identifies patterns the algorithm misses (correlations, news implications, trend shifts)
3. Surfaces risks the user should know about
4. Generates a portfolio-level insight summary

Uses gemini-pro-latest (auto-resolves to 2026 flagship) — ONE call per run,
monthly cadence.
Output is saved to smart_insights.json and shown in UI + Telegram.

Calls the native google-genai SDK with Google Search grounding so the brief
can incorporate real-time news/earnings context, then falls back to the
plain langchain path when the SDK or grounded call is unavailable.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_INSIGHTS_PATH = _ROOT / "smart_insights.json"


def _build_analysis_prompt(recommendations: dict, macro: dict, settings: dict) -> str:
    """Build a comprehensive analysis prompt with all available data."""
    holdings = recommendations.get("holdings", [])
    new_ideas = recommendations.get("new_ideas", [])

    # Portfolio overview
    by_verdict = {"buy": [], "hold": [], "sell": []}
    for h in holdings:
        v = (h.get("verdict") or "hold").lower()
        by_verdict[v].append(h)

    lines = [
        "You are a senior portfolio analyst reviewing a client's portfolio.",
        "Your job: identify insights the scoring algorithm MISSES.",
        "",
        f"Client profile: {settings.get('profile_name', 'Conservative investor')}",
        f"Horizon: {settings.get('horizon_years', 4)} years | Risk: {settings.get('risk_level', 'medium')}",
        f"Strategy: {settings.get('scoring_strategy', 'conservative_longterm').replace('_', ' ')}",
        "",
        "MARKET CONTEXT:",
        f"  VIX: {macro.get('vix', 'N/A')}",
        f"  Fed Rate: {macro.get('fed_rate', 'N/A')}%",
        f"  10Y Yield: {macro.get('ten_year_yield', 'N/A')}%",
        f"  S&P 500 today: {macro.get('sp500_change', 'N/A')}%",
        f"  Nasdaq today: {macro.get('nasdaq_change', 'N/A')}%",
        f"  USD/ILS: {macro.get('usd_ils', 'N/A')}",
        "",
        f"PORTFOLIO ({len(holdings)} holdings):",
    ]

    # Include top 5 best and worst by score
    for h in sorted(holdings, key=lambda x: -sum(x.get("scores", {}).values())):
        tk = h.get("ticker", "")
        v = h.get("verdict", "hold").upper()
        c = h.get("conviction", 0)
        scores = h.get("scores", {})
        scores_str = " ".join(f"{k[:3].upper()}{v}" for k, v in scores.items())
        lines.append(f"  {tk}: {v} {c}%  [{scores_str}]")

    if new_ideas:
        lines.append("")
        lines.append("NEW IDEAS:")
        for i in new_ideas:
            lines.append(f"  {i.get('ticker','')}: {i.get('name','')} (conviction {i.get('conviction',0)}%)")

    lines.extend([
        "",
        "YOUR TASK:",
        "Write a professional portfolio insights brief in HEBREW (4-6 short paragraphs).",
        "",
        "Cover these areas — ONLY what's actually interesting/actionable:",
        "1. **Portfolio Health** — the 1-2 most important things the client should know today",
        "2. **Hidden Risks** — correlations or concentration issues the algorithm may miss",
        "3. **Market Context** — how current macro environment affects THIS portfolio specifically",
        "4. **Opportunities** — which holdings show divergence between scores (e.g., great quality but bad timing)",
        "5. **Action Items** — 1-2 concrete things to consider (NOT financial advice)",
        "",
        "Rules:",
        "- HEBREW ONLY (except ticker symbols)",
        "- Use specific numbers from the data above",
        "- Be concise — 4-6 paragraphs max",
        "- No generic advice — every sentence must reference specific data",
        "- End with: 'סקירת שוק — אינה המלצה פיננסית.'",
        "",
        "Return JSON:",
        '{"headline": "one-line Hebrew headline", "insights": "the full Hebrew brief"}',
    ])

    return "\n".join(lines)


_SYSTEM_INSTRUCTION = (
    "You are a senior portfolio analyst. Write in Hebrew. "
    "When you need current market context (recent news, earnings calls, "
    "policy changes), use Google Search before answering."
)


def _try_grounded_call(prompt: str) -> str | None:
    """Run the brief via the native google-genai SDK with Google Search grounding.

    Why a separate path: langchain-google-genai (used elsewhere) doesn't expose
    the Search tool natively. Real-time grounding meaningfully improves the
    monthly brief, where stale knowledge is the failure mode.

    Tries the configured smart model first; on 429/quota, retries on the
    flash fallback (still grounded). Returns None on any other failure so
    the caller can fall back to the plain langchain path.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None

    primary = os.environ.get("GEMINI_SMART_MODEL", "gemini-pro-latest")
    fallback = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-flash-latest")
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        tools=[{"google_search": {}}],
        temperature=0.4,
        system_instruction=_SYSTEM_INSTRUCTION,
    )

    for model in (primary, fallback):
        try:
            resp = client.models.generate_content(
                model=model, contents=prompt, config=config,
            )
            return resp.text
        except Exception as e:
            err = str(e)
            is_quota = any(s in err for s in ("429", "RESOURCE_EXHAUSTED", "quota"))
            if is_quota and model == primary:
                print(f"[warn] grounded brief: {primary} quota exhausted, "
                      f"retrying with {fallback}", file=sys.stderr)
                continue
            print(f"[warn] grounded brief failed on {model}, "
                  f"falling back to langchain: {err[:200]}", file=sys.stderr)
            return None
    return None


def generate_smart_insights(llm_smart, recommendations: dict, macro: dict, settings: dict) -> dict:
    """Generate deep portfolio analysis using smart model (ONE call per day).

    Tries the grounded native-SDK path first (real-time web context), then
    falls back to the plain langchain client when grounding is unavailable.
    """
    import re

    prompt = _build_analysis_prompt(recommendations, macro, settings)

    try:
        content: str | None = _try_grounded_call(prompt)
        if content is None:
            resp = llm_smart.invoke([
                ("system", _SYSTEM_INSTRUCTION),
                ("user", prompt),
            ])
            content = resp.content if hasattr(resp, "content") else str(resp)
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "") if isinstance(p, dict)
                else p.text if hasattr(p, "text") else str(p)
                for p in content
            )

        # Parse JSON from response
        m = re.search(r"\{.*\}", str(content), re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            result = {
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
                "headline": parsed.get("headline", ""),
                "insights": parsed.get("insights", content[:2000]),
            }
        else:
            result = {
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
                "headline": "ניתוח יומי",
                "insights": str(content)[:2000],
            }

        _INSIGHTS_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    except Exception as e:
        return {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
            "headline": "Analysis unavailable",
            "insights": f"[error: {str(e)[:200]}]",
        }


def load_insights() -> dict:
    """Load the latest smart insights."""
    if _INSIGHTS_PATH.exists():
        try:
            return json.loads(_INSIGHTS_PATH.read_text())
        except Exception:
            pass
    return {}


def get_smart_llm():
    """Instantiate the 'smart' Gemini model for deep analysis.

    Uses gemini-3-pro for monthly deep-dive briefs; defaults overridable via env.
    ONE call per day, so even the more expensive model costs ~$0.001/run.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        # Smart Brief is monthly now — splurge on the best Pro model available
        # (`gemini-pro-latest` auto-resolves to current flagship Pro preview).
        model = os.environ.get("GEMINI_SMART_MODEL", "gemini-pro-latest")
        return ChatGoogleGenerativeAI(
            model=model, google_api_key=api_key,
            temperature=0.4, timeout=60, max_retries=0,
        )
    except Exception:
        return None
