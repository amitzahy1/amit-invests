"""
Performance — backtest dashboard.

Shows how accurate the AI recommendations have been historically:
- Hit rate (% of correct calls)
- Hit rate by verdict type (BUY / HOLD / SELL)
- Calibration: does higher conviction = higher hit rate?
- Alpha vs S&P 500
- Individual verdict outcomes (sortable list)
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, minify

import html as _html
import streamlit as st

inject_css()
inject_header("performance")
handle_actions()

# ─── Load backtest ───────────────────────────────────────────────────────────
try:
    from backtest_engine import get_or_compute_backtest
    with st.spinner("Running backtest…"):
        result = get_or_compute_backtest(days_elapsed=30)
except Exception as e:
    st.error(f"Backtest failed: {e}")
    st.stop()

status = result.get("status")
if status == "no_data":
    st.markdown("""
    <div class="below-section">
      <div class="sect-head"><div>
        <h2>Performance</h2>
        <div class="sect-sub">No verdict history yet</div>
      </div></div>
      <div style="border:1px dashed var(--hair);padding:28px;background:var(--bg-softer);
           font-size:14px;text-align:center;line-height:1.7;">
        Performance tracking starts accumulating once the daily pipeline runs.<br>
        Come back after a few days of runs to see backtest results.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

if status == "insufficient_history":
    st.markdown("""
    <div class="below-section">
      <div class="sect-head"><div>
        <h2>Performance</h2>
        <div class="sect-sub">Not enough history yet</div>
      </div></div>
      <div style="border:1px dashed var(--hair);padding:28px;background:var(--bg-softer);
           font-size:14px;text-align:center;line-height:1.7;">
        Need at least 3 days of verdict history to compute accuracy.<br>
        The daily pipeline logs verdicts — please wait for it to run a few times.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

total = result.get("total", 0)
hit_rate = result.get("hit_rate", 0)
by_verdict = result.get("by_verdict", {})
calibration = result.get("calibration", {})
buy_return = result.get("buy_portfolio_avg_return_pct", 0)
spy_return = result.get("spy_return_pct")
alpha = result.get("alpha_vs_spy_pct")
details = result.get("details", [])

# ─── Hero ────────────────────────────────────────────────────────────────────

# Color code hit rate
if hit_rate >= 65:
    hr_color = "var(--up)"
    hr_label = "Strong"
elif hit_rate >= 55:
    hr_color = "#b45309"
    hr_label = "Acceptable"
else:
    hr_color = "var(--dn)"
    hr_label = "Below target"

alpha_html = "—"
alpha_color = "var(--text-mute)"
if alpha is not None:
    alpha_color = "var(--up)" if alpha > 0 else "var(--dn)"
    sign = "+" if alpha > 0 else ""
    alpha_html = f"{sign}{alpha:.1f}%"

buy_color = "var(--up)" if buy_return > 0 else "var(--dn)"
buy_sign = "+" if buy_return > 0 else ""

st.markdown(minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Performance — AI Accuracy Track Record</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">{total} verdicts analyzed</div>
</div>
<div class="hero-grid" style="grid-template-columns: repeat(4, 1fr);">
<div class="hero-cell">
<div class="lbl">Hit Rate</div>
<div class="hero-value tab" style="color:{hr_color};">{hit_rate:.0f}<span class="hero-value-suffix">%</span></div>
<div class="hero-sub">{hr_label} · {result.get('correct', 0)}/{total} correct</div>
</div>
<div class="hero-cell">
<div class="lbl">BUY Avg Return</div>
<div class="hero-value tab" style="color:{buy_color};">{buy_sign}{buy_return:.1f}<span class="hero-value-suffix">%</span></div>
<div class="hero-sub">{by_verdict.get('buy', {}).get('count', 0)} BUY calls</div>
</div>
<div class="hero-cell">
<div class="lbl">S&P 500</div>
<div class="hero-value tab">{f"+{spy_return:.1f}%" if spy_return and spy_return >= 0 else f"{spy_return:.1f}%" if spy_return else "—"}</div>
<div class="hero-sub">Same period benchmark</div>
</div>
<div class="hero-cell">
<div class="lbl">Alpha vs SPY</div>
<div class="hero-value tab" style="color:{alpha_color};">{alpha_html}</div>
<div class="hero-sub">Excess return</div>
</div>
</div>
</section>
"""), unsafe_allow_html=True)

# ─── Hit Rate by Verdict Type ────────────────────────────────────────────────
st.markdown(
    '<div class="below-section"><div class="sect-head"><div>'
    '<h2>Hit Rate by Verdict Type</h2>'
    '<div class="sect-sub">How accurate is each verdict category?</div>'
    '</div></div></div>', unsafe_allow_html=True)

