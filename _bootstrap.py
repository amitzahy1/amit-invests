"""
Shared bootstrap for pages — sys.path, CSS, custom topbar (1:1 demo), action router.
"""

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402


_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<defs>'
    '<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#1e293b"/>'
    '<stop offset="100%" stop-color="#0f172a"/>'
    '</linearGradient>'
    '</defs>'
    '<rect width="32" height="32" rx="6" fill="url(#g)"/>'
    '<path d="M8 22 L16 8 L24 22" stroke="#22d3ee" stroke-width="2.5" '
    'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    '<line x1="11" y1="17" x2="21" y2="17" stroke="#22d3ee" '
    'stroke-width="1.5" stroke-linecap="round" opacity="0.6"/>'
    '</svg>'
)


def inject_css() -> None:
    import base64
    css_path = ROOT / "style.css"
    fav_b64 = base64.b64encode(_FAVICON_SVG.encode()).decode()
    favicon_tag = (
        f'<link rel="icon" type="image/svg+xml" '
        f'href="data:image/svg+xml;base64,{fav_b64}">'
    )
    if css_path.exists():
        st.markdown(
            f"<style>{css_path.read_text()}</style>{favicon_tag}",
            unsafe_allow_html=True,
        )


def minify(html: str) -> str:
    """Strip leading whitespace and blank lines so st.markdown doesn't treat HTML as code blocks."""
    return "".join(line.lstrip() for line in html.splitlines() if line.strip())


def load_json(name: str) -> dict:
    import json
    p = ROOT / name
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


# Page registry — slug, label, target path
NAV_PAGES = [
    ("portfolio",        "Portfolio",       "views/portfolio.py"),
    ("recommendations",  "Recommendations", "views/recommendations.py"),
    ("import_csv",       "Import",          "views/import_csv.py"),
    ("inbox",            "Inbox",           "views/inbox.py"),
    ("settings",         "Settings",        "views/settings.py"),
    ("explainer",        "How It Works",    "views/explainer.py"),
]


def handle_actions() -> None:
    """
    Process ?action=... URL params at the top of every page.
    Handles 'refresh' (cache clear) and 'run_ai' (live-polled Gemini run).
    """
    qp = st.query_params
    action = qp.get("action")
    if not action:
        return

    if action == "refresh":
        st.cache_data.clear()
        st.query_params.clear()
        st.rerun()
        return

    if action == "run_ai":
        _run_ai_modal()
        st.stop()


# Phases definition (shared)
_AI_PHASES = [
    ("init",   "Init",        "Load API key & config"),
    ("load",   "Load data",   "Portfolio & market data"),
    ("gemini", "Call Gemini", "Multi-persona analysis"),
    ("write",  "Finalize",    "Aggregate & write JSON"),
]


def _ai_header_html(state: str, title: str, subtitle: str) -> str:
    icon = {"running": "AI", "success": "✓", "error": "!"}[state]
    return (
        f'<div class="ai-dialog ai-dialog-{state}">'
        f'  <div class="ai-dialog-icon">{icon}</div>'
        f'  <div class="ai-dialog-body">'
        f'    <div class="ai-dialog-title">{title}</div>'
        f'    <div class="ai-dialog-sub">{subtitle}</div>'
        f'  </div>'
        f'</div>'
    )


def _ai_stepper_html(current_key: str, done_keys: set, error: bool = False) -> str:
    items = []
    for idx, (key, label, desc) in enumerate(_AI_PHASES, start=1):
        if key in done_keys:
            cls, glyph = "done", "✓"
        elif key == current_key and not error:
            cls, glyph = "active", str(idx)
        elif key == current_key and error:
            cls, glyph = "error", "!"
        else:
            cls, glyph = "pending", str(idx)
        items.append(
            f'<div class="ai-step ai-step-{cls}">'
            f'  <div class="ai-step-glyph">{glyph}</div>'
            f'  <div class="ai-step-label">{label}</div>'
            f'  <div class="ai-step-desc">{desc}</div>'
            f'</div>'
        )
    return f'<div class="ai-stepper">{"".join(items)}</div>'


def _run_ai_modal() -> None:
    """True modal dialog (st.dialog) with stepper + progress. No ticker log leaks."""
    # st.dialog is available in Streamlit ≥ 1.31. Fallback: inline rendering.
    dialog_decorator = getattr(st, "dialog", None)
    if dialog_decorator is None:
        _ai_runner_body()
        return

    @dialog_decorator("AI Analysis", width="large")  # type: ignore[misc]
    def _inner():
        _ai_runner_body()

    _inner()


