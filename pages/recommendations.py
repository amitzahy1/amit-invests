"""
Recommendations — Institutional design (matches Portfolio's visual language).
Hero + stats KPI strip + Daily Summary card + New Ideas grid + Action Items grid.
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, load_json, minify

from datetime import datetime
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
        Click <b>Run analysis →</b> in the topbar to generate recommendations, or run
        <code style="background:rgba(0,0,0,0.05);padding:1px 5px;font-family:'IBM Plex Mono';">python scripts/run_recommendations.py --once</code> from the terminal.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

updated = recs.get("updated", "—")
profile = recs.get("profile_name", "—")
summary = recs.get("summary", "")
holdings = recs.get("holdings", [])
new_ideas = recs.get("new_ideas", [])
is_dry_run = bool(recs.get("dry_run", False))

# ─── Hero strip — same shape as Portfolio (5 KPI cells) ────────────────────
total_buy = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "buy")
total_hold = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "hold")
total_sell = sum(1 for h in holdings if (h.get("verdict") or "").lower() == "sell")
strong_buys = [h.get("ticker") for h in holdings
               if (h.get("verdict") or "").lower() == "buy" and int(h.get("conviction", 0)) >= 75]
strong_sells = [h.get("ticker") for h in holdings
                if (h.get("verdict") or "").lower() == "sell" and int(h.get("conviction", 0)) >= 60]

today_str = datetime.now().strftime("%b %d, %Y")
updated_short = updated[:16].replace("T", " ") if updated else "—"

st.markdown(minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Recommendations — {profile}</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">Generated {updated_short}</div>
</div>
<div class="hero-grid">
<div class="hero-cell">
<div class="lbl">Strong Buys</div>
<div class="hero-value tab up">{len(strong_buys)}</div>
<div class="hero-sub mono" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{', '.join(strong_buys[:5]) if strong_buys else '—'}</div>
</div>
<div class="hero-cell">
<div class="lbl">Strong Sells</div>
<div class="hero-value tab dn">{len(strong_sells)}</div>
<div class="hero-sub mono" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{', '.join(strong_sells[:5]) if strong_sells else '—'}</div>
</div>
<div class="hero-cell">
<div class="lbl">Distribution</div>
<div class="hero-value tab" style="font-size:28px;"><span class="up">{total_buy}</span><span style="color:var(--text-mute);"> / </span><span style="color:var(--hold);">{total_hold}</span><span style="color:var(--text-mute);"> / </span><span class="dn">{total_sell}</span></div>
<div class="hero-sub">Buy / Hold / Sell · {len(holdings)} reviewed</div>
</div>
<div class="hero-cell">
<div class="lbl">New Ideas</div>
<div class="hero-value tab">{len(new_ideas)}</div>
<div class="hero-sub mono" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{', '.join(i.get('ticker','') for i in new_ideas[:3]) if new_ideas else '—'}</div>
</div>
<div class="hero-cell">
<div class="lbl">Engine</div>
<div class="hero-value hero-value-light" style="font-size:22px;">Gemini</div>
<div class="hero-sub mono">{'flash-latest' if not is_dry_run else 'dry-run mock'}</div>
</div>
</div>
</section>
"""), unsafe_allow_html=True)

# ─── Dry-run banner ─────────────────────────────────────────────────────────
if is_dry_run:
    st.markdown(
        '<div class="alert-banner"><div class="alert-banner-inner" '
        'style="background:var(--hold-bg);border-color:var(--hold-border);'
        'border-left-color:var(--hold);color:var(--hold);">'
        'Dry-run data — rationales generated locally, not live LLM output. '
        'Click <b>Run analysis →</b> in the topbar for real Gemini output.'
        '</div></div>',
        unsafe_allow_html=True,
    )


# ─── Controls + Daily Summary ───────────────────────────────────────────────
st.markdown('<div class="below-section">', unsafe_allow_html=True)

min_conv = st.slider("Minimum conviction %", min_value=0, max_value=100,
                     value=60, step=5, label_visibility="visible")