cols = st.columns(3, gap="small")
for i, (vtype, icon, label) in enumerate([
    ("buy",  "🟢", "BUY Recommendations"),
    ("hold", "🟡", "HOLD Recommendations"),
    ("sell", "🔴", "SELL Recommendations"),
]):
    data = by_verdict.get(vtype, {})
    count = data.get("count", 0)
    hr = data.get("hit_rate", 0)
    avg_ret = data.get("avg_return_pct", 0)

    if hr >= 60:
        box_color = "#047857"
    elif hr >= 45:
        box_color = "#b45309"
    else:
        box_color = "#b91c1c"

    sign = "+" if avg_ret >= 0 else ""
    ret_color = "#047857" if avg_ret > 0 else "#b91c1c" if avg_ret < 0 else "#6b7280"

    with cols[i]:
        st.markdown(
            f'<div style="border:1px solid var(--hair);border-left:4px solid {box_color};'
            f'border-radius:6px;padding:18px 20px;background:#fff;">'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
            f'<span style="font-size:16px;">{icon}</span>'
            f'<span style="font-size:11px;font-weight:600;color:var(--text-dim);'
            f'text-transform:uppercase;letter-spacing:0.1em;">{label}</span>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;">'
            f'<div><div style="font-size:28px;font-weight:700;color:{box_color};'
            f'font-family:\'IBM Plex Mono\',monospace;">{hr}%</div>'
            f'<div style="font-size:11px;color:var(--text-mute);">hit rate</div></div>'
            f'<div style="text-align:right;">'
            f'<div style="font-size:18px;font-weight:600;color:{ret_color};'
            f'font-family:\'IBM Plex Mono\',monospace;">{sign}{avg_ret:.1f}%</div>'
            f'<div style="font-size:11px;color:var(--text-mute);">avg return</div></div>'
            f'</div>'
            f'<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--hair-soft);'
            f'font-size:11px;color:var(--text-dim);">'
            f'{count} call{"s" if count != 1 else ""} tracked</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

# ─── Calibration ─────────────────────────────────────────────────────────────
st.markdown(
    '<div class="below-section"><div class="sect-head"><div>'
    '<h2>Calibration</h2>'
    '<div class="sect-sub">Does higher conviction translate to higher accuracy?</div>'
    '</div></div></div>', unsafe_allow_html=True)

st.caption(
    "A well-calibrated model should show **higher hit rates for higher conviction buckets**. "
    "If 80%+ conviction has 80%+ hit rate, the model is reliable. "
    "Large drops in hit rate at high conviction = overconfidence."
)

cal_cols = st.columns(4, gap="small")
for i, bucket in enumerate(["50-60", "60-70", "70-80", "80+"]):
    data = calibration.get(bucket, {})
    count = data.get("count", 0)
    hr = data.get("hit_rate", 0)
    avg_ret = data.get("avg_return_pct", 0)

    bar_width = max(4, hr)
    if hr >= 70:
        bar_color = "#047857"
    elif hr >= 50:
        bar_color = "#b45309"
    else:
        bar_color = "#b91c1c"

    with cal_cols[i]:
        st.markdown(
            f'<div style="border:1px solid var(--hair);border-radius:6px;padding:14px 16px;'
            f'background:#fff;">'
            f'<div style="font-size:10px;font-weight:700;color:var(--text-mute);'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">'
            f'Conviction {bucket}</div>'
            f'<div style="font-size:22px;font-weight:700;color:{bar_color};'
            f'font-family:\'IBM Plex Mono\',monospace;">{hr}%</div>'
            f'<div style="font-size:11px;color:var(--text-dim);margin-bottom:6px;">'
            f'hit rate · {count} calls</div>'
            f'<div style="height:6px;background:#f0f0f0;border-radius:3px;overflow:hidden;">'
            f'<div style="width:{bar_width}%;height:100%;background:{bar_color};"></div>'
            f'</div>'
            f'<div style="font-size:11px;color:var(--text-mute);margin-top:6px;'
            f'font-family:\'IBM Plex Mono\',monospace;">avg {avg_ret:+.1f}%</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

# ─── Individual outcomes table ───────────────────────────────────────────────
if details:
    st.markdown(
        '<div class="below-section"><div class="sect-head"><div>'
        '<h2>Individual Verdicts</h2>'
        '<div class="sect-sub">Sorted by absolute return — biggest wins and losses first</div>'
        '</div></div></div>', unsafe_allow_html=True)

    with st.expander(f"All {len(details)} verdict outcomes", expanded=False):
        rows_html = []
        for d in details[:100]:  # cap at 100
            ret = d["return_pct"]
            ret_color = "#047857" if ret > 0 else "#b91c1c" if ret < 0 else "#6b7280"
            sign = "+" if ret >= 0 else ""
            out_emoji = "✅" if d["outcome"] == "correct" else "❌"
            v = d["verdict"].upper()
            v_color = {"BUY": "#047857", "SELL": "#b91c1c", "HOLD": "#b45309"}.get(v, "#6b7280")
            rows_html.append(
                f'<tr>'
                f'<td class="recs-tbl-tkr mono">{d["ticker"]}</td>'
                f'<td class="txt-dim" style="font-size:11px;">{d["date"]}</td>'
                f'<td><span style="color:{v_color};font-weight:600;font-size:11px;">{v}</span>'
                f' <span class="txt-mute mono" style="font-size:11px;">{d["conviction"]}%</span></td>'
                f'<td class="r mono">${d["entry_price"]:.2f}</td>'
                f'<td class="r mono">${d["current_price"]:.2f}</td>'
                f'<td class="r mono" style="color:{ret_color};font-weight:600;">{sign}{ret:.1f}%</td>'
                f'<td>{out_emoji}</td>'
                f'</tr>'
            )
        st.markdown(
            '<table class="recs-table"><thead><tr>'
            '<th>Ticker</th><th>Date</th><th>Verdict</th>'
            '<th class="r">Entry</th><th class="r">Current</th>'
            '<th class="r">Return</th><th>Outcome</th>'
            f'</tr></thead><tbody>{"".join(rows_html)}</tbody></table>',
            unsafe_allow_html=True,
        )

# Footer
st.markdown("""
<footer class="page-footer">
  <div>AMIT CAPITAL · Performance Tracking · Historical accuracy, not a guarantee.</div>
  <div class="right">Cache refreshes every 12 hours</div>
</footer>
""", unsafe_allow_html=True)
