"""
How It Works — behind-the-scenes explanation of every component in the system.
Shows last-run timestamps for each agent, schedule, and data flow.
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, minify

from datetime import datetime
from pathlib import Path

import streamlit as st

inject_css()
inject_header("explainer")
handle_actions()
_ROOT = ROOT  # legacy alias used below

# Hero
st.markdown("""
<section class="hero">
  <div class="hero-top">
    <h1 class="lbl">How It Works</h1>
    <div class="mono" style="font-size:12px;color:var(--text-mute);">Architecture · Schedules · Agents · Data Sources</div>
  </div>

  <div class="hero-grid" style="grid-template-columns: repeat(4, 1fr);">
    <div class="hero-cell">
      <div class="lbl">LLM</div>
      <div class="hero-value hero-value-light" style="font-size:22px;">Gemini</div>
      <div class="hero-sub mono">flash-latest auto-alias</div>
    </div>
    <div class="hero-cell">
      <div class="lbl">Personas</div>
      <div class="hero-value tab">12</div>
      <div class="hero-sub">5 active by default</div>
    </div>
    <div class="hero-cell">
      <div class="lbl">Schedule</div>
      <div class="hero-value hero-value-light" style="font-size:22px;">16:35</div>
      <div class="hero-sub">Daily IDT · 5min after US open</div>
    </div>
    <div class="hero-cell">
      <div class="lbl">Stack</div>
      <div class="hero-value hero-value-light" style="font-size:22px;">Streamlit</div>
      <div class="hero-sub mono">Python 3.12 · launchd · Telegram</div>
    </div>
  </div>
</section>
<div style="height:24px;"></div>
""", unsafe_allow_html=True)
st.markdown('<div class="below-section">', unsafe_allow_html=True)


def _mtime(p: Path) -> str:
    if not p.exists():
        return "never"
    ts = datetime.fromtimestamp(p.stat().st_mtime)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _size(p: Path) -> str:
    if not p.exists():
        return "—"
    s = p.stat().st_size
    if s < 1024:
        return f"{s} B"
    if s < 1024 * 1024:
        return f"{s / 1024:.1f} KB"
    return f"{s / 1024 / 1024:.1f} MB"


# ─── Last-run dashboard ─────────────────────────────────────────────────────
st.markdown('<div class="section-header">⏱️ Last-Run Status</div>', unsafe_allow_html=True)

portfolio_path = _ROOT / "portfolio.json"
recs_path = _ROOT / "recommendations.json"
settings_path = _ROOT / "settings.json"
logs_dir = _ROOT / "logs"
latest_log = None
if logs_dir.exists():
    logs = sorted(logs_dir.glob("*.log"), reverse=True)
    latest_log = logs[0] if logs else None

status_rows = [
    ("📊 Portfolio data (portfolio.json)",
     _mtime(portfolio_path),
     _size(portfolio_path),
     "Source of truth for holdings. Updated by `sync_portfolio.py` (Yahoo Finance scrape) or manually."),
    ("🎯 Recommendations (recommendations.json)",
     _mtime(recs_path),
     _size(recs_path),
     "Written by `run_recommendations.py` via ai-hedge-fund."),
    ("⚙️ Settings (settings.json)",
     _mtime(settings_path),
     _size(settings_path),
     "Your personality profile. Changes here affect the next recommendation run."),
    ("📝 Latest daily log",
     _mtime(latest_log) if latest_log else "pipeline not yet scheduled",
     _size(latest_log) if latest_log else "—",
     (f"File: {latest_log.name} — written by `scripts/run_daily.sh` (via launchd)."
      if latest_log else
      "The `logs/` directory is created automatically the first time `scripts/run_daily.sh` runs. "
      "Load the launchd plist (see 'Daily Schedule' below) to enable it.")),
]

status_table_html = ''
for label, when, size, desc in status_rows:
    status_table_html += (
        f'<tr style="border-bottom:1px solid var(--hair-soft);">'
        f'<td style="padding:14px 16px;font-weight:500;font-size:13px;color:var(--text);vertical-align:top;">{label}</td>'
        f'<td style="padding:14px 16px;font-family:\'IBM Plex Mono\',monospace;font-size:12px;color:var(--text-dim);white-space:nowrap;vertical-align:top;">{when}</td>'
        f'<td style="padding:14px 16px;font-family:\'IBM Plex Mono\',monospace;font-size:12px;color:var(--text-mute);white-space:nowrap;vertical-align:top;">{size}</td>'
        f'<td style="padding:14px 16px;font-size:12px;color:var(--text-dim);line-height:1.6;vertical-align:top;">{desc}</td>'
        f'</tr>'
    )

st.markdown(
    f'<div style="border:1px solid var(--hair);background:white;overflow:hidden;">'
    f'<table style="width:100%;border-collapse:collapse;">'
    f'<thead><tr style="background:var(--bg-softer);border-bottom:1px solid var(--hair);">'
    f'<th style="text-align:left;padding:12px 16px;font-size:10px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);font-weight:500;">Component</th>'
    f'<th style="text-align:left;padding:12px 16px;font-size:10px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);font-weight:500;">Last Updated</th>'
    f'<th style="text-align:left;padding:12px 16px;font-size:10px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);font-weight:500;">Size</th>'
    f'<th style="text-align:left;padding:12px 16px;font-size:10px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);font-weight:500;">Source</th>'
    f'</tr></thead>'
    f'<tbody>{status_table_html}</tbody>'
    f'</table></div>',
    unsafe_allow_html=True,
)

# ─── Daily timeline ─────────────────────────────────────────────────────────
st.markdown('<div style="height:32px;"></div>', unsafe_allow_html=True)
st.markdown('<div class="sect-head"><div><h2>Daily Schedule</h2><div class="sect-sub">Automated pipeline runs every weekday at 16:35 IDT</div></div></div>', unsafe_allow_html=True)
st.markdown("""
The whole pipeline runs once a day via **macOS launchd** — no server, no cloud.
The plist lives at `launchd/com.amit.invest.daily.plist` and is loaded into
`~/Library/LaunchAgents/`.

