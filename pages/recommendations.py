"""
Recommendations — Institutional focused view.

Design principle: the eye goes straight to what needs action.
  1. Tiny one-line hero (stats inline, no big cards)
  2. Two-column action board:
       Left  = REDUCE / SELL  (red accent, short list)
       Right = BUY / ADD      (green accent, includes holdings BUY + new ideas)
  3. Collapsed "All holdings reviewed" table for the rest (quiet)

No Daily Summary, no persona consensus cards, no per-holding rationale shown
by default. Reasoning lives behind an expander per row.
"""

from _bootstrap import inject_css, inject_header, handle_actions, load_json, minify

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

# ─── Parse ─────────────────────────────────────────────────────────────────
updated = recs.get("updated", "—")
profile = recs.get("profile_name", "—")
holdings = recs.get("holdings", [])
new_ideas = recs.get("new_ideas", [])
is_dry_run = bool(recs.get("dry_run", False))

n_buy = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "buy")
n_hold = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "hold")
n_sell = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "sell")
updated_short = updated[:16].replace("T", " ") if updated else "—"


# ─── Tiny hero strip — one line, no big KPI cards ─────────────────────────
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
  <div class="recs-hero-right mono">Generated {updated_short}</div>
</section>
"""), unsafe_allow_html=True)


# ─── Dry-run notice ───────────────────────────────────────────────────────
if is_dry_run:
    st.markdown(
        '<div class="recs-dry-note">'
        'Dry-run data — rationales are local placeholders, not live Gemini output. '
        'Click <b>Run analysis →</b> for real output.'
        '</div>',
        unsafe_allow_html=True,
    )


# ─── Conviction filter — single right-aligned control ─────────────────────
fc1, fc2 = st.columns([1, 3], gap="small")
with fc1:
    min_conv = st.slider("Minimum conviction", min_value=0, max_value=100,
                         value=60, step=5, label_visibility="collapsed")
with fc2:
    st.markdown(
        f'<div class="recs-filter-caption">Showing items with conviction ≥ '
        f'<b>{min_conv}%</b></div>',
        unsafe_allow_html=True,
    )


# ─── Helpers ──────────────────────────────────────────────────────────────
VERDICT_CLS = {"buy": "pill-buy", "sell": "pill-sell", "hold": "pill-hold"}

PERSONA_COLORS = {
    "warren_buffett": "#1E3A8A", "charlie_munger": "#475569",
    "cathie_wood": "#BE185D", "peter_lynch": "#EA580C",
    "michael_burry": "#991B1B", "ben_graham": "#0369A1",
    "technical_analyst": "#0891B2", "fundamentals_analyst": "#15803D",
    "risk_manager": "#6D28D9", "valuation": "#4338CA",
    "sentiment": "#B45309", "macro": "#1F2937",
}


def vote_dots_html(personas):
    nb = sum(1 for p in personas if (p.get("verdict") or "").lower() == "buy")
    nh = sum(1 for p in personas if (p.get("verdict") or "").lower() == "hold")
    ns = sum(1 for p in personas if (p.get("verdict") or "").lower() == "sell")
    dots = (
        '<span class="pos-vote-dot buy"></span>' * nb +
        '<span class="pos-vote-dot hold"></span>' * nh +
        '<span class="pos-vote-dot sell"></span>' * ns
    )
    return (
        f'<div class="recs-votes">'
        f'<div class="pos-votes">{dots}</div>'
        f'<div class="recs-votes-count mono">'
        f'{nb}<span class="txt-mute">/</span>'
        f'{nh}<span class="txt-mute">/</span>'
        f'{ns}'
        f'</div>'
        f'</div>'
    )


def persona_block_html(p):
    pkey = p.get("name", "")
    pname = p.get("display_name") or pkey
    pv = (p.get("verdict") or "hold").lower()
    pc = int(p.get("conviction", 0))
    pcol = PERSONA_COLORS.get(pkey, "#6B7280")
    cls = VERDICT_CLS.get(pv, "pill-hold")
    rationale = p.get("rationale", "")
    is_hebrew = any('\u0590' <= c <= '\u05FF' for c in rationale[:80])
    rtl = ' dir="rtl"' if is_hebrew else ''
    return (
        f'<div class="recs-persona" style="border-left-color:{pcol};">'
        f'<div class="recs-persona-head">'
        f'<div class="recs-persona-name" style="color:{pcol};">{pname}</div>'
        f'<span class="pill {cls}" style="font-size:10px;">{pv.upper()} {pc}</span>'
        f'</div>'
        f'<div{rtl} class="recs-persona-body">{rationale}</div>'
        f'</div>'
    )


def render_holding_card(h: dict, accent: str):
    tk = h.get("ticker", "")
    name = DISPLAY_NAMES.get(tk, tk)
    v = (h.get("verdict") or "hold").lower()
    c = int(h.get("conviction", 0))
    personas = h.get("personas", [])
    st.markdown(
        f'<div class="recs-card recs-card-{accent}">'
        f'<div class="recs-card-top">'
        f'<div>'
        f'<div class="recs-card-ticker mono">{tk}</div>'
        f'<div class="recs-card-name">{name}</div>'
        f'</div>'
        f'<span class="pill {VERDICT_CLS.get(v, "pill-hold")}">{v.upper()} {c}</span>'
        f'</div>'
        f'{vote_dots_html(personas)}'
        f'</div>',
        unsafe_allow_html=True,
    )
    if personas:
        with st.expander(f"Why — {len(personas)} analysts", expanded=False):
            pairs = [personas[i:i+2] for i in range(0, len(personas), 2)]
            for batch in pairs:
                st.markdown(
                    '<div class="recs-persona-grid">'
                    + "".join(persona_block_html(p) for p in batch)
                    + '</div>',
                    unsafe_allow_html=True,
                )


def render_new_idea_card(idea: dict):
    tk = idea.get("ticker", "")
    name = idea.get("name", tk)
    conv = int(idea.get("conviction", 0))
    rationale = idea.get("rationale", "")
    is_hebrew = any('\u0590' <= c <= '\u05FF' for c in rationale[:80])
    rtl = ' dir="rtl"' if is_hebrew else ''
    st.markdown(
        f'<div class="recs-card recs-card-buy recs-card-idea">'
        f'<div class="recs-card-top">'
        f'<div>'
        f'<div class="recs-card-ticker mono">{tk}</div>'
        f'<div class="recs-card-name">{name}</div>'
        f'</div>'
        f'<span class="pill pill-new">NEW {conv}</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if rationale:
        with st.expander("Why", expanded=False):
            st.markdown(
                f'<div{rtl} class="recs-idea-body">{rationale}</div>',
                unsafe_allow_html=True,
            )


# ─── Action board ─────────────────────────────────────────────────────────
sells = sorted(
    [h for h in holdings
     if (h.get("verdict") or "").lower() == "sell"
     and int(h.get("conviction", 0)) >= min_conv],
    key=lambda h: -int(h.get("conviction", 0)),
)

holding_buys = sorted(
    [h for h in holdings
     if (h.get("verdict") or "").lower() == "buy"
     and int(h.get("conviction", 0)) >= min_conv],
    key=lambda h: -int(h.get("conviction", 0)),
)

filtered_ideas = sorted(
    [i for i in new_ideas if int(i.get("conviction", 0)) >= min_conv],
    key=lambda x: -int(x.get("conviction", 0)),
)

st.markdown(
    '<div class="recs-board-head">'
    '<div>'
    '<h2>Action Board</h2>'
    f'<div class="sect-sub">What to do next · filtered by ≥ {min_conv}% conviction</div>'
    '</div>'
    '</div>',
    unsafe_allow_html=True,
)

col_sell, col_buy = st.columns(2, gap="medium")

with col_sell:
    st.markdown(
        f'<div class="recs-col-head recs-col-head-sell">'
        f'<div class="recs-col-title">Reduce / Sell</div>'
        f'<div class="recs-col-count">{len(sells)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if not sells:
        st.markdown(
            '<div class="recs-empty">Nothing to trim at this threshold. '
            'Your positions look balanced.</div>',
            unsafe_allow_html=True,
        )
    else:
        for h in sells:
            render_holding_card(h, "sell")

with col_buy:
    total_buy_side = len(holding_buys) + len(filtered_ideas[:6])
    st.markdown(
        f'<div class="recs-col-head recs-col-head-buy">'
        f'<div class="recs-col-title">Buy / Add</div>'
        f'<div class="recs-col-count">{total_buy_side}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if not holding_buys and not filtered_ideas:
        st.markdown(
            '<div class="recs-empty">No strong BUY signals at this threshold.</div>',
            unsafe_allow_html=True,
        )
    for h in holding_buys:
        render_holding_card(h, "buy")
    if filtered_ideas:
        st.markdown(
            '<div class="recs-subhead">New Ideas</div>',
            unsafe_allow_html=True,
        )
        for idea in filtered_ideas[:6]:
            render_new_idea_card(idea)


# ─── Everything else — quiet list ─────────────────────────────────────────
others = sorted(
    [h for h in holdings
     if (h.get("verdict") or "").lower() not in ("buy", "sell")
     or int(h.get("conviction", 0)) < min_conv],
    key=lambda h: (-(int(h.get("conviction", 0))), h.get("ticker", "")),
)

with st.expander(f"All {len(holdings)} holdings reviewed — show full list", expanded=False):
    rows_html = []
    for h in sorted(holdings, key=lambda x: x.get("ticker", "")):
        tk = h.get("ticker", "")
        name = DISPLAY_NAMES.get(tk, tk)
        v = (h.get("verdict") or "hold").lower()
        c = int(h.get("conviction", 0))
        personas = h.get("personas", [])
        nb = sum(1 for p in personas if (p.get("verdict") or "").lower() == "buy")
        nh = sum(1 for p in personas if (p.get("verdict") or "").lower() == "hold")
        ns = sum(1 for p in personas if (p.get("verdict") or "").lower() == "sell")
        rows_html.append(
            f'<tr>'
            f'<td class="recs-tbl-tkr mono">{tk}</td>'
            f'<td class="recs-tbl-name">{name}</td>'
            f'<td class="r"><span class="pill {VERDICT_CLS.get(v, "pill-hold")}" '
            f'style="font-size:10px;">{v.upper()}</span></td>'
            f'<td class="r mono">{c}</td>'
            f'<td class="r mono"><span class="up">{nb}</span>'
            f'<span class="txt-mute">/</span>'
            f'<span style="color:var(--hold);">{nh}</span>'
            f'<span class="txt-mute">/</span>'
            f'<span class="dn">{ns}</span></td>'
            f'</tr>'
        )
    st.markdown(
        '<table class="recs-table">'
        '<thead><tr>'
        '<th>Ticker</th><th>Name</th>'
        '<th class="r">Verdict</th>'
        '<th class="r">Conv.</th>'
        '<th class="r">B/H/S</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>',
        unsafe_allow_html=True,
    )


# ─── Footer ────────────────────────────────────────────────────────────────
st.markdown("""
<footer class="page-footer">
  <div>AMIT CAPITAL · Recommendations · Market commentary, not financial advice.</div>
  <div class="right">Powered by Gemini · 5 personas</div>
</footer>
""", unsafe_allow_html=True)
