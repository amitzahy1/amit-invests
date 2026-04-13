"""
Portfolio — Institutional (Concept 🅑) · 1:1 with design demo

Structure:
  1. handle_actions()           — process URL query params (?action=refresh|run_ai)
  2. inject_header("portfolio") — demo's topbar
  3. one st.markdown() w/ render_above_fold() — hero + positions+insights grid + allocation
  4. Below-fold Streamlit widgets: Performance, Snapshot history, Rebalancing, P&L, Risk, Drill-down
  5. Footer
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, load_json

import json
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np

from config import DISPLAY_NAMES, ISRAELI_TICKERS
from data_loader import (
    load_portfolio, get_holdings_df, fetch_live_quotes,
    fetch_usd_ils_rate, fetch_usd_ils_history, fetch_historical_data,
    build_portfolio_df, compute_period_changes,
)
from portfolio_calc import (
    compute_daily_returns, compute_portfolio_cumulative,
    compute_benchmark_cumulative, compute_risk_metrics,
    compute_individual_metrics, compute_correlation_matrix,
)
from charts import (
    fig_portfolio_performance, fig_pnl_waterfall,
    fig_risk_return_scatter, fig_correlation_heatmap,
    fig_individual_detail,
)
from templates.portfolio_layout import render_above_fold, render_footer, SECTOR_PALETTE

# ─── Setup ──────────────────────────────────────────────────────────────────
inject_css()
inject_header("portfolio")
handle_actions()

PCFG = {"displayModeBar": False, "displaylogo": False}


# ─── Data ────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_all_data():
    portfolio = load_portfolio()
    h_df = get_holdings_df(portfolio)
    us = [t for t in h_df["ticker"] if t not in ISRAELI_TICKERS]
    usd_ils = fetch_usd_ils_rate()
    lq = fetch_live_quotes(us)
    hist = fetch_historical_data(us + ["SPY"], "1y")
    fx_h = fetch_usd_ils_history("1y")
    pf = build_portfolio_df(h_df, lq, usd_ils)
    dr = compute_daily_returns(hist)
    w = dict(zip(pf["ticker"], pf["weight"] / 100))
    pc = compute_portfolio_cumulative(dr, w)
    bc = compute_benchmark_cumulative(hist, "SPY")
    pd_r = (1 + pc).pct_change().dropna() if not pc.empty else pd.Series(dtype=float)
    bd = hist["SPY"]["close"].pct_change().dropna() if "SPY" in hist else None
    return {
        "pf": pf, "hist": hist, "pc": pc, "bc": bc,
        "risk": compute_risk_metrics(pd_r, bd),
        "indiv": compute_individual_metrics(hist, hist),
        "corr": compute_correlation_matrix(hist),
        "usd_ils": usd_ils, "fx_h": fx_h,
        "periods": compute_period_changes(hist, h_df, fx_h, usd_ils),
        "updated": portfolio.get("last_updated", ""),
    }


with st.spinner("Loading"):
    D = load_all_data()

settings = load_json("settings.json")
recs = load_json("recommendations.json")

pf = D["pf"]


# ─── Missing-cost banner (above fold) ──────────────────────────────────────
_missing_cost = [h for h in load_json("portfolio.json").get("holdings", [])
                 if h.get("cost_unknown")]
if _missing_cost:
    tickers_html = ", ".join(f"<b>{h.get('ticker')}</b>" for h in _missing_cost)
    st.markdown(
        f'<div class="alert-banner"><div class="alert-banner-inner">'
        f'Cost basis missing for {tickers_html}. Total P&L excludes these holdings. '
        f'Upload a CSV via the Import tab.'
        f'</div></div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ABOVE-THE-FOLD — single HTML block, demo 1:1
# ═══════════════════════════════════════════════════════════════════════════

# ─── Filter (URL ?filter=all|equities|etfs|crypto) ─────────────────────────
current_filter = (st.query_params.get("filter") or "all").lower()
if current_filter == "equities":
    pf_view = pf[pf["asset_type"].str.contains("Stock", case=False, na=False)]
elif current_filter == "etfs":
    pf_view = pf[pf["asset_type"].str.contains("ETF", case=False, na=False)]
elif current_filter == "crypto":
    pf_view = pf[pf["sector"] == "Crypto"]
else:
    pf_view = pf

above_fold_html = render_above_fold(
    pf=pf_view,
    hist=D["hist"],
    risk=D["risk"],
    periods=D["periods"],
    settings=settings,
    recs=recs,
    usd_ils=D["usd_ils"],
    last_updated=D["updated"],
    current_filter=current_filter,
)
st.markdown(above_fold_html, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════
# BELOW-FOLD — Streamlit widgets in same design language
# ═══════════════════════════════════════════════════════════════════════════

# ─── Performance ────────────────────────────────────────────────────────────
st.markdown("""
<div class="below-section">
  <div class="sect-head">
    <div>
      <h2>Performance</h2>
      <div class="sect-sub">1-year cumulative return vs S&amp;P 500</div>
    </div>
    <div class="sect-side">1Y</div>
  </div>