| Time (local) | Agent | What it does | Output |
|---|---|---|---|
| **16:35** (5 min after US open) | `run_recommendations.py` | Calls Gemini 75+ times (15 holdings × 5 personas + summary + new ideas), tuned to your profile | `recommendations.json` |
| **16:35** | `snapshot_portfolio.py` | Records today's total value, sector weights, top holdings | appends to `snapshots.jsonl` |
| **16:35** | `telegram_digest.py` | Reads the fresh `recommendations.json`, formats a digest, sends to Telegram | Telegram message |

If any step fails, the others still run. Logs go to `logs/YYYY-MM-DD.log`.

**To run manually right now:**

```bash
bash scripts/run_daily.sh          # full pipeline
python scripts/run_recommendations.py --dry-run  # just the AI agents
python scripts/telegram_digest.py --once         # just re-send the last digest
```
""")


# ─── Agent deep-dives ───────────────────────────────────────────────────────
st.markdown('<div class="section-header">🤖 The Agents</div>', unsafe_allow_html=True)

with st.expander("📥 Portfolio sync — manual CSV upload", expanded=False):
    st.markdown("""
**What it does.** You export a CSV from your broker (Extrade Pro, Yahoo Finance, anywhere) and
upload it on the **Import** tab. The app parses it, shows you a diff vs the current state
(what's being added / removed / changed), and waits for your approval before writing
`portfolio.json`. Pure manual control, zero credentials stored.

**Why manual and not automated.** We considered automated scraping via browser-use + Gemini
but dropped it because:
1. Yahoo Finance has CAPTCHA + 2FA on login — automation is brittle
2. Giving an LLM any broker credentials is a security risk even for read-only sites
3. You update the portfolio infrequently anyway (bi-monthly contributions)

**CSV flexibility.** The parser auto-detects column names in English **and** Hebrew. Minimum
required: Ticker/Symbol + Quantity. Cost Price is optional — holdings without cost basis get
flagged with a red banner until you set it.

**Merge modes.**
- *Replace*: CSV becomes the source of truth; any holding missing from the CSV gets dropped.
- *Add/update only*: only touches tickers that appear in the CSV — other holdings stay.
""")

with st.expander("🎯 Recommendations engine — direct Gemini (multi-persona)", expanded=False):
    st.markdown("""
**What it does.** `scripts/run_recommendations.py` calls **Gemini directly** via
`langchain-google-genai`. For each of your active personas × each of your holdings, it sends a
tailored prompt and parses a structured JSON response (`{verdict, conviction, rationale}` in
Hebrew). Then it aggregates per-persona votes into a single overall verdict per holding
(weighted voting: the majority verdict wins, conviction averaged).

**We do NOT use virattt/ai-hedge-fund.** We started with that plan — the personas are *inspired* by
their list — but the persona prompts, rationale templates, and aggregation are all ours. One
less dependency, one less venv, clean control over prompts, and we can call Gemini directly
without shelling out.

**Model.** **`gemini-flash-latest`** — Google's auto-aliased pointer to the newest stable Gemini
Flash. When Gemini 3 Flash goes GA, we inherit it automatically, no code change. Override with
`GEMINI_MODEL` in `.env` (e.g. `gemini-2.5-pro` for higher quality at the cost of speed).

**Your profile injection.** Before each run the script reads `settings.json` and builds a
system-prompt preamble:

```
PROFILE: Amit — Conservative AI Bull
Trading style: conservative · Horizon: 4y · Risk: medium
Crypto cap: 3% of portfolio
Preferred sectors: Technology, Aerospace & Defense, Energy/Nuclear, Healthcare, ...
Avoid sectors: Crypto
Theses:
  - AI is the dominant growth story of the next 3 years...
  - Google/Alphabet is a core long-term AI winner...
  - Israeli insurance sector is a defensive diversifier...
Rationale language: WRITE ALL RATIONALE TEXT AND THE DAILY SUMMARY IN HEBREW.
```

This preamble is prepended to every single Gemini call. So when `warren_buffett` scores NVDA,
it does so through the lens of **your** profile, not a generic Buffett.

**Available personas** (12 in `scripts/run_recommendations.py:PERSONA_SYSTEM_PROMPTS`):
Warren Buffett, Charlie Munger, Cathie Wood, Peter Lynch, Michael Burry, Ben Graham,
Technical Analyst, Fundamentals Analyst, Valuation, Sentiment, Macro, Risk Manager.
Pick any subset in the Settings page — defaults to 5.

**Load.** 5 personas × 16 holdings = 80 calls + 1 for new ideas + 1 for summary = ~82 calls per
daily run. Free tier is 1,500 calls/day → we use ~5%.

**Retries.** Each call wraps in `_invoke_with_retry` with exponential backoff on 503/429
(Gemini occasionally overloads). Up to 5 attempts, 2→17s backoff.

**Dry-run mode.** Without an API key, `--dry-run` produces Hebrew rationales from a hand-tuned
lookup table. The Recommendations page renders identically — useful for development/UX tweaks
without burning API quota.
""")

with st.expander("📲 Telegram digest — daily push (and strong-signal alerts)", expanded=False):
    st.markdown("""
**What it sends.** Every morning after the recommendations are generated, a formatted digest:

- Profile name + date
- The Hebrew daily summary from the Risk Manager persona
- One line per holding with the verdict emoji, ticker, verdict, and conviction %
- All "new ideas" with their 1-line Hebrew rationale
- Legal tagline: *market commentary — not financial advice*

**When alerts fire.** If any holding has a BUY or SELL verdict at ≥75% conviction, you also
get an immediate push (not just the daily digest) — controlled by the
*"Send immediate alert on STRONG BUY / STRONG SELL"* toggle in Settings.

**Credentials** are stored in environment secrets — `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

**Privacy.** Messages go directly to Telegram's Bot API from your Mac. No third-party server
in the middle.
""")


# ─── Data sources ───────────────────────────────────────────────────────────
st.markdown('<div class="section-header">📡 Data Sources</div>', unsafe_allow_html=True)
st.markdown("""
| Source | What we read | How often | Notes |
|---|---|---|---|
| **Yahoo Finance v8 API** | Live prices, historical OHLCV, USD/ILS | Every 5 min (cached) | Public endpoint, no auth. Used for the Portfolio chart + drill-down. |
| **CSV upload** (manual) | Your holdings list | When you upload it | Import tab. Parses English & Hebrew column headers. Diff + approve flow. |
| **ai-hedge-fund internal sources** | Fundamentals, news, earnings | Daily at 07:31 | Wraps Financial Datasets API + news feeds. Rate-limited per persona. |
| **Your `settings.json`** | Profile, theses, thresholds | On save | Injected into every recommendation run. |
""")


# ─── Logic map ──────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">🔄 End-to-End Flow</div>', unsafe_allow_html=True)
st.markdown("""
```
   You (manual, anytime):
   CSV from broker ──▶ Import tab ──▶ diff/approve ──▶ portfolio.json

              ┌─────────────────────────────┐
              │  launchd (16:35 IDT daily)  │   ← 5 min after US open
              └──────────────┬──────────────┘
                             ▼
      ┌───────────────────────────────────────────┐
      │ scripts/run_daily.sh                      │
      │                                           │
      │ 1. run_recommendations.py ───(Gemini)────▶│ gemini-flash-latest
      │    (settings.json injected as system      │
      │     prompt; 5 personas per holding)       │
      │                                           │
      │ 2. snapshot_portfolio.py                  │
      │                                           │
      │ 3. telegram_digest.py ─────(Bot API)─────▶│ Telegram
      └──────┬───────────────┬──────────────┬─────┘
             ▼               ▼              ▼
     snapshots.jsonl   recommendations.json  (message)
                             │
                             ▼
                     ┌──────────────┐
                     │  Streamlit   │  ← 6 tabs at the top
                     │   (you!)     │
                     └──────────────┘
```

**Cache behaviour.** The Portfolio page caches Yahoo quote + historical data for 5 minutes.
Hit "Refresh" in the sidebar to clear it. The Recommendations and Settings pages read their
JSON files fresh on every render — no cache, because those files change.

**What happens if you change Settings.** Saving the form rewrites `settings.json`. The next
`run_recommendations.py` invocation picks it up automatically. You don't need to restart the app
or reload anything.
""")

st.markdown("---")
st.caption("This page reads live file timestamps — refresh to see updated values.")
