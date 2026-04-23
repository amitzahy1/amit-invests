"""News sentiment scorer — Loughran-McDonald-inspired finance lexicon.

Pure-Python, no heavy ML deps (no torch / no transformers). Uses a hand-curated
lexicon of ~180 finance-specific positive / negative terms derived from the
Loughran-McDonald Master Dictionary (the academic gold standard for financial
text sentiment before the transformer era).

Why not FinBERT? FinBERT is ~44% more accurate on financial text but requires
~1.5 GB of torch + transformers. For the marginal benefit on a personal
portfolio tracker, the trade-off isn't worth it here. If the user ever installs
transformers, `score_with_finbert()` is the drop-in upgrade path.

Aggregates headline-level scores per ticker into a single 0-100 score.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent
_CACHE = _ROOT / "news_sentiment_cache.json"
_CACHE_TTL_SEC = 6 * 3600

# Loughran-McDonald-inspired finance lexicon. Words are stemmed-ish so they
# match common forms (e.g. "beat" also matches "beats"/"beating" via substring).
_POSITIVE = {
    # Earnings / fundamentals beats
    "beat", "beats", "beaten", "outperform", "outperforms", "outperformed",
    "exceed", "exceeds", "exceeded", "surpass", "surpasses", "surpassed",
    "record", "highs", "all-time", "record-high",
    # Growth
    "surge", "surges", "surged", "soar", "soars", "soared", "rally", "rallies",
    "rallied", "climb", "climbs", "climbed", "jump", "jumps", "jumped",
    "rise", "rises", "rose", "gain", "gains", "gained", "advance", "advanced",
    "growth", "growing", "grow", "grew", "expansion", "expanding",
    # Analyst / ratings
    "upgrade", "upgraded", "upgrades", "bullish", "buy-rated", "outperform-rated",
    "overweight", "strong-buy",
    # Business positives
    "profit", "profitable", "profits", "surplus", "positive", "bullish",
    "improve", "improved", "improving", "improvement", "breakthrough",
    "milestone", "momentum", "accelerate", "accelerating",
    # Capital return
    "buyback", "dividend", "dividend-hike", "raising-guidance", "raised-guidance",
    # Contract / deal positives
    "wins", "won", "secured", "approved", "approval", "launched", "launches",
    "partnership", "expands", "expanded",
}

_NEGATIVE = {
    # Earnings / fundamentals misses
    "miss", "misses", "missed", "disappoint", "disappoints", "disappointed",
    "shortfall", "shortfalls", "underperform", "underperforms", "underperformed",
    # Decline
    "plunge", "plunges", "plunged", "tumble", "tumbles", "tumbled",
    "fall", "falls", "fell", "drop", "drops", "dropped",
    "decline", "declines", "declined", "slump", "slumps", "slumped",
    "sink", "sinks", "sank", "slide", "slides", "slid",
    "crash", "crashed", "plummet", "plummeted",
    # Analyst / ratings
    "downgrade", "downgraded", "downgrades", "bearish", "sell-rated",
    "underperform-rated", "underweight", "strong-sell",
    # Business negatives
    "loss", "losses", "loss-making", "unprofitable", "deficit", "negative",
    "impairment", "writedown", "write-down", "writeoff", "write-off",
    "layoff", "layoffs", "fired", "job-cuts", "restructuring",
    "investigation", "subpoena", "lawsuit", "sued", "fraud", "settlement",
    "recall", "recalled", "warning", "warn", "warns", "warned",
    "guidance-cut", "cut-guidance", "lowered-guidance",
    # Macro negatives
    "headwinds", "challenging", "pressure", "slowdown", "weakness", "weaker",
    "concern", "concerns", "concerning", "uncertainty", "uncertain",
    "recession", "crisis", "bankruptcy", "bankrupt", "default", "defaulted",
    "inflation-concern",
}

# Negation triggers — if one appears within 3 tokens before a sentiment word
# it flips the polarity. Cheap but effective.
_NEGATIONS = {"not", "no", "never", "without", "hardly", "barely", "fails", "failed"}


def _tokenise(text: str) -> list[str]:
    # Keep hyphens because many finance terms are compounded.
    return re.findall(r"[a-zA-Z][a-zA-Z\-']+", text.lower())


def _headline_score(text: str) -> int:
    """Score a single headline/snippet. Returns int in [-3, +3]."""
    tokens = _tokenise(text)
    if not tokens:
        return 0
    score = 0
    for i, tok in enumerate(tokens):
        polarity = 0
        if tok in _POSITIVE:
            polarity = 1
        elif tok in _NEGATIVE:
            polarity = -1
        if polarity == 0:
            continue
        # Negation look-back: any negation in the 3 preceding tokens flips sign.
        lookback = tokens[max(0, i - 3):i]
        if any(n in _NEGATIONS for n in lookback):
            polarity *= -1
        score += polarity
    # Clamp to prevent a single overly-long, repetitive headline from dominating.
    return max(-3, min(3, score))


def _aggregate(headlines: list[str]) -> tuple[int, int, int, int]:
    """Return (bullish_count, neutral_count, bearish_count, score_0_100)."""
    if not headlines:
        return 0, 0, 0, 50
    bulls = bears = neutrals = 0
    total = 0
    for h in headlines:
        s = _headline_score(h)
        total += s
        if s > 0:
            bulls += 1
        elif s < 0:
            bears += 1
        else:
            neutrals += 1
    # Map the raw total (range roughly -3N..+3N) to 0-100 around neutral 50.
    # Saturate at ±(2 * len(headlines)) so a few strong ones don't over-express.
    n = max(1, len(headlines))
    norm = max(-1.0, min(1.0, total / (2 * n)))
    score = int(round(50 + norm * 50))
    return bulls, neutrals, bears, max(0, min(100, score))


def _load_cache() -> dict:
    if not _CACHE.exists():
        return {}
    try:
        return json.loads(_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
        return age < _CACHE_TTL_SEC
    except Exception:
        return False


def score_ticker_news(ticker: str, headlines: Optional[list[str]] = None,
                      max_headlines: int = 10) -> dict:
    """Score a ticker's recent news. Returns:
        {score: 0-100, bulls, neutrals, bears, top_headlines, used_count}

    If `headlines` not supplied, falls back to fetching via
    `data_loader_fundamentals.fetch_news_headlines`.
    """
    cache = _load_cache()
    entry = cache.get(ticker)
    if entry and _fresh(entry) and headlines is None:
        return entry.get("data")

    if headlines is None:
        try:
            from data_loader_fundamentals import fetch_news_headlines
            headlines = fetch_news_headlines(ticker, max_items=max_headlines) or []
        except Exception:
            headlines = []

    headlines = headlines[:max_headlines]
    b, n, br, score = _aggregate(headlines)

    # Pick the 3 most-polar headlines as "top"
    scored = sorted(
        ((_headline_score(h), h) for h in headlines),
        key=lambda x: -abs(x[0]),
    )
    top = [{"headline": h, "score": s} for s, h in scored[:3]]

    out = {
        "ticker": ticker,
        "score": score,
        "bulls": b, "neutrals": n, "bears": br,
        "top_headlines": top,
        "used_count": len(headlines),
    }

    cache[ticker] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": out,
    }
    _save_cache(cache)
    return out


def explain_news_sentiment(info: Optional[dict]) -> list[str]:
    """Return 1-3 short English lines describing the signal — for score_details."""
    if not info or info.get("used_count", 0) == 0:
        return ["No recent news headlines available"]
    b = info.get("bulls", 0)
    n = info.get("neutrals", 0)
    br = info.get("bears", 0)
    lines = [f"News last 24h: {b} bullish / {n} neutral / {br} bearish"]
    top = info.get("top_headlines", [])
    if top:
        h = top[0]
        tone = "bullish" if h["score"] > 0 else "bearish" if h["score"] < 0 else "neutral"
        lines.append(f"Top {tone}: \"{h['headline'][:80]}\"")
    return lines


# ── Drop-in upgrade path (only used if transformers + torch are installed) ──

def score_with_finbert(headlines: list[str]) -> Optional[int]:
    """Alternative scorer using HuggingFace ProsusAI/finbert.

    Returns a 0-100 score (50 = neutral), or None if transformers is not
    available. First call downloads the ~440MB model to ~/.cache/huggingface/.
    """
    try:
        from transformers import pipeline  # type: ignore
    except ImportError:
        return None

    try:
        pipe = pipeline("sentiment-analysis", model="ProsusAI/finbert",
                        truncation=True)
    except Exception:
        return None

    if not headlines:
        return 50

    pos = neg = 0
    for h in headlines:
        try:
            res = pipe(h[:512])[0]
            label = res["label"].lower()
            score = float(res["score"])
            if label == "positive":
                pos += score
            elif label == "negative":
                neg += score
        except Exception:
            continue
    total = pos + neg
    if total == 0:
        return 50
    return int(round(pos / total * 100))
