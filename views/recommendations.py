"""
Recommendations — decision-first institutional view.

Design principles (research-backed, Bloomberg + Morningstar pattern):
  1. Answer the user's real question FIRST: "what should I do right now?"
     → Priority Actions strip at top: highest-conviction SELL + strong BUYS,
       each with a conviction bar and a single row — scannable in 2 seconds.
  2. Group the rest by sector so the user reasons at portfolio level.
  3. Rationale on demand (expanders).  No always-visible text walls.
  4. Tight information density — hairlines, mono numerals, color-coded pills,
     small typography. Match the Portfolio page's visual language 1:1.
  5. A compact "All holdings" table collapsed at the bottom for completeness.
"""

from _bootstrap import inject_css, inject_header, handle_actions, load_json, minify

import html as _html
from datetime import datetime, timezone
import streamlit as st
from config import DISPLAY_NAMES

inject_css()
inject_header("recommendations")
handle_actions()

recs = load_json("recommendations.json")
if not recs:
    st.markdown("""
    <div class="below-section">
      <div class="sect-head">
        <div>
          <h2>Recommendations</h2>
          <div class="sect-sub">No recommendations yet</div>
        </div>
      </div>
      <div style="border:1px solid var(--hair);padding:24px;background:var(--bg-softer);font-size:13px;">
        Click <b>Run analysis →</b> in the topbar to generate recommendations.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ─── Parse input ──────────────────────────────────────────────────────────
profile = recs.get("profile_name", "—")
holdings = recs.get("holdings", [])
new_ideas = recs.get("new_ideas", [])
updated_raw = recs.get("updated") or ""
updated = updated_raw[:16].replace("T", " ") if updated_raw else "—"
is_dry_run = bool(recs.get("dry_run", False))

# Freshness badge
_freshness_html = ""
if updated_raw:
    try:
        _ts = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        _age_h = (datetime.now(timezone.utc) - _ts).total_seconds() / 3600
        if _age_h < 1:
            _fl, _fc = "Just now", "fresh-green"
        elif _age_h < 12:
            _fl, _fc = f"{int(_age_h)}h ago", "fresh-green"
        elif _age_h < 48:
            _fl, _fc = f"{int(_age_h)}h ago", "fresh-yellow"
        else:
            _fl, _fc = f"{int(_age_h / 24)}d ago", "fresh-red"
        _freshness_html = f'<span class="recs-fresh recs-fresh-{_fc}">{_fl}</span>'
    except Exception:
        pass

n_buy = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "buy")
n_hold = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "hold")
n_sell = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "sell")
n_analysts = max((len(h.get("personas", [])) for h in holdings), default=0)


# Load sectors once (portfolio.json has .sector)
_portfolio = load_json("portfolio.json") or {}
_SECTORS = {h.get("ticker"): (h.get("sector") or "Other")
            for h in _portfolio.get("holdings", [])}

VERDICT_CLS = {"buy": "pill-buy", "sell": "pill-sell", "hold": "pill-hold"}
VERDICT_COLOR = {"buy": "#047857", "sell": "#B91C1C", "hold": "#92400E"}
PERSONA_COLORS = {
    "warren_buffett": "#1E3A8A", "charlie_munger": "#475569",
    "cathie_wood": "#BE185D", "peter_lynch": "#EA580C",
    "michael_burry": "#991B1B", "ben_graham": "#0369A1",
    "technical_analyst": "#0891B2", "fundamentals_analyst": "#15803D",
    "risk_manager": "#6D28D9", "valuation": "#4338CA",
    "sentiment": "#B45309", "macro": "#1F2937",
}


def _sector_of(ticker: str) -> str:
    return _SECTORS.get(ticker, "Other")


def _vote_breakdown(personas):
    nb = sum(1 for p in personas if (p.get("verdict") or "").lower() == "buy")
    nh = sum(1 for p in personas if (p.get("verdict") or "").lower() == "hold")
    ns = sum(1 for p in personas if (p.get("verdict") or "").lower() == "sell")
    return nb, nh, ns


def _vote_mono_html(nb, nh, ns):
    return (
        f'<span class="up mono">{nb}</span>'
        f'<span class="txt-mute mono">/</span>'
        f'<span class="mono" style="color:var(--hold);">{nh}</span>'
        f'<span class="txt-mute mono">/</span>'
        f'<span class="dn mono">{ns}</span>'
    )


def _conviction_bar(pct: int, verdict: str) -> str:
    color = VERDICT_COLOR.get(verdict, "#6B7280")
    pct = max(0, min(100, int(pct)))
    return (
        f'<div class="conv-bar" title="Conviction {pct}%">'
        f'<div class="conv-bar-fill" style="width:{pct}%;background:{color};"></div>'
        f'</div>'
    )


def persona_block_html(p):
    pkey = p.get("name", "")
    pname = _html.escape(p.get("display_name") or pkey)
    pv = (p.get("verdict") or "hold").lower()
    pc = int(p.get("conviction", 0))
    pcol = PERSONA_COLORS.get(pkey, "#6B7280")
    cls = VERDICT_CLS.get(pv, "pill-hold")
    rationale = _html.escape(p.get("rationale", ""))
    is_hebrew = any('\u0590' <= c <= '\u05FF' for c in (p.get("rationale") or "")[:80])
    dir_attr = ' dir="rtl"' if is_hebrew else ''
    return (
        f'<div class="recs-persona" style="border-left-color:{pcol};">'
        f'<div class="recs-persona-head">'
        f'<div class="recs-persona-name" style="color:{pcol};">{pname}</div>'
        f'<span class="pill {cls}" style="font-size:10px;">{pv.upper()} {pc}</span>'
        f'</div>'
        f'<div{dir_attr} class="recs-persona-body">{rationale}</div>'
        f'</div>'
    )


def render_action_row(h: dict, accent: str) -> None:
    """Compact single-row card for Priority Actions section."""
    tk = h.get("ticker", "")
    name = DISPLAY_NAMES.get(tk, tk)
    sector = _sector_of(tk)
    v = (h.get("verdict") or "hold").lower()
    c = int(h.get("conviction", 0))
    personas = h.get("personas", [])
    nb, nh, ns = _vote_breakdown(personas)
    total = max(1, nb + nh + ns)
    st.markdown(
        f'<div class="act-row act-row-{accent}">'
        f'  <div class="act-row-left">'
        f'    <span class="pill {VERDICT_CLS.get(v, "pill-hold")}">{v.upper()}</span>'
        f'    <div class="act-ticker-block">'
        f'      <div class="act-ticker mono">{tk}</div>'
        f'      <div class="act-name">{name}</div>'
        f'    </div>'
        f'  </div>'
        f'  <div class="act-row-mid">'
        f'    {_conviction_bar(c, v)}'
        f'    <div class="act-conv-pct mono">{c}<span class="txt-mute">%</span></div>'
        f'  </div>'
        f'  <div class="act-row-right">'
        f'    <div class="act-votes mono">{_vote_mono_html(nb, nh, ns)} <span class="txt-mute">of {total}</span></div>'
        f'    <div class="act-sector txt-dim">{sector}</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if personas:
        with st.expander(f"Why {tk}? — {len(personas)} analysts", expanded=False):
            st.markdown(
                '<div class="recs-persona-grid">'
                + "".join(persona_block_html(p) for p in personas)
                + '</div>',
                unsafe_allow_html=True,
            )


def render_mini_card(h: dict, accent: str) -> None:
    """Tight 3-column-grid card for sector groupings."""
    tk = h.get("ticker", "")
    name = DISPLAY_NAMES.get(tk, tk)
    v = (h.get("verdict") or "hold").lower()
    c = int(h.get("conviction", 0))
    personas = h.get("personas", [])
    nb, nh, ns = _vote_breakdown(personas)
    st.markdown(
        f'<div class="mini-card mini-card-{accent}">'
        f'  <div class="mini-card-top">'
        f'    <div class="mini-ticker mono">{tk}</div>'
        f'    <span class="pill {VERDICT_CLS.get(v, "pill-hold")}">{v.upper()} {c}</span>'
        f'  </div>'
        f'  <div class="mini-name txt-dim">{name}</div>'
        f'  {_conviction_bar(c, v)}'
        f'  <div class="mini-votes mono txt-dim">{_vote_mono_html(nb, nh, ns)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if personas:
        with st.expander(f"Why {tk}?", expanded=False):
            st.markdown(
                '<div class="recs-persona-grid">'
                + "".join(persona_block_html(p) for p in personas)
                + '</div>',
                unsafe_allow_html=True,
            )


def render_new_idea_card(idea: dict):
    tk = idea.get("ticker", "")
    name = _html.escape(idea.get("name", tk))
    conv = int(idea.get("conviction", 0))
    rationale_raw = idea.get("rationale", "")
    rationale = _html.escape(rationale_raw)
    is_hebrew = any('\u0590' <= c <= '\u05FF' for c in rationale_raw[:80])
    rtl = ' dir="rtl"' if is_hebrew else ''
    st.markdown(
        f'<div class="mini-card mini-card-idea">'
        f'  <div class="mini-card-top">'
        f'    <div class="mini-ticker mono">{tk}</div>'
        f'    <span class="pill pill-new">NEW {conv}</span>'
        f'  </div>'
        f'  <div class="mini-name txt-dim">{name}</div>'
        f'  {_conviction_bar(conv, "buy")}'
        f'</div>',
        unsafe_allow_html=True,
    )
    if rationale:
        with st.expander(f"Why {tk}?", expanded=False):
            st.markdown(
                f'<div{rtl} class="recs-idea-body">{rationale}</div>',
                unsafe_allow_html=True,
            )


# ═══════════════════════════════════════════════════════════════════════════
# Hero strip — one line, dense, scannable
# ═══════════════════════════════════════════════════════════════════════════
st.markdown(minify(f"""
<section class="recs-hero">
  <div class="recs-hero-left">
    <div class="lbl">Recommendations — {profile}</div>
    <div class="recs-hero-stats mono">
      <span class="up tab">{n_buy}</span><span class="recs-sep">buy</span>
      <span class="recs-dot">·</span>
      <span class="tab" style="color:var(--hold);">{n_hold}</span><span class="recs-sep">hold</span>
      <span class="recs-dot">·</span>
      <span class="dn tab">{n_sell}</span><span class="recs-sep">sell</span>
      <span class="recs-dot">·</span>
      <span class="tab">{len(new_ideas)}</span><span class="recs-sep">new ideas</span>
    </div>
  </div>
  <div class="recs-hero-right mono">
    {n_analysts} analysts per stock · Generated {updated} {_freshness_html}
  </div>
</section>
"""), unsafe_allow_html=True)

if is_dry_run:
    st.markdown(
        '<div class="recs-dry-note">'
        'Dry-run data — rationales are local placeholders, not live Gemini output. '
        'Click <b>Run analysis →</b> for real output.'
        '</div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Filter
# ═══════════════════════════════════════════════════════════════════════════
fc1, fc2 = st.columns([2, 5], gap="small")
with fc1:
    min_conv = st.slider("Minimum conviction", min_value=0, max_value=100,
                         value=60, step=5, label_visibility="collapsed")
with fc2:
    st.markdown(
        f'<div class="recs-filter-caption">Filter · showing items with conviction ≥ <b>{min_conv}%</b></div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. PRIORITY ACTIONS
# ═══════════════════════════════════════════════════════════════════════════
sells = sorted(
    [h for h in holdings
     if (h.get("verdict") or "").lower() == "sell"
     and int(h.get("conviction", 0)) >= min_conv],
    key=lambda h: -int(h.get("conviction", 0)),
)
strong_buys = sorted(
    [h for h in holdings
     if (h.get("verdict") or "").lower() == "buy"
     and int(h.get("conviction", 0)) >= max(min_conv, 75)],
    key=lambda h: -int(h.get("conviction", 0)),
)
priority_count = len(sells) + min(len(strong_buys), 3)

st.markdown(
    f'<div class="below-section">'
    f'<div class="sect-head">'
    f'<div>'
    f'<h2>Priority Actions</h2>'
    f'<div class="sect-sub">Sells first, then strongest buys (≥ 75%)</div>'
    f'</div>'
    f'<div class="sect-side">{priority_count} actionable</div>'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)

if priority_count == 0:
    st.markdown(
        '<div class="recs-empty-lg">'
        '<div class="recs-empty-title">✓ No urgent actions</div>'
        '<div class="recs-empty-sub">Your positions are balanced at this threshold. '
        'Lower the slider or review the Buy Signals below.</div>'
        '</div>',
        unsafe_allow_html=True,
    )
else:
    for h in sells:
        render_action_row(h, "sell")
    for h in strong_buys[:3]:
        render_action_row(h, "buy")


# ═══════════════════════════════════════════════════════════════════════════
# 2. BUY SIGNALS BY SECTOR
# ═══════════════════════════════════════════════════════════════════════════
top_3_buy_tickers = {h.get("ticker") for h in strong_buys[:3]}
remaining_buys = [h for h in holdings
                  if (h.get("verdict") or "").lower() == "buy"
                  and int(h.get("conviction", 0)) >= min_conv
                  and h.get("ticker") not in top_3_buy_tickers]

by_sector = {}
for h in remaining_buys:
    sec = _sector_of(h.get("ticker", ""))
    by_sector.setdefault(sec, []).append(h)

if by_sector:
    st.markdown(
        f'<div class="below-section">'
        f'<div class="sect-head">'
        f'<div>'
        f'<h2>Buy Signals by Sector</h2>'
        f'<div class="sect-sub">Grouped for portfolio-level reasoning</div>'
        f'</div>'
        f'<div class="sect-side">{len(remaining_buys)} holdings</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    for sec in sorted(by_sector.keys(), key=lambda s: -len(by_sector[s])):
        stocks = sorted(by_sector[sec], key=lambda h: -int(h.get("conviction", 0)))
        st.markdown(
            f'<div class="sector-head">'
            f'<span class="sector-name">{sec}</span>'
            f'<span class="sector-count mono txt-dim">{len(stocks)}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        rows = [stocks[i:i + 3] for i in range(0, len(stocks), 3)]
        for row in rows:
            cols = st.columns(3, gap="small")
            for ci, h in enumerate(row):
                with cols[ci]:
                    render_mini_card(h, "buy")


# ═══════════════════════════════════════════════════════════════════════════
# 3. NEW IDEAS
# ═══════════════════════════════════════════════════════════════════════════
filtered_ideas = sorted(
    [i for i in new_ideas if int(i.get("conviction", 0)) >= min_conv],
    key=lambda x: -int(x.get("conviction", 0)),
)
if filtered_ideas:
    st.markdown(
        f'<div class="below-section">'
        f'<div class="sect-head">'
        f'<div>'
        f'<h2>New Ideas</h2>'
        f'<div class="sect-sub">Outside your portfolio · fits your profile</div>'
        f'</div>'
        f'<div class="sect-side">{len(filtered_ideas)}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    top_ideas = filtered_ideas[:6]
    rows = [top_ideas[i:i + 3] for i in range(0, len(top_ideas), 3)]
    for row in rows:
        cols = st.columns(3, gap="small")
        for ci, idea in enumerate(row):
            with cols[ci]:
                render_new_idea_card(idea)


# ═══════════════════════════════════════════════════════════════════════════
# 4. ALL HOLDINGS — compact table
# ═══════════════════════════════════════════════════════════════════════════
with st.expander(f"All {len(holdings)} holdings — full review table", expanded=False):
    rows_html = []
    for h in sorted(holdings, key=lambda x: -int(x.get("conviction", 0))):
        tk = h.get("ticker", "")
        name = DISPLAY_NAMES.get(tk, tk)
        sec = _sector_of(tk)
        v = (h.get("verdict") or "hold").lower()
        c = int(h.get("conviction", 0))
        personas = h.get("personas", [])
        nb, nh, ns = _vote_breakdown(personas)
        rows_html.append(
            f'<tr>'
            f'<td class="recs-tbl-tkr mono">{tk}</td>'
            f'<td class="recs-tbl-name">{name}</td>'
            f'<td class="txt-dim" style="font-size:11px;">{sec}</td>'
            f'<td class="r"><span class="pill {VERDICT_CLS.get(v, "pill-hold")}" '
            f'style="font-size:10px;">{v.upper()}</span></td>'
            f'<td class="r mono" style="font-weight:500;">{c}%</td>'
            f'<td class="r mono">{_vote_mono_html(nb, nh, ns)}</td>'
            f'</tr>'
        )
    st.markdown(
        '<table class="recs-table">'
        '<thead><tr>'
        '<th>Ticker</th><th>Name</th><th>Sector</th>'
        '<th class="r">Verdict</th>'
        '<th class="r">Conv.</th>'
        '<th class="r">B/H/S</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>',
        unsafe_allow_html=True,
    )


st.markdown("""
<footer class="page-footer">
  <div>AMIT CAPITAL · Recommendations · Market commentary, not financial advice.</div>
  <div class="right">Powered by Gemini · multi-persona analyst consensus</div>
</footer>
""", unsafe_allow_html=True)
