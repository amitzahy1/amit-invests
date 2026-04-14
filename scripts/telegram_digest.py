#!/usr/bin/env python3
"""
Telegram digest — reads recommendations.json and pushes a summary to Telegram.

Uses existing OSS: python-telegram-bot (https://github.com/python-telegram-bot/python-telegram-bot)

Environment:
    TELEGRAM_BOT_TOKEN   from @BotFather
    TELEGRAM_CHAT_ID     your chat id (send a message to your bot, then visit
                         https://api.telegram.org/bot<TOKEN>/getUpdates)

Usage:
    python scripts/telegram_digest.py --once         # send a one-shot digest
    python scripts/telegram_digest.py --strong-only  # only send if any STRONG verdict
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass  # .env is optional

_ROOT = Path(__file__).resolve().parent.parent
RECS_PATH = _ROOT / "recommendations.json"
SETTINGS_PATH = _ROOT / "settings.json"

VERDICT_EMOJI = {"buy": "🟢", "sell": "🔴", "hold": "🟡"}


def _load_json(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def _format_digest(recs: dict) -> str:
    lines = []
    lines.append(f"*Portfolio Digest — {recs.get('updated', '')[:10]}*")
    lines.append(f"_Profile: {recs.get('profile_name', '—')}_")
    lines.append("")

    summary = recs.get("summary", "")
    if summary:
        lines.append(summary)
        lines.append("")

    holdings = recs.get("holdings", [])
    if holdings:
        lines.append("*Holdings*")
        for h in holdings:
            emoji = VERDICT_EMOJI.get((h.get("verdict") or "hold").lower(), "ℹ️")
            lines.append(
                f"{emoji} `{h.get('ticker', '')}` — "
                f"*{(h.get('verdict') or 'hold').upper()}* ({h.get('conviction', 0)}%)"
            )
        lines.append("")

    new_ideas = recs.get("new_ideas", [])
    if new_ideas:
        lines.append("*New Ideas*")
        for i in new_ideas:
            lines.append(
                f"🚀 `{i.get('ticker', '')}` — {i.get('name', '')} ({i.get('conviction', 0)}%)"
            )
            rationale = i.get("rationale", "")
            if rationale:
                lines.append(f"   _{rationale}_")
        lines.append("")

    lines.append("_Market commentary — not financial advice._")
    return "\n".join(lines)


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

    # Use the bare HTTP API so we avoid a heavy dependency for a tiny call.
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

    text = _format_digest(recs)
    send_telegram(text)
    print("[ok] digest sent")


if __name__ == "__main__":
    main()