if summary:
    is_hebrew = any('\u0590' <= c <= '\u05FF' for c in summary[:80])
    rtl = ' dir="rtl"' if is_hebrew else ''
    st.markdown(
        f'<div style="background:var(--bg-softer);border:1px solid var(--hair-soft);'
        f'border-left:3px solid var(--text);padding:18px 22px;margin-top:14px;">'
        f'<div class="lbl" style="margin-bottom:8px;">Daily Summary</div>'
        f'<div{rtl} style="font-size:14px;color:var(--text);line-height:1.85;">{summary}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ─── Joint Technical + Fundamentals consensus ──────────────────────────────
def _aggregate_persona_view(holdings, persona_key, label, color):
    """Build a summary of how many BUY/HOLD/SELL one specific persona issued, plus top picks."""
    rows = []
    for h in holdings:
        for p in h.get("personas", []):
            if p.get("name") == persona_key:
                rows.append((h.get("ticker", ""), (p.get("verdict") or "hold").lower(),
                             int(p.get("conviction", 0))))
                break
    if not rows:
        return None
    n_buy = sum(1 for _, v, _ in rows if v == "buy")
    n_hold = sum(1 for _, v, _ in rows if v == "hold")
    n_sell = sum(1 for _, v, _ in rows if v == "sell")
    top_buys = sorted([(t, c) for t, v, c in rows if v == "buy"], key=lambda x: -x[1])[:3]
    top_sells = sorted([(t, c) for t, v, c in rows if v == "sell"], key=lambda x: -x[1])[:3]
    return {
        "label": label, "color": color,
        "n_buy": n_buy, "n_hold": n_hold, "n_sell": n_sell,
        "top_buys": top_buys, "top_sells": top_sells, "total": len(rows),
    }


tech = _aggregate_persona_view(holdings, "technical_analyst",
                                "Technical Analyst", "#0891B2")
fund = _aggregate_persona_view(holdings, "fundamentals_analyst",
                                "Fundamentals Analyst", "#15803D")

if tech or fund:
    st.markdown('<div style="height:14px;"></div>', unsafe_allow_html=True)
    cols = st.columns(2 if (tech and fund) else 1, gap="medium")

    def _render_persona_summary(card, data):
        if not data:
            return
        top_buy_html = ", ".join(f"<b>{t}</b>" for t, _ in data["top_buys"]) or "—"
        top_sell_html = ", ".join(f"<b>{t}</b>" for t, _ in data["top_sells"]) or "—"
        card.markdown(
            f'<div style="background:white;border:1px solid var(--hair);'
            f'border-top:3px solid {data["color"]};padding:18px 22px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;">'
            f'<div>'
            f'<div class="lbl" style="color:{data["color"]};">{data["label"]} — Consensus</div>'
            f'<div style="font-size:11px;color:var(--text-mute);margin-top:2px;">Across {data["total"]} holdings</div>'
            f'</div>'
            f'<div style="font-family:\'IBM Plex Mono\';font-size:18px;font-weight:500;">'
            f'<span class="up">{data["n_buy"]}</span>'
            f'<span style="color:var(--text-mute);"> · </span>'
            f'<span style="color:var(--hold);">{data["n_hold"]}</span>'
            f'<span style="color:var(--text-mute);"> · </span>'
            f'<span class="dn">{data["n_sell"]}</span>'
            f'</div>'
            f'</div>'
            f'<div style="font-size:13px;color:var(--text);line-height:1.7;border-top:1px solid var(--hair-soft);padding-top:10px;">'
            f'<div><span style="font-size:10px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);">Top buys:</span> '
            f'<span style="font-family:\'IBM Plex Mono\';color:var(--up);">{top_buy_html}</span></div>'
            f'<div style="margin-top:4px;"><span style="font-size:10px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);">Top sells:</span> '
            f'<span style="font-family:\'IBM Plex Mono\';color:var(--dn);">{top_sell_html}</span></div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if tech and fund:
        _render_persona_summary(cols[0], tech)
        _render_persona_summary(cols[1], fund)
    elif tech:
        _render_persona_summary(cols[0], tech)
    elif fund:
        _render_persona_summary(cols[0], fund)

st.markdown('</div>', unsafe_allow_html=True)


# ─── New Ideas (top 3-column grid) ──────────────────────────────────────────
filtered_ideas = sorted(
    [i for i in new_ideas if int(i.get("conviction", 0)) >= min_conv],
    key=lambda x: -int(x.get("conviction", 0)),
)

if filtered_ideas:
    st.markdown(f"""
    <div class="below-section">
      <div class="sect-head">
        <div>
          <h2>New Ideas</h2>
          <div class="sect-sub">Tickers outside your portfolio that fit your profile</div>
        </div>
        <div class="sect-side">{len(filtered_ideas)} ideas</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    cols = st.columns(3, gap="medium")
    for idx, idea in enumerate(filtered_ideas[:6]):
        tk = idea.get("ticker", "")
        name = idea.get("name", tk)
        conv = int(idea.get("conviction", 0))
        rationale = idea.get("rationale", "")
        is_hebrew = any('\u0590' <= c <= '\u05FF' for c in rationale[:80])
        rtl = ' dir="rtl"' if is_hebrew else ''
        with cols[idx % 3]:
            st.markdown(
                f'<div style="border:1px solid var(--hair);border-top:3px solid var(--up);'
                f'background:white;padding:18px 20px;margin-bottom:12px;">'
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:6px;">'
                f'<div>'
                f'<div style="font-size:15px;font-weight:600;color:var(--text);line-height:1.2;">{name}</div>'
                f'<div class="mono txt-dim" style="font-size:11px;margin-top:3px;">{tk}</div>'
                f'</div>'
                f'<span class="pill pill-buy">BUY {conv}%</span>'
                f'</div>'
                f'<div{rtl} style="margin-top:10px;font-size:13px;color:var(--text);line-height:1.75;">{rationale}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ─── Action Items (your holdings, 2-column grid) ───────────────────────────
def _signal_strength(h):
    v = (h.get("verdict") or "hold").lower()
    c = int(h.get("conviction", 0))
    return ({"sell": 0, "buy": 1}.get(v, 2), -c)

filtered = sorted(
    [h for h in holdings if int(h.get("conviction", 0)) >= min_conv],
    key=_signal_strength,
)

st.markdown(f"""
<div class="below-section">
  <div class="sect-head">
    <div>
      <h2>Action Items</h2>
      <div class="sect-sub">Your holdings · sorted: strongest SELL → strongest BUY → HOLD</div>
    </div>
    <div class="sect-side">{len(filtered)} of {len(holdings)} above {min_conv}%</div>
  </div>
</div>
""", unsafe_allow_html=True)

if not filtered:
    st.info(f"No holdings meet the {min_conv}% threshold. Lower the slider.")
else:
    VERDICT_COLOR = {"buy": "var(--up)", "sell": "var(--dn)", "hold": "var(--hold)"}
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
        n_buy = sum(1 for p in personas if (p.get("verdict") or "").lower() == "buy")
        n_hold = sum(1 for p in personas if (p.get("verdict") or "").lower() == "hold")
        n_sell = sum(1 for p in personas if (p.get("verdict") or "").lower() == "sell")
        total = len(personas)
        dots = (
            '<span class="pos-vote-dot buy"></span>' * n_buy +
            '<span class="pos-vote-dot hold"></span>' * n_hold +
            '<span class="pos-vote-dot sell"></span>' * n_sell
        )
        parts = []
        if n_buy: parts.append(f'<b class="up">{n_buy} buy</b>')
        if n_hold: parts.append(f'<b style="color:var(--hold);">{n_hold} hold</b>')
        if n_sell: parts.append(f'<b class="dn">{n_sell} sell</b>')
        return (
            f'<div style="display:flex;align-items:center;gap:10px;font-size:12px;">'
            f'<div class="pos-votes">{dots}</div>'
            f'<div style="color:var(--text-dim);">{" · ".join(parts)} <span class="txt-mute">of {total}</span></div>'
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
            f'<div style="background:white;border:1px solid var(--hair-soft);'
            f'border-left:3px solid {pcol};padding:12px 14px;height:100%;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:6px;">'
            f'<div style="font-size:11px;font-weight:600;color:{pcol};letter-spacing:0.06em;text-transform:uppercase;">{pname}</div>'
            f'<span class="pill {cls}" style="font-size:10px;">{pv.upper()} {pc}</span>'
            f'</div>'
            f'<div{rtl} style="font-size:12.5px;color:var(--text);line-height:1.7;">{rationale}</div>'
            f'</div>'
        )

    rows = [filtered[i:i + 2] for i in range(0, len(filtered), 2)]
    for row in rows:
        cols = st.columns(2, gap="medium")
        for ci, h in enumerate(row):
            tk = h.get("ticker", "")
            name = DISPLAY_NAMES.get(tk, tk)
            verdict = (h.get("verdict") or "hold").lower()
            conviction = int(h.get("conviction", 0))
            color = VERDICT_COLOR.get(verdict, "var(--text-dim)")
            personas = h.get("personas", [])

            with cols[ci]:
                st.markdown(
                    f'<div style="border:1px solid var(--hair);border-top:3px solid {color};'
                    f'background:white;padding:18px 22px;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">'
                    f'<div>'
                    f'<div style="font-size:17px;font-weight:600;color:var(--text);line-height:1.2;">{name}</div>'
                    f'<div class="mono txt-dim" style="font-size:11px;margin-top:3px;">{tk}</div>'
                    f'</div>'
                    f'<span class="pill {VERDICT_CLS.get(verdict, "pill-hold")}" style="font-size:12px;">{verdict.upper()} {conviction}</span>'
                    f'</div>'
                    f'<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--hair-soft);">'
                    f'{vote_dots_html(personas)}'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                if personas:
                    with st.expander(f"Show {len(personas)} analysts' reasoning"):
                        pairs = [personas[i:i+2] for i in range(0, len(personas), 2)]
                        for batch in pairs:
                            html = (
                                '<div style="display:grid;grid-template-columns:repeat(2,1fr);'
                                'gap:8px;margin-top:8px;">'
                                + "".join(persona_block_html(p) for p in batch)
                                + '</div>'
                            )
                            st.markdown(html, unsafe_allow_html=True)

        st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)


# ─── Footer ─────────────────────────────────────────────────────────────────
st.markdown("""
<footer class="page-footer">
  <div>AMIT CAPITAL · Recommendations · Market commentary, not financial advice.</div>
  <div class="right">Powered by Gemini · 5 personas</div>
</footer>
""", unsafe_allow_html=True)