def _ai_runner_body() -> None:
    """All rendering / polling — used inside the modal OR as fallback."""
    import time as _time
    log_file = ROOT / "logs" / "last_run.log"
    log_file.parent.mkdir(exist_ok=True)
    log_file.write_text("")

    header_slot = st.empty()
    stepper_slot = st.empty()
    progress_slot = st.empty()
    meta_slot = st.empty()
    action_slot = st.empty()

    # Pull persona count from settings so the subtitle matches reality
    try:
        _settings = load_json("settings.json") or {}
        _n_personas = max(2, len(_settings.get("personas_active") or []))  # script forces ≥2
    except Exception:
        _n_personas = 5
    _eta_min = max(2, int(round(15 * _n_personas * 4 / 60)))  # rough: 4s/call avg
    header_slot.markdown(
        _ai_header_html("running", "Running AI Analysis",
                        f"Calling Gemini for each holding across {_n_personas} personas. "
                        f"Expect roughly {_eta_min}–{_eta_min*2} minutes."),
        unsafe_allow_html=True,
    )
    stepper_slot.markdown(_ai_stepper_html("init", set()), unsafe_allow_html=True)

    def render_progress(pct: float, primary: str, secondary: str, state: str = "running"):
        pct_i = max(0, min(100, int(pct * 100)))
        progress_slot.markdown(
            f'<div class="ai-progress ai-progress-{state}">'
            f'  <div class="ai-progress-top">'
            f'    <div class="ai-progress-label">{primary}</div>'
            f'    <div class="ai-progress-pct">{pct_i}%</div>'
            f'  </div>'
            f'  <div class="ai-progress-track">'
            f'    <div class="ai-progress-fill" style="width:{pct_i}%;"></div>'
            f'  </div>'
            f'  <div class="ai-progress-sub">{secondary}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    render_progress(0, "Starting", "Initializing subprocess", "running")

    try:
        proc = subprocess.Popen(
            [sys.executable, "-u",
             str(ROOT / "scripts" / "run_recommendations.py"), "--once"],
            stdout=open(log_file, "w"),
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            text=True,
        )
    except Exception as e:
        header_slot.markdown(
            _ai_header_html("error", "Failed to start", str(e)),
            unsafe_allow_html=True,
        )
        stepper_slot.markdown(_ai_stepper_html("init", set(), error=True),
                              unsafe_allow_html=True)
        if action_slot.button("Close", type="secondary"):
            st.query_params.clear()
            st.rerun()
        return

    TOTAL_GUESS = 16
    HARD_TIMEOUT = 2700   # 45 minutes — matches GitHub Actions timeout
    start_ts = _time.time()
    last_phase = "init"
    timed_out = False

    while proc.poll() is None:
        elapsed = _time.time() - start_ts
        if elapsed > HARD_TIMEOUT:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            timed_out = True
            header_slot.markdown(
                _ai_header_html("error", "Timed out (45 min)",
                                "Subprocess was killed. Try running from the terminal: "
                                "`python scripts/run_recommendations.py --once`"),
                unsafe_allow_html=True,
            )
            break

        try:
            content = log_file.read_text()
        except Exception:
            content = ""
        lines = content.splitlines()

        # Count completed tickers (lines like "[5/15] XYZ: VERDICT NN%")
        ticker_lines = [l for l in lines if "/" in l and ":" in l and "%" in l
                        and l.strip().startswith("[")]
        done = len(ticker_lines)
        n_total = TOTAL_GUESS
        for l in lines:
            if "calling Gemini" in l and "holdings ×" in l:
                try:
                    n_total = int(l.split("(")[1].split(" holdings")[0])
                except Exception:
                    pass

        # Phase detection (no ticker content shown to user)
        if "calling Gemini" in content or ticker_lines:
            phase, done_phases = "gemini", {"init", "load"}
        elif any(k in content.lower() for k in
                 ("fetching", "yfinance", "loading portfolio", "market data")):
            phase, done_phases = "load", {"init"}
        else:
            phase, done_phases = "init", set()

        if phase != last_phase:
            stepper_slot.markdown(_ai_stepper_html(phase, done_phases),
                                  unsafe_allow_html=True)
            last_phase = phase

        if phase == "gemini":
            pct = min(0.95, done / max(1, n_total))
            eta_txt = ""
            if done > 0:
                per = elapsed / max(1, done)
                remaining = int(per * max(0, n_total - done))
                eta_txt = f" · ETA ~{remaining}s"
            primary = f"Analyzing holding {min(done + 1, n_total)} of {n_total}"
            secondary = f"{done} complete · {n_total - done} remaining{eta_txt}"
        elif phase == "load":
            pct = 0.15
            primary = "Fetching market data"
            secondary = "Pulling prices & historical data from Yahoo Finance"
        else:
            pct = 0.05
            primary = "Initializing"
            secondary = "Loading API key & portfolio"

        render_progress(pct, primary, secondary, "running")

        pct_display = int(pct * 100)
        meta_slot.markdown(
            f'<div class="ai-meta">'
            f'<span>Elapsed<b>{elapsed:.0f}s</b></span>'
            f'<span>Progress<b>{pct_display}%</b></span>'
            f'<span>Phase<b>{phase.capitalize()}</b></span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        _time.sleep(1.5)

    # Ensure we have a real returncode (kill() alone leaves it None)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    rc = proc.returncode

    if rc == 0 and not timed_out:
        header_slot.markdown(
            _ai_header_html("success", "Analysis complete",
                            "Recommendations updated. Refreshing your dashboard…"),
            unsafe_allow_html=True,
        )
        stepper_slot.markdown(
            _ai_stepper_html("write", {"init", "load", "gemini", "write"}),
            unsafe_allow_html=True,
        )
        render_progress(1.0, "Done", "Wrote recommendations.json", "success")
        st.cache_data.clear()
        st.query_params.clear()
        import time as _time
        _time.sleep(1.2)
        st.rerun()
    else:
        # Surface useful error excerpt from the log (avoid "exit None")
        try:
            _tail = "\n".join(log_file.read_text().splitlines()[-10:])
        except Exception:
            _tail = ""
        _rc_label = "killed" if timed_out or rc is None else str(rc)
        _subtitle = (
            "The run didn't complete. Tail of log below. "
            f"Check `logs/last_run.log` for the full trace."
        )
        header_slot.markdown(
            _ai_header_html("error", f"Run failed (exit {_rc_label})", _subtitle),
            unsafe_allow_html=True,
        )
        stepper_slot.markdown(_ai_stepper_html(last_phase, set(), error=True),
                              unsafe_allow_html=True)
        progress_slot.empty()
        if _tail:
            action_slot.code(_tail, language="text")
        if action_slot.button("Close", type="secondary"):
            st.query_params.clear()
            st.rerun()


@st.cache_data(ttl=300)
def _cached_usd_ils() -> float:
    try:
        from data_loader import fetch_usd_ils_rate
        return fetch_usd_ils_rate() or 3.67
    except Exception:
        return 3.67


def inject_header(current: str = "") -> None:
    """
    Render the demo's topbar 1:1: brand + nav + meta + Refresh + Run analysis.
    Buttons are <a href="?action=..."> handled by handle_actions().
    """
    portfolio = load_json("portfolio.json")
    holdings_count = len(portfolio.get("holdings", []))
    last_updated = portfolio.get("last_updated") or "—"
    usd_ils = _cached_usd_ils()

    nav_html = "".join(
        f'<a href="{"/" if slug == "portfolio" else "/" + slug}" target="_self" '
        f'class="nav-link {"active" if slug == current else ""}">{label}</a>'
        for slug, label, _ in NAV_PAGES
    )

    topbar_html = minify(f"""
<div class="topbar-wrap">
<div class="topbar">
<div class="topbar-left">
<div class="brand">AMIT <span class="brand-sub">CAPITAL</span></div>
<nav class="topbar-nav">{nav_html}</nav>
</div>
<div class="topbar-right">
<div class="topbar-meta">USD/ILS {usd_ils:.2f} · {holdings_count} holdings · Updated {last_updated}</div>
<a href="?action=refresh" target="_self" class="btn btn-secondary">Refresh</a>
<a href="?action=run_ai" target="_self" class="btn btn-primary" title="Run the multi-persona Gemini analysis on your portfolio"><span class="btn-label">Run analysis</span><span class="btn-arrow">→</span></a>
</div>
</div>
</div>
""")
    st.markdown(topbar_html, unsafe_allow_html=True)
