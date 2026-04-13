"""
Inbox — timeline of what happened: syncs, recommendation runs, alerts, setting changes.
Reads events from `logs/*.log`, file mtimes, and transactions in portfolio.json.
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, load_json, minify

from datetime import datetime
from pathlib import Path

import streamlit as st

inject_css()
inject_header("inbox")
handle_actions()


# ─── Collect events ─────────────────────────────────────────────────────────
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


events: list[tuple[float, str, str, str, str]] = []  # (timestamp, icon, title, body, kind)

portfolio_path = ROOT / "portfolio.json"
recs_path = ROOT / "recommendations.json"
settings_path = ROOT / "settings.json"
logs_dir = ROOT / "logs"

# File-mtime events
for p, icon, title, kind in [
    (portfolio_path, "📊", "Portfolio updated",       "portfolio"),
    (recs_path,      "🎯", "Recommendations regenerated", "recs"),
    (settings_path,  "⚙️", "Settings changed",         "settings"),
]:
    if p.exists():
        events.append(
            (p.stat().st_mtime, icon, title,
             f"File <code>{p.name}</code> was modified.", kind)
        )

# Transactions from portfolio.json
portfolio = load_json("portfolio.json")
for t in portfolio.get("transactions", []):
    date_str = t.get("date") or ""
    try:
        ts = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
    except ValueError:
        ts = datetime.utcnow().timestamp()
    t_type = (t.get("type") or "").lower()
    icon = {"buy": "🟢", "sell": "🔴", "sync": "🔄", "snapshot": "📸",
            "csv_import": "📥"}.get(t_type, "📝")
    title = f"{t_type.replace('_', ' ').title() or 'Transaction'}"
    body = t.get("description", "")
    events.append((ts, icon, title, body, "transaction"))

# Strong verdicts
recs = load_json("recommendations.json")
strong = [
    h for h in recs.get("holdings", [])
    if (h.get("verdict") or "").lower() in ("buy", "sell") and int(h.get("conviction", 0)) >= 75
]
if strong and recs.get("updated"):
    try:
        ts = datetime.fromisoformat(recs["updated"].rstrip("Z")).timestamp()
    except Exception:
        ts = datetime.utcnow().timestamp()
    for h in strong:
        tk = h.get("ticker", "")
        v = (h.get("verdict") or "").upper()
        c = int(h.get("conviction", 0))
        icon = "🚨" if v == "SELL" else "⭐"
        events.append(
            (ts, icon, f"Strong {v} signal — {tk} ({c}%)",
             "Conviction ≥75%. This triggers a Telegram alert if enabled.", "alert")
        )

# Daily log summaries
if logs_dir.exists():
    for log_file in sorted(logs_dir.glob("*.log"), reverse=True)[:7]:
        events.append(
            (log_file.stat().st_mtime, "📝", f"Daily pipeline ran — {log_file.stem}",
             f"See <code>logs/{log_file.name}</code>.", "pipeline")
        )

events.sort(key=lambda e: -e[0])

# ─── Hero ──────────────────────────────────────────────────────────────────
n_alerts = sum(1 for e in events if e[4] == "alert")
n_transactions = sum(1 for e in events if e[4] == "transaction")
n_pipelines = sum(1 for e in events if e[4] == "pipeline")
last_event_when = _iso(events[0][0])[:16] if events else "—"

st.markdown(minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Inbox — Activity Timeline</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">Newest first · Last event {last_event_when}</div>
</div>
<div class="hero-grid" style="grid-template-columns: repeat(4, 1fr);">
<div class="hero-cell">
<div class="lbl">Total Events</div>
<div class="hero-value tab">{len(events)}</div>
<div class="hero-sub">Across all sources</div>
</div>
<div class="hero-cell">
<div class="lbl">Strong Alerts</div>
<div class="hero-value tab dn">{n_alerts}</div>
<div class="hero-sub">≥75% conviction signals</div>
</div>
<div class="hero-cell">
<div class="lbl">Transactions</div>
<div class="hero-value tab">{n_transactions}</div>
<div class="hero-sub">CSV imports + manual</div>
</div>
<div class="hero-cell">
<div class="lbl">Pipeline Runs</div>
<div class="hero-value tab">{n_pipelines}</div>
<div class="hero-sub">Daily AI runs</div>
</div>
</div>
</section>
"""), unsafe_allow_html=True)

# ─── Timeline ──────────────────────────────────────────────────────────────
st.markdown('<div class="below-section">', unsafe_allow_html=True)

if not events:
    st.info("Nothing in the inbox yet.")
else:
    st.markdown("""
    <div class="sect-head">
      <div>
        <h2>Recent Activity</h2>
        <div class="sect-sub">Last 50 events across portfolio, recommendations, settings, and pipeline runs</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    rows_html = ""
    for ts, icon, title, body, kind in events[:50]:
        when = _iso(ts)
        kind_color = {
            "alert":      "var(--dn)",
            "transaction": "var(--up)",
            "pipeline":   "var(--text-dim)",
            "recs":       "#1E40AF",
            "portfolio":  "var(--text)",
            "settings":   "#92400E",
        }.get(kind, "var(--text-dim)")
        rows_html += (
            f'<div style="border:1px solid var(--hair-soft);border-left:3px solid {kind_color};'
            f'background:white;padding:14px 18px;margin-bottom:6px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">'
            f'<div style="flex:1;">'
            f'<div style="font-size:13px;font-weight:600;color:var(--text);">{icon} {title}</div>'
            f'<div style="font-size:12px;color:var(--text-dim);margin-top:4px;line-height:1.6;">{body}</div>'
            f'</div>'
            f'<div class="mono" style="font-size:11px;color:var(--text-mute);white-space:nowrap;">{when}</div>'
            f'</div>'
            f'</div>'
        )
    st.markdown(rows_html, unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# Footer
st.markdown("""
<footer class="page-footer">
  <div>AMIT CAPITAL · Inbox · Sources: portfolio.json, recommendations.json, logs/*.log</div>
  <div class="right">Newest first · Auto-updates on page reload</div>
</footer>
""", unsafe_allow_html=True)
