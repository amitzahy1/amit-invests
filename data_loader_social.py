"""
Social Sentiment — fetches Twitter/X discussion and news sentiment via Perplexity API.

Why Perplexity instead of direct X API:
- X API basic tier: $100/month, limited
- Perplexity API: $5/month starter tier, includes Twitter search + news
- Returns synthesized sentiment + top headlines — no scraping

Cache TTL: 4 hours (social sentiment changes fast but we don't want per-run calls).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ROOT = Path(__file__).resolve().parent
_CACHE_PATH = _ROOT / "social_sentiment_cache.json"
_TIMEOUT = 15


def _perplexity_key() -> str | None:
    return os.environ.get("PERPLEXITY_API_KEY")


def _load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    _CACHE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _cache_is_fresh(cache: dict, max_age_hours: float = 4) -> bool:
    ts = cache.get("updated")
    if not ts:
        return False
    try:
        age = (datetime.now(timezone.utc) -
               datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds()
        return age < max_age_hours * 3600
    except Exception:
        return False


def fetch_social_sentiment(ticker: str) -> dict | None:
    """Query Perplexity for Twitter + news sentiment on a ticker.

    Returns:
        {
            "sentiment_score": 0-100,  # 0 = very negative, 100 = very positive
            "label": "bullish" | "neutral" | "bearish",
            "top_themes": [str, str, str],  # top 3 discussion themes
            "key_accounts": [str, ...],  # analysts / fund managers mentioned
        }
        or None if API fails.
    """
    key = _perplexity_key()
    if not key:
        return None

    query = (
        f"What is the current sentiment on ${ticker} stock on Twitter/X and financial news? "
        f"Analyze posts from the past 48 hours from analysts, hedge fund managers, "
        f"and financial journalists. Return JSON with: "
        f'sentiment_score (0-100, where 0=very_bearish, 50=neutral, 100=very_bullish), '
        f'label ("bullish"/"neutral"/"bearish"), '
        f'top_themes (array of 3 short strings describing what people are talking about), '
        f'key_accounts (array of up to 3 notable analyst/manager names mentioning it). '
        f"Return ONLY the JSON object, no other text."
    )

    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [
                    {"role": "system", "content": "You are a financial sentiment analyst. Return only valid JSON."},
                    {"role": "user", "content": query},
                ],
                "temperature": 0.2,
            },
            timeout=_TIMEOUT,
            verify=False,
        )
        if resp.status_code != 200:
            return None
        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        return {
            "sentiment_score": int(data.get("sentiment_score", 50)),
            "label": (data.get("label") or "neutral").lower(),
            "top_themes": data.get("top_themes", [])[:3],
            "key_accounts": data.get("key_accounts", [])[:3],
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        }
    except Exception:
        return None


def fetch_all_social_sentiment(tickers: list[str]) -> dict[str, dict]:
    """Fetch social sentiment for all tickers with caching (4h TTL)."""
    cache = _load_cache()
    cached_tickers = cache.get("tickers", {})
    is_fresh = _cache_is_fresh(cache, max_age_hours=4)

    result: dict[str, dict] = {}
    need_fetch = []

    for tk in tickers:
        if tk.endswith(".TA"):
            continue  # skip Israeli — Perplexity coverage is US-focused
        if is_fresh and tk in cached_tickers:
            result[tk] = cached_tickers[tk]
        else:
            need_fetch.append(tk)

    if not need_fetch:
        return result

    if not _perplexity_key():
        print("[warn] PERPLEXITY_API_KEY not set — skipping social sentiment",
              flush=True)
        return result

    import time
    print(f"[info] fetching social sentiment for {len(need_fetch)} tickers…",
          flush=True)
    for tk in need_fetch:
        data = fetch_social_sentiment(tk)
        if data:
            result[tk] = data
            cached_tickers[tk] = data
        time.sleep(1)  # rate-limit politely

    cache["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
    cache["tickers"] = cached_tickers
    _save_cache(cache)
    return result
