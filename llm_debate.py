"""Bull / Bear / Judge debate for per-holding rationale.

Replaces the single-shot `_scoring_synthesis_call` with a structured debate.
Pattern from TauricResearch/TradingAgents + virattt/ai-hedge-fund (2026 SOTA).

Flow:
    1. Bull agent   — given scores + context, argues the most compelling BUY case.
    2. Bear agent   — independently argues the most compelling SELL case.
    3. Judge agent  — sees both theses + the raw scores, produces the final
                      verdict / conviction / Hebrew rationale that weighs them.

Cost: 3 LLM calls per holding instead of 1 — on gemini-3-flash that's still
fractions of a cent per holding. Quality uplift is substantial because
adversarial roles force the model to surface counter-evidence it would
otherwise gloss over.

Environment:
    GEMINI_DEBATE=false        # disable; fall back to single-call synthesis
    GEMINI_DEBATE_MAX_WORDS=120  # per agent, keep theses tight
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable


_DEFAULT_MAX_WORDS = 120


def _format_scores_block(scores: dict, score_details: dict | None) -> str:
    """Render scores + their first-line justification so the agents can cite evidence."""
    details = score_details or {}
    lines = []
    for k, v in scores.items():
        first_detail = ""
        d = details.get(k)
        if d:
            if isinstance(d, list) and d:
                first_detail = f" — {d[0].strip()[:90]}"
            elif isinstance(d, str):
                first_detail = f" — {d.strip()[:90]}"
        tag = "strong" if v > 70 else "weak" if v < 30 else ""
        lines.append(f"  {k.title():14s} {v}/100 {tag}{first_detail}")
    avg = sum(scores.values()) / max(1, len(scores))
    lines.append(f"  Weighted avg: {avg:.0f}/100")
    return "\n".join(lines)


def _bull_prompt(ticker: str, display_name: str, scores_block: str,
                 market_context: str, max_words: int) -> tuple[str, str]:
    system = (
        "You are a Bull analyst. Your job: make the strongest possible BUY case "
        f"for {ticker} in under {max_words} words, based ONLY on the data below. "
        "Cite specific scores and numbers. Do not invent data. If the data is "
        "genuinely weak, concede one weakness and explain why you still lean BUY."
    )
    user = (
        f"Asset: {display_name} ({ticker})\n\n"
        f"SCORES:\n{scores_block}\n\n"
        f"MARKET CONTEXT:\n{market_context}\n\n"
        f"Write the Bull thesis. Be concrete. Use numbers."
    )
    return system, user


def _bear_prompt(ticker: str, display_name: str, scores_block: str,
                 market_context: str, max_words: int) -> tuple[str, str]:
    system = (
        "You are a Bear analyst. Your job: make the strongest possible SELL / AVOID "
        f"case for {ticker} in under {max_words} words, based ONLY on the data "
        "below. Cite specific scores and numbers. Do not invent data. If data is "
        "actually bullish, concede one strength and explain why you still lean bearish."
    )
    user = (
        f"Asset: {display_name} ({ticker})\n\n"
        f"SCORES:\n{scores_block}\n\n"
        f"MARKET CONTEXT:\n{market_context}\n\n"
        f"Write the Bear thesis. Be concrete. Use numbers."
    )
    return system, user


def _judge_prompt(ticker: str, display_name: str, scores_block: str,
                  bull_thesis: str, bear_thesis: str,
                  per_ticker_schema: str) -> tuple[str, str]:
    system = (
        "אתה שופט השקעות ותיק. קיבלת: ציונים אלגוריתמיים, תזה של בולי, ותזה של דובי. "
        "תפקידך: לשקלל את הראיות ולהפיק verdict סופי (buy / hold / sell), רמת ביטחון "
        "(0-100), ונימוק קצר בעברית של 2-3 משפטים. אל תמציא נתונים — השתמש רק במה "
        "שהבול והדובי הציגו. ציין בצד איזו טענה הכריעה (bull_wins / bear_wins / split)."
    )
    user = (
        f"נכס: {display_name} ({ticker})\n\n"
        f"ALGORITHMIC SCORES:\n{scores_block}\n\n"
        f"---- BULL THESIS ----\n{bull_thesis}\n\n"
        f"---- BEAR THESIS ----\n{bear_thesis}\n\n"
        f"הפק JSON בדיוק לפי הסכמה: {per_ticker_schema}\n"
        "הוסף שדה נוסף: \"debate_winner\": \"bull\"|\"bear\"|\"split\"."
    )
    return system, user


def _call_text(invoker: Callable, system: str, user: str) -> str:
    """Invoke the LLM and extract plain text. `invoker` is the caller-provided
    function that takes (messages) and returns a response object with .content."""
    try:
        resp = invoker([("system", system), ("user", user)])
    except Exception as e:
        return f"[error: {str(e)[:120]}]"
    content = resp.content if hasattr(resp, "content") else str(resp)
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(p.get("text", ""))
            elif hasattr(p, "text"):
                parts.append(p.text)
            else:
                parts.append(str(p))
        content = "\n".join(parts)
    return content if isinstance(content, str) else str(content)


def _parse_judge_json(text: str) -> dict:
    """Best-effort JSON extraction from judge response."""
    # Try fenced ```json``` first
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # Fallback: first {...} block
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def debate_rationale(
    invoker: Callable,
    ticker: str,
    display_name: str,
    scores: dict,
    market_context: str,
    per_ticker_schema: str,
    score_details: dict | None = None,
    max_words_per_side: int | None = None,
) -> dict:
    """Run the 3-step Bull/Bear/Judge pipeline. Returns:
        {verdict, conviction, rationale, debate_winner, bull_thesis, bear_thesis}
    """
    max_words = int(max_words_per_side
                    or os.environ.get("GEMINI_DEBATE_MAX_WORDS", _DEFAULT_MAX_WORDS))
    scores_block = _format_scores_block(scores, score_details)

    bull_sys, bull_usr = _bull_prompt(ticker, display_name, scores_block,
                                      market_context, max_words)
    bull_thesis = _call_text(invoker, bull_sys, bull_usr).strip()

    bear_sys, bear_usr = _bear_prompt(ticker, display_name, scores_block,
                                      market_context, max_words)
    bear_thesis = _call_text(invoker, bear_sys, bear_usr).strip()

    # If both sides errored, no point in summoning the judge.
    if bull_thesis.startswith("[error") and bear_thesis.startswith("[error"):
        return {
            "verdict": "hold", "conviction": 50,
            "rationale": "[debate failed — both agents errored]",
            "debate_winner": "split",
            "bull_thesis": bull_thesis, "bear_thesis": bear_thesis,
        }

    judge_sys, judge_usr = _judge_prompt(
        ticker, display_name, scores_block, bull_thesis, bear_thesis,
        per_ticker_schema,
    )
    judge_text = _call_text(invoker, judge_sys, judge_usr).strip()
    parsed = _parse_judge_json(judge_text)

    verdict = (parsed.get("verdict") or "hold").lower()
    if verdict not in ("buy", "hold", "sell"):
        verdict = "hold"
    conviction = parsed.get("conviction", 50)
    try:
        conviction = int(conviction)
    except (TypeError, ValueError):
        conviction = 50
    rationale = parsed.get("rationale") or judge_text[:500]
    winner = (parsed.get("debate_winner") or "split").lower()
    if winner not in ("bull", "bear", "split"):
        winner = "split"

    return {
        "verdict": verdict,
        "conviction": conviction,
        "rationale": rationale,
        "debate_winner": winner,
        "bull_thesis": bull_thesis,
        "bear_thesis": bear_thesis,
    }