</div>
""", unsafe_allow_html=True)

perf_l, perf_r = st.columns([7, 3], gap="medium")
with perf_l:
    pm = st.radio("Mode", ["Cumulative Return", "Daily Returns"],
                  horizontal=True, label_visibility="collapsed", key="perf_mode")
    st.plotly_chart(
        fig_portfolio_performance(D["pc"], D["bc"], "cumulative" if "Cumulative" in pm else "daily"),
        use_container_width=True, config=PCFG,
    )

with perf_r:
    st.markdown('<div class="lbl" style="margin-bottom:10px;">Performance by Period</div>',
                unsafe_allow_html=True)
    for key, label in [("1d", "Today"), ("1w", "1 Week"), ("1m", "1 Month"),
                       ("6m", "6 Months"), ("1y", "1 Year"), ("all", "All-Time")]:
        p = D["periods"].get(key, {})
        pu, pp = p.get("pnl_usd", 0), p.get("pnl_pct", 0)
        pi, fx = p.get("pnl_ils", 0), p.get("fx_impact_pct", 0)
        cls = "up" if pu >= 0 else "dn"
        st.markdown(
            f"""<div style="padding:10px 0;border-bottom:1px solid var(--hair-soft);">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <span style="font-size:12px;color:var(--text-dim);font-weight:500;">{label}</span>
                    <span class="tab mono {cls}" style="font-size:14px;font-weight:500;">
                      {"+" if pu >= 0 else ""}${pu:,.0f}
                      <span style="font-size:11px;color:var(--text-dim);font-weight:400;">({"+" if pp >= 0 else ""}{pp:.1f}%)</span>
                    </span>
                </div>
                <div style="display:flex;justify-content:space-between;margin-top:3px;">
                    <span style="font-size:10px;color:var(--text-mute);font-family:'IBM Plex Mono';background:var(--bg-softer);padding:1px 6px;">FX {"+" if fx >= 0 else ""}{fx:.1f}%</span>
                    <span class="mono {cls}" style="font-size:11px;">₪{pi:+,.0f}</span>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )


# ─── Snapshot history ──────────────────────────────────────────────────────
snapshots_path = ROOT / "snapshots.jsonl"
if snapshots_path.exists():
    snap_rows = []
    for line in snapshots_path.read_text().splitlines():
        line = line.strip()
        if line:
            try: snap_rows.append(json.loads(line))
            except Exception: pass
    if len(snap_rows) >= 2:
        st.markdown("""
        <div class="below-section">
          <div class="sect-head">
            <div>
              <h2>Recorded History</h2>
              <div class="sect-sub">Daily snapshots — true value curve, not reconstructed</div>
            </div>
            <div class="sect-side">""" + str(len(snap_rows)) + """ snapshots</div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        snap_df = pd.DataFrame(snap_rows).sort_values("date")
        snap_df["date"] = pd.to_datetime(snap_df["date"])

        import plotly.graph_objects as _go
        from charts import INST as _INST
        snap_fig = _go.Figure()
        snap_fig.add_trace(_go.Scatter(
            x=snap_df["date"], y=snap_df["value_usd"],
            line=dict(color=_INST["up"], width=1.5),
            hovertemplate="%{x|%b %d, %Y}<br>$%{y:,.0f}<extra></extra>",
        ))
        snap_fig.update_layout(
            template=None, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Helvetica Neue", color="#0A0A0A", size=11),
            height=220, margin=dict(l=12, r=12, t=12, b=12),
            xaxis=dict(gridcolor="#F0F0F0", linecolor="#E5E5E5",
                       tickfont=dict(size=10, color="#6B7280", family="IBM Plex Mono")),
            yaxis=dict(gridcolor="#F0F0F0", linecolor="#E5E5E5", tickprefix="$",
                       tickfont=dict(size=10, color="#6B7280", family="IBM Plex Mono")),
            hovermode="x unified", showlegend=False,
        )
        st.plotly_chart(snap_fig, use_container_width=True, config=PCFG)


# ─── Wealth Projection ─────────────────────────────────────────────────────
contrib_ils = settings.get("contribution_ils", 0)
contrib_days = settings.get("contribution_frequency_days", 60)
contributions_per_year = (365 / contrib_days) if contrib_days > 0 else 0
annual_contrib_usd = (contrib_ils * contributions_per_year) / D["usd_ils"] if D["usd_ils"] else 0

st.markdown("""
<div class="below-section">
  <div class="sect-head">
    <div>
      <h2>Wealth Projection</h2>
      <div class="sect-sub">Compound your portfolio with bi-monthly contributions — principal vs gains</div>
    </div>
    <div class="sect-side">""" + f"₪{contrib_ils:,.0f} every {contrib_days}d" + """</div>
  </div>
</div>
""", unsafe_allow_html=True)

wp_c1, wp_c2, wp_c3 = st.columns([2, 2, 8], gap="medium")
with wp_c1:
    proj_rate = st.slider("Annual return %", min_value=0.0, max_value=20.0,
                          value=8.0, step=0.5, format="%.1f%%", key="proj_rate")
with wp_c2:
    proj_years = st.slider("Years", min_value=5, max_value=40, value=20, step=1, key="proj_years")

# Compute year-by-year projection
starting_balance_usd = float(pf["value_usd"].sum())
r = proj_rate / 100.0

years_axis = list(range(0, proj_years + 1))
invested_series = [starting_balance_usd]   # cumulative principal (starting + contributions)
balance_series  = [starting_balance_usd]   # total balance with growth

for y in range(1, proj_years + 1):
    prev = balance_series[-1]
    # End-of-year compounding + contribution mid-year (avg compound factor 1 + r/2)
    new_balance = prev * (1 + r) + annual_contrib_usd * (1 + r / 2)
    balance_series.append(new_balance)
    invested_series.append(invested_series[-1] + annual_contrib_usd)

gains_series = [b - i for b, i in zip(balance_series, invested_series)]
final_balance = balance_series[-1]
total_invested = invested_series[-1]
total_gains = gains_series[-1]

# Stacked area: principal at bottom, gains stacked on top
import plotly.graph_objects as _go
proj_fig = _go.Figure()
proj_fig.add_trace(_go.Scatter(
    x=years_axis, y=invested_series,
    mode="lines", name="Principal (invested)",
    line=dict(width=0),
    fillcolor="rgba(55, 65, 81, 0.55)",
    stackgroup="one",
    hovertemplate="Year %{x}<br>Invested: $%{y:,.0f}<extra></extra>",
))
proj_fig.add_trace(_go.Scatter(
    x=years_axis, y=gains_series,
    mode="lines", name="Gains (compound returns)",
    line=dict(width=0),
    fillcolor="rgba(4, 120, 87, 0.45)",
    stackgroup="one",
    hovertemplate="Year %{x}<br>Gains: $%{y:,.0f}<extra></extra>",
))
# Total balance overlay (thin line)
proj_fig.add_trace(_go.Scatter(
    x=years_axis, y=balance_series,
    mode="lines", name="Total balance",
    line=dict(color="#0A0A0A", width=1.5),
    hovertemplate="Year %{x}<br><b>$%{y:,.0f}</b><extra></extra>",
))
# End-point labels (last year)
end_x = years_axis[-1]
proj_fig.add_annotation(
    x=end_x, y=invested_series[-1] / 2,
    text=f"<b>Principal</b><br>${total_invested:,.0f}",
    showarrow=False, xanchor="right", yanchor="middle",
    font=dict(size=11, color="#374151", family="Helvetica Neue"),
    bgcolor="rgba(255,255,255,0.85)", borderpad=4,
)
proj_fig.add_annotation(
    x=end_x, y=invested_series[-1] + (gains_series[-1] / 2),
    text=f"<b>Gains</b><br>+${total_gains:,.0f}",
    showarrow=False, xanchor="right", yanchor="middle",
    font=dict(size=11, color="#047857", family="Helvetica Neue"),
    bgcolor="rgba(255,255,255,0.85)", borderpad=4,
)
proj_fig.add_annotation(
    x=end_x, y=balance_series[-1],
    text=f"<b>Total: ${final_balance:,.0f}</b>",
    showarrow=True, arrowhead=0, arrowsize=1, arrowwidth=1, arrowcolor="#0A0A0A",
    ax=-30, ay=-20,
    xanchor="right", yanchor="bottom",
    font=dict(size=12, color="#0A0A0A", family="Helvetica Neue"),
    bgcolor="white", bordercolor="#0A0A0A", borderwidth=1, borderpad=5,
)
# Doubling milestone (if total reaches 2× starting balance within range)
double_year = next((y for y, b in enumerate(balance_series) if b >= 2 * starting_balance_usd), None)
if double_year and double_year > 0 and starting_balance_usd > 0:
    proj_fig.add_vline(
        x=double_year, line_dash="dot", line_color="#9CA3AF", line_width=1,
        annotation_text=f"2× starting · year {double_year}",
        annotation_position="top",
        annotation_font=dict(size=10, color="#6B7280", family="IBM Plex Mono"),
    )

proj_fig.update_layout(
    template=None,
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Helvetica Neue", color="#0A0A0A", size=11),
    height=380, margin=dict(l=60, r=80, t=30, b=50),
    xaxis=dict(title=dict(text="Years from now",
                          font=dict(size=11, color="#6B7280", family="Helvetica Neue")),
               gridcolor="#F0F0F0", linecolor="#E5E5E5",
               tickfont=dict(size=10, color="#6B7280", family="IBM Plex Mono")),
    yaxis=dict(title=dict(text="USD value",
                          font=dict(size=11, color="#6B7280", family="Helvetica Neue")),
               gridcolor="#F0F0F0", linecolor="#E5E5E5", tickprefix="$",
               tickformat=",.0f",
               tickfont=dict(size=10, color="#6B7280", family="IBM Plex Mono")),
    hovermode="x unified", showlegend=True,
    legend=dict(orientation="h", yanchor="top", y=1.08, xanchor="left", x=0,
                font=dict(size=11, color="#374151")),
)
st.plotly_chart(proj_fig, use_container_width=True, config=PCFG)

# Summary cards row
proj_summary_html = f"""
<div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:0; border:1px solid var(--hair); margin-top:8px;">
  <div style="padding:14px 18px; border-right:1px solid var(--hair-soft);">
    <div class="lbl">Final Balance</div>
    <div style="font-size:22px; font-weight:400; font-family:'IBM Plex Mono'; margin-top:4px;">${final_balance:,.0f}</div>
    <div style="font-size:11px; color:var(--text-dim); margin-top:2px;">Year {proj_years}</div>
  </div>
  <div style="padding:14px 18px; border-right:1px solid var(--hair-soft);">
    <div class="lbl">Total Invested</div>
    <div style="font-size:22px; font-weight:400; font-family:'IBM Plex Mono'; margin-top:4px;">${total_invested:,.0f}</div>
    <div style="font-size:11px; color:var(--text-dim); margin-top:2px;">Starting + ${annual_contrib_usd:,.0f}/yr × {proj_years}</div>
  </div>
  <div style="padding:14px 18px; border-right:1px solid var(--hair-soft);">
    <div class="lbl">Total Gains</div>
    <div style="font-size:22px; font-weight:400; font-family:'IBM Plex Mono'; color:var(--up); margin-top:4px;">+${total_gains:,.0f}</div>
    <div style="font-size:11px; color:var(--text-dim); margin-top:2px;">{(total_gains/total_invested*100) if total_invested else 0:.0f}% of invested</div>
  </div>
  <div style="padding:14px 18px;">
    <div class="lbl">In ILS</div>
    <div style="font-size:22px; font-weight:400; font-family:'IBM Plex Mono'; margin-top:4px;">₪{final_balance * D["usd_ils"]:,.0f}</div>
    <div style="font-size:11px; color:var(--text-dim); margin-top:2px;">at today's USD/ILS</div>
  </div>
</div>
"""
st.markdown(proj_summary_html, unsafe_allow_html=True)


# ─── Rebalancing ────────────────────────────────────────────────────────────
preferred = set(settings.get("preferred_sectors", []))
avoid = set(settings.get("avoid_sectors", []))
crypto_cap = float(settings.get("crypto_cap_pct", 10))
sector_weights_pct = pf.groupby("sector")["weight"].sum().to_dict()
crypto_weight = sector_weights_pct.get("Crypto", 0.0)


def _compute_targets(pf_df: pd.DataFrame) -> dict:
    sectors = list(pf_df["sector"].unique())
    pref_ex_broad = [s for s in preferred if s in sectors and s != "Broad Market"]
    others = [s for s in sectors if s not in preferred and s not in ("Broad Market", "Crypto")]
    targets = {}
    if "Broad Market" in sectors: targets["Broad Market"] = 35.0
    if pref_ex_broad:
        each = 45.0 / len(pref_ex_broad)
        for s in pref_ex_broad: targets[s] = each
    if others:
        each = max(0.0, 20.0 - (crypto_cap if "Crypto" in sectors else 0.0)) / max(1, len(others))
        for s in others: targets[s] = each
    if "Crypto" in sectors:
        targets["Crypto"] = 0.0 if "Crypto" in avoid else crypto_cap
    total = sum(targets.values())
    if total > 0:
        targets = {k: v * 100 / total for k, v in targets.items()}
    return targets


targets = _compute_targets(pf)
rows = []
for sec, w in sorted(sector_weights_pct.items(), key=lambda kv: -kv[1]):
    tgt = targets.get(sec, 0.0)
    drift = w - tgt
    rows.append((sec, w, tgt, drift))

st.markdown("""
<div class="below-section">
  <div class="sect-head">
    <div>
      <h2>Rebalancing</h2>
      <div class="sect-sub">Current allocation vs target weights</div>
    </div>
    <div class="sect-side">""" + f"crypto cap {crypto_cap:.0f}%" + """</div>
  </div>
</div>
""", unsafe_allow_html=True)

reb1, reb2 = st.columns([3, 2], gap="medium")
with reb1:
    dfreb = pd.DataFrame(rows, columns=["Sector", "Current %", "Target %", "Drift (pp)"])
    st.dataframe(
        dfreb.style.format({"Current %": "{:.1f}%", "Target %": "{:.1f}%", "Drift (pp)": "{:+.1f}"})
                   .map(lambda v: "color:#047857" if isinstance(v, (int, float)) and v > 1
                        else ("color:#B91C1C" if isinstance(v, (int, float)) and v < -1 else ""),
                        subset=["Drift (pp)"]),
        use_container_width=True, hide_index=True, height=min(420, 52 + len(rows) * 36),
    )

with reb2:
    st.markdown('<div class="lbl" style="margin-bottom:10px;">Action Items</div>',
                unsafe_allow_html=True)
    actions = sorted(rows, key=lambda r: -abs(r[3]))[:5]
    for sec, cur, tgt, drift in actions:
        arrow = "↓" if drift > 0 else "↑"
        verb = "Reduce" if drift > 0 else "Add to"
        color = "#B91C1C" if drift > 0 else "#047857"
        st.markdown(
            f"<div style='padding:10px 14px;border-left:3px solid {color};"
            f"background:#FAFAFA;border:1px solid #F0F0F0;border-left-width:3px;margin-bottom:6px;font-size:12px;'>"
            f"<b style='color:{color};'>{arrow} {verb}</b> — {sec}: "
            f"<span class='mono'>{cur:.1f}%</span> vs target <span class='mono'>{tgt:.1f}%</span> "
            f"<span class='mono' style='color:{color};'>({drift:+.1f}pp)</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    if crypto_weight > crypto_cap:
        st.warning(f"Crypto exposure {crypto_weight:.1f}% exceeds your {crypto_cap:.0f}% cap.")


# ─── P&L ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="below-section">
  <div class="sect-head">
    <div>
      <h2>P&amp;L Analysis</h2>
      <div class="sect-sub">Per holding, sorted</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
pnl_ccy = st.radio("Currency", ["USD $", "ILS ₪"], horizontal=True,
                   label_visibility="collapsed", key="pnl_c")
st.plotly_chart(fig_pnl_waterfall(pf, "usd" if "USD" in pnl_ccy else "ils"),
                use_container_width=True, config=PCFG)


# ─── Risk (tabs) ────────────────────────────────────────────────────────────
st.markdown("""
<div class="below-section">
  <div class="sect-head">
    <div>
      <h2>Risk Analytics</h2>
      <div class="sect-sub">Annualized over 1 year</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

risk = D["risk"]
indiv = D["indiv"]
tab_metrics, tab_risk, tab_corr = st.tabs(["Metrics", "Risk vs Return", "Correlation"])

with tab_metrics:
    vol_v = risk['ann_volatility'] * 100
    alp_v = risk['alpha'] * 100
    metrics_rows_html = ""
    for label, val, cls, desc in [
        ("Alpha",         f"{alp_v:+.2f}%", "up" if alp_v >= 0 else "dn",
         "Excess return vs S&P 500"),
        ("Beta",          f"{risk['beta']:.2f}", "",
         "Sensitivity to market (1.0 = moves with market)"),
        ("Volatility",    f"{vol_v:.1f}%", "",
         "Std dev of returns (annualized)"),
        ("Sharpe Ratio",  f"{risk['sharpe']:.2f}", "",
         "Return per unit of total risk (>1 good, >2 excellent)"),
        ("Sortino Ratio", f"{risk['sortino']:.2f}", "",
         "Return per unit of downside risk"),
        ("Calmar Ratio",  f"{risk['calmar']:.2f}", "",
         "Return divided by max drawdown"),
    ]:
        metrics_rows_html += (
            f"<tr style='border-bottom:1px solid var(--hair-soft);'>"
            f"<td style='padding:12px 14px;font-weight:500;'>{label}</td>"
            f"<td style='padding:12px 14px;text-align:right;font-family:IBM Plex Mono;' class='{cls}'>{val}</td>"
            f"<td style='padding:12px 14px;color:var(--text-dim);font-size:13px;'>{desc}</td>"
            f"</tr>"
        )
    st.markdown(
        f"<div style='border:1px solid var(--hair);background:white;'>"
        f"<table style='width:100%;border-collapse:collapse;'>"
        f"<thead><tr style='background:var(--bg-softer);border-bottom:1px solid var(--hair);'>"
        f"<th style='text-align:left;padding:12px 14px;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);font-weight:500;'>Metric</th>"
        f"<th style='text-align:right;padding:12px 14px;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);font-weight:500;'>Value</th>"
        f"<th style='text-align:left;padding:12px 14px;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:var(--text-dim);font-weight:500;'>Interpretation</th>"
        f"</tr></thead><tbody>{metrics_rows_html}</tbody></table></div>",
        unsafe_allow_html=True,
    )

with tab_risk:
    st.plotly_chart(fig_risk_return_scatter(indiv, pf), use_container_width=True, config=PCFG)

with tab_corr:
    st.plotly_chart(fig_correlation_heatmap(D["corr"]), use_container_width=True, config=PCFG)


# ─── Drill-Down ─────────────────────────────────────────────────────────────
st.markdown("""
<div class="below-section">
  <div class="sect-head">
    <div>
      <h2>Drill-Down</h2>
      <div class="sect-sub">Candlestick + analytics for any holding</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

avail = [t for t in pf["ticker"] if t not in ISRAELI_TICKERS and t in D["hist"]]
sel = st.selectbox("Select a holding", avail,
                   format_func=lambda t: f"{DISPLAY_NAMES.get(t, t)} ({t})",
                   label_visibility="collapsed")

if sel and sel in D["hist"]:
    row = pf[pf["ticker"] == sel].iloc[0]
    hist = D["hist"][sel]
    closes = hist["close"]
    now_p = closes.iloc[-1]
    period_rets = {}
    for label, days in [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126)]:
        if len(closes) > days:
            old_p = closes.iloc[-1 - days]
            period_rets[label] = ((now_p / old_p) - 1) * 100

    pills = " · ".join([f"<span class='mono' style=\"color:{'#047857' if v >= 0 else '#B91C1C'};font-weight:500;\">{k} {v:+.1f}%</span>"
                        for k, v in period_rets.items()])
    st.markdown(
        f"<div style='background:white;border:1px solid var(--hair);padding:10px 16px;"
        f"font-size:13px;margin-bottom:12px;'>{pills}</div>",
        unsafe_allow_html=True,
    )

    dc = st.columns([7, 3], gap="medium")
    with dc[0]:
        st.plotly_chart(fig_individual_detail(hist, sel, row["cost_price"]),
                        use_container_width=True, config=PCFG)
    with dc[1]:
        pnl_dir = "in profit" if row['pnl_usd'] >= 0 else "in loss"
        cls_p = "up" if row['pnl_usd'] >= 0 else "dn"
        st.markdown(
            f"""<div style='border:1px solid var(--hair);background:white;padding:18px 20px;margin-bottom:12px;'>
                <div class='lbl' style='margin-bottom:10px;'>Summary — {row['display_name']}</div>
                <div style='font-size:13px;line-height:1.9;color:var(--text);'>
                    Position is <b>{pnl_dir}</b>: <span class='mono tab {cls_p}'>${abs(row['pnl_usd']):,.0f}</span> ({row['pnl_pct']:+.1f}%)<br>
                    Value: <span class='mono tab'>${row['value_usd']:,.0f}</span> <span class='txt-dim'>(₪{row['value_ils']:,.0f})</span><br>
                    Weight: <span class='mono tab'>{row['weight']:.1f}%</span><br>
                    Sector: <b>{row['sector']}</b>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
        if sel in indiv.index:
            im = indiv.loc[sel]
            beta, vol, ret, dd = im['beta'], im['ann_volatility'] * 100, im['ann_return'] * 100, im['max_drawdown'] * 100
            beta_txt = "very volatile" if beta > 1.3 else ("somewhat volatile" if beta > 1 else ("reasonable" if beta > 0.7 else "conservative"))
            vol_txt = "extremely volatile" if vol > 40 else ("volatile" if vol > 25 else "relatively stable")
            cls_r = "up" if ret >= 0 else "dn"
            st.markdown(
                f"""<div style='border:1px solid var(--hair);background:white;padding:18px 20px;'>
                    <div class='lbl' style='margin-bottom:10px;'>Analytics</div>
                    <div style='font-size:12px;line-height:1.95;color:var(--text);'>
                        Annual return: <span class='mono tab {cls_r}'>{ret:+.1f}%</span><br>
                        Volatility: <span class='mono tab'>{vol:.0f}%</span> <span class='txt-dim'>— {vol_txt}</span><br>
                        Beta: <span class='mono tab'>{beta:.2f}</span> <span class='txt-dim'>— {beta_txt}</span><br>
                        Max drawdown: <span class='mono tab dn'>{dd:.0f}%</span>
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )


# ─── Footer ─────────────────────────────────────────────────────────────────
st.markdown(render_footer(D["usd_ils"]), unsafe_allow_html=True)
