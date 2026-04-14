#!/usr/bin/env bash
# Daily pipeline: sync portfolio from Yahoo → run AI recommendations → snapshot → Telegram digest.
# Invoked daily by launchd at 16:35 Israel time (5 min after US market open).
set -e

# Resolve project root from this script's location (portable across machines)
PROJECT="${PROJECT:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHON="$PROJECT/.venv/bin/python"   # Use the project's venv (Python 3.12)
LOG_DIR="$PROJECT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"

cd "$PROJECT"

# Load .env into this shell so scripts see credentials without extra work
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

{
  echo "===== $(date +%Y-%m-%dT%H:%M:%S%z) ====="
  echo "(Portfolio sync is manual via CSV upload in the Streamlit UI — no browser automation.)"

  echo "[1/4] pre-warm data caches (fundamentals + macro + news)"
  "$PYTHON" -c "
from dotenv import load_dotenv; load_dotenv()
from data_loader_fundamentals import fetch_all_fundamentals, fetch_all_news
from data_loader_macro import fetch_macro_snapshot
import json
tickers = [h['ticker'] for h in json.loads(open('portfolio.json').read()).get('holdings', [])]
fetch_all_fundamentals(tickers)
fetch_all_news(tickers)
fetch_macro_snapshot()
print('[ok] caches warm')
" || echo "[warn] cache warm-up failed — recommendations will fetch on demand"

  echo "[2/4] run recommendations (real, using Gemini + scoring engine)"
  "$PYTHON" scripts/run_recommendations.py --once || {
    echo "[warn] real run failed — falling back to dry-run"
    "$PYTHON" scripts/run_recommendations.py --dry-run
  }

  echo "[3/4] snapshot portfolio value"
  "$PYTHON" scripts/snapshot_portfolio.py || echo "[warn] snapshot failed"

  echo "[4/4] push Telegram digest (market context + lesson + charts)"
  "$PYTHON" scripts/telegram_digest.py --once || echo "[warn] telegram digest failed"

  echo "[done]"
} >> "$LOG" 2>&1
