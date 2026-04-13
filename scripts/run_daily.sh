#!/usr/bin/env bash
# Daily pipeline: sync portfolio from Yahoo → run AI recommendations → snapshot → Telegram digest.
# Invoked daily by launchd at 16:35 Israel time (5 min after US market open).
set -e

PROJECT="/Users/amitzahy/Documents/Draft/Amit Invests"
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

  echo "[1/3] run recommendations (real, using Gemini)"
  "$PYTHON" scripts/run_recommendations.py --once || {
    echo "[warn] real run failed — falling back to dry-run"
    "$PYTHON" scripts/run_recommendations.py --dry-run
  }

  echo "[2/3] snapshot portfolio value"
  "$PYTHON" scripts/snapshot_portfolio.py || echo "[warn] snapshot failed"

  echo "[3/3] push Telegram digest"
  "$PYTHON" scripts/telegram_digest.py --once || echo "[warn] telegram digest failed"

  echo "[done]"
} >> "$LOG" 2>&1
