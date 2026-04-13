"""
Plotly charts — Institutional (Concept 🅑)
Hairline gridlines, Helvetica Neue, muted palette, no fills, no gradients.
"""

import plotly.graph_objects as go
import pandas as pd
import numpy as np
from config import SECTOR_COLORS, CHART_COLORS, DISPLAY_NAMES

# Institutional palette — muted, high-contrast for print
INST = {
    "text": "#0A0A0A",
    "dim": "#6B7280",
    "grid": "#F0F0F0",
    "border": "#E5E5E5",
    "up": "#047857",
    "dn": "#B91C1C",
    "hold": "#92400E",
    "accent": "#111827",
    "fill_up": "rgba(4,120,87,0.08)",
    "fill_dn": "rgba(185,28,28,0.08)",
}

LAYOUT_BASE = dict(
    template=None,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Helvetica Neue, Inter, sans-serif", color=INST["text"], size=11),
    margin=dict(l=12, r=12, t=36, b=12),
    title=dict(font=dict(size=12, color=INST["dim"], family="Helvetica Neue, Inter, sans-serif"),
               x=0.015, xanchor="left", y=0.97, yanchor="top"),
    hoverlabel=dict(bgcolor="white", font_color=INST["text"], bordercolor=INST["border"],
                    font_family="IBM Plex Mono", font_size=12),
    legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)",
                font=dict(size=10, color=INST["dim"])),
    xaxis=dict(gridcolor=INST["grid"], linecolor=INST["border"], zerolinecolor=INST["border"],
               tickfont=dict(size=10, color=INST["dim"], family="IBM Plex Mono")),
    yaxis=dict(gridcolor=INST["grid"], linecolor=INST["border"], zerolinecolor=INST["border"],
               tickfont=dict(size=10, color=INST["dim"], family="IBM Plex Mono")),
)


def _base(**kw):
    d = {**LAYOUT_BASE}
    for k, v in kw.items():
        if k in ("xaxis", "yaxis") and k in d and isinstance(v, dict):
            d[k] = {**d[k], **v}
        else:
            d[k] = v
    return d


# Institutional sector palette (muted, monochrome-adjacent)
SECTOR_PALETTE = {
    "Technology":             "#1E3A8A",
    "Consumer Discretionary": "#4C1D95",
    "Financials":             "#164E63",
    "Crypto":                 "#9A3412",
    "Healthcare":             "#064E3B",
    "Broad Market":           "#374151",
    "Aerospace & Defense":    "#713F12",
    "Energy / Uranium":       "#7F1D1D",
    "Energy / Nuclear":       "#7C2D12",
    "Insurance (Israel)":     "#14532D",
    "Broad Market (Israel)":  "#115E59",
    "Other":                  "#6B7280",
}


# ─── 1. Performance ─────────────────────────────────────────────────────────

def fig_portfolio_performance(port_cum, bench_cum, mode="cumulative"):
    fig = go.Figure()
    if port_cum.empty:
        fig.update_layout(**_base(title="Performance — no data"))
        return fig

    if mode == "cumulative":
        port_vals = port_cum.values * 100
        fig.add_trace(go.Scatter(
            x=port_cum.index, y=port_vals,
            name="Portfolio",
            line=dict(color=INST["up"], width=2.25, shape="spline", smoothing=0.3),
            fill="tozeroy", fillcolor=INST["fill_up"],
            hovertemplate="%{x|%b %d, %Y}<br>Portfolio %{y:+.2f}%<extra></extra>",
        ))
        if not bench_cum.empty:
            fig.add_trace(go.Scatter(
                x=bench_cum.index, y=bench_cum.values * 100,
                name="S&P 500",
                line=dict(color=INST["dim"], width=1.25, dash="dot"),
                hovertemplate="%{x|%b %d, %Y}<br>S&P 500 %{y:+.2f}%<extra></extra>",
            ))
        fig.update_layout(**_base(
            title="PORTFOLIO vs S&P 500 — CUMULATIVE RETURN",
            yaxis=dict(ticksuffix="%", zeroline=True,
                       zerolinecolor=INST["dim"], zerolinewidth=1),
            hovermode="x unified",
            height=360,
            legend=dict(orientation="h", yanchor="top", y=1.08, xanchor="right", x=1),
        ))
    elif mode == "daily":
        daily = port_cum.diff()
        if not daily.empty:
            colors = [INST["up"] if v >= 0 else INST["dn"] for v in daily.values]
            fig.add_trace(go.Bar(
                x=daily.index, y=daily.values * 100,
                marker_color=colors, marker_line_width=0,
                hovertemplate="%{x|%b %d}<br>%{y:+.2f}%<extra></extra>",
            ))
        fig.update_layout(**_base(
            title="DAILY RETURNS",
            yaxis=dict(ticksuffix="%"),
            height=360,
        ))
    return fig


# ─── 2. Donut (allocation) ───────────────────────────────────────────────────

def fig_allocation_donut(df, group_by="ticker"):
    if group_by == "ticker":
        labels, values = df["display_name"].tolist(), df["value_ils"].tolist()
        colors = CHART_COLORS[:len(labels)]
    elif group_by == "sector":
        g = df.groupby("sector")["value_ils"].sum().sort_values(ascending=False)
        labels, values = g.index.tolist(), g.values.tolist()
        colors = [SECTOR_PALETTE.get(s, "#6B7280") for s in labels]
    elif group_by == "asset_type":
        g = df.groupby("asset_type")["value_ils"].sum().sort_values(ascending=False)
        labels, values = g.index.tolist(), g.values.tolist()
        colors = CHART_COLORS[:len(labels)]
    else:
        labels, values, colors = [], [], []

    total = sum(values) if values else 0
    titles = {"ticker": "HOLDING", "sector": "SECTOR", "asset_type": "ASSET TYPE"}

    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.62,
        marker=dict(colors=colors, line=dict(color="white", width=1.5)),
        textinfo="label+percent", textposition="outside",
        textfont=dict(size=10, family="Helvetica Neue", color=INST["text"]),
        hovertemplate="<b>%{label}</b><br>₪%{value:,.0f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(**_base(
        title=f"ALLOCATION BY {titles.get(group_by, group_by).upper()}",
        height=400,
        annotations=[dict(
            text=f"<b>₪{total:,.0f}</b>",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color=INST["text"], family="IBM Plex Mono"),
        )],
        showlegend=False,
    ))
    return fig


# ─── 3. Sector Bar ──────────────────────────────────────────────────────────

def fig_sector_bar(df):
    g = df.groupby("sector")["weight"].sum().sort_values()
    colors = [SECTOR_PALETTE.get(s, "#6B7280") for s in g.index]
    fig = go.Figure(go.Bar(
        y=g.index, x=g.values, orientation="h",
        marker_color=colors, marker_line_width=0,
        text=[f"{v:.1f}%" for v in g.values], textposition="outside",
        textfont=dict(color=INST["text"], size=10, family="IBM Plex Mono"),
        hovertemplate="<b>%{y}</b><br>%{x:.1f}%<extra></extra>",
    ))
    fig.update_layout(**_base(
        title="SECTOR EXPOSURE",
        xaxis=dict(ticksuffix="%"),
        height=400,
        showlegend=False,
        bargap=0.35,
    ))
    return fig


# ─── 4. P&L Bar ─────────────────────────────────────────────────────────────

def fig_pnl_waterfall(df, currency="usd"):
    col = "pnl_usd" if currency == "usd" else "pnl_ils"
    sym = "$" if currency == "usd" else "₪"
    d = df.sort_values(col, ascending=True).copy()
    colors = [INST["up"] if v >= 0 else INST["dn"] for v in d[col]]
    fig = go.Figure(go.Bar(
        y=d["display_name"], x=d[col], orientation="h",
        marker_color=colors, marker_line_width=0,
        text=[f"{sym}{v:+,.0f} ({p:+.1f}%)" for v, p in zip(d[col], d["pnl_pct"])],
        textposition="outside",
        textfont=dict(size=10, family="IBM Plex Mono", color=INST["text"]),
        hovertemplate="<b>%{y}</b><br>P&L: " + sym + "%{x:+,.0f}<extra></extra>",
    ))
    n = len(d)
    dyn_height = max(420, 30 * n + 80)
    fig.update_layout(**_base(
        title=f"P&L BY HOLDING ({sym})",
        xaxis=dict(zeroline=True, zerolinecolor=INST["accent"], zerolinewidth=1.25,
                   automargin=True),
        yaxis=dict(automargin=True),
        margin=dict(l=10, r=90, t=36, b=12),
        height=dyn_height,
        bargap=0.35,
        uniformtext=dict(mode="show", minsize=9),
    ))
    return fig


# ─── 5. Risk/Return Scatter ─────────────────────────────────────────────────

def fig_risk_return_scatter(indiv, pf_df):
    if indiv.empty:
        fig = go.Figure()
        fig.update_layout(**_base(title="RISK vs RETURN — no data"))
        return fig

    df = indiv.copy()
    df["name"] = df.index.map(lambda t: DISPLAY_NAMES.get(t, t))
    df["sector"] = df.index.map(
        lambda t: pf_df.set_index("ticker")["sector"].get(t, "Other")
        if t in pf_df["ticker"].values else "Other"
    )
    df["w"] = df.index.map(
        lambda t: pf_df.set_index("ticker")["weight"].get(t, 1)
        if t in pf_df["ticker"].values else 1
    )

    # Institutional: label only the top-weighted holdings; rest on hover
    top_by_weight = set(df.nlargest(5, "w").index.tolist())

    fig = go.Figure()
    for sec in df["sector"].unique():
        s = df[df["sector"] == sec]
        labels = [n if t in top_by_weight else "" for t, n in zip(s.index, s["name"])]
        fig.add_trace(go.Scatter(
            x=s["ann_volatility"] * 100, y=s["ann_return"] * 100,
            mode="markers+text", name=sec, text=labels,
            customdata=s["name"],
            textposition="top center",
            textfont=dict(size=9, family="Helvetica Neue", color=INST["text"]),
            marker=dict(
                size=s["w"] * 2 + 8,
                color=SECTOR_PALETTE.get(sec, "#6B7280"),
                opacity=0.8,
                line=dict(color="white", width=1),
            ),
            hovertemplate="<b>%{customdata}</b><br>Return: %{y:+.1f}%<br>Volatility: %{x:.1f}%<extra></extra>",
        ))
    fig.update_layout(**_base(
        title="RISK vs RETURN — ANNUALIZED",
        xaxis=dict(title=dict(text="Volatility %", font=dict(size=10, color=INST["dim"])),
                   ticksuffix="%", automargin=True),
        yaxis=dict(title=dict(text="Return %", font=dict(size=10, color=INST["dim"])),
                   ticksuffix="%", automargin=True,
                   zeroline=True, zerolinecolor=INST["accent"], zerolinewidth=1),
        height=440,
        margin=dict(l=50, r=30, t=60, b=50),
        legend=dict(orientation="h", yanchor="top", y=1.1, xanchor="center", x=0.5),
    ))
    return fig


# ─── 6. Correlation ─────────────────────────────────────────────────────────

def fig_correlation_heatmap(corr):
    if corr.empty:
        fig = go.Figure()
        fig.update_layout(**_base(title="CORRELATION — no data"))
        return fig

    labels = [DISPLAY_NAMES.get(t, t) for t in corr.index]
    # Institutional: monochrome diverging scale (red → white → green)
    scale = [
        [0.0, "#7F1D1D"],
        [0.35, "#F3E4E4"],
        [0.5, "#FFFFFF"],
        [0.65, "#DCEBE3"],
        [1.0, "#064E3B"],
    ]
    fig = go.Figure(go.Heatmap(
        z=corr.values, x=labels, y=labels,
        colorscale=scale, zmin=-1, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in corr.values],
        texttemplate="%{text}",
        textfont=dict(size=9, family="IBM Plex Mono"),
        hovertemplate="%{x} ↔ %{y}<br>r = %{z:.2f}<extra></extra>",
        colorbar=dict(
            title=dict(text="r", font=dict(color=INST["dim"], size=10)),
            tickfont=dict(color=INST["dim"], size=10, family="IBM Plex Mono"),
            thickness=10, len=0.7, outlinewidth=0,
        ),
    ))
    fig.update_layout(**_base(
        title="CORRELATION MATRIX",
        height=max(440, 28 * len(labels) + 120),
        margin=dict(l=120, r=40, t=50, b=120),
        xaxis=dict(tickangle=-45, automargin=True),
        yaxis=dict(automargin=True, autorange="reversed"),
    ))
    return fig


# ─── 7. Treemap ──────────────────────────────────────────────────────────────

def fig_treemap(df_in):
    # Kept for backward compat; institutional design de-emphasizes treemaps.
    df = df_in[df_in["value_ils"] > 0].copy()
    if df.empty:
        fig = go.Figure()
        fig.update_layout(**_base(title="PORTFOLIO MAP — no data"))
        return fig
    pnl_formatted = [f"{v:+.1f}" for v in df["pnl_pct"]]
    text_labels = [f"<b>{n}</b><br>{p}%" for n, p in zip(df["display_name"], pnl_formatted)]
    fig = go.Figure(go.Treemap(
        labels=df["display_name"],
        parents=[""] * len(df),
        values=df["value_ils"],
        text=text_labels, texttemplate="%{text}",
        textfont=dict(size=10, family="Helvetica Neue", color="white"),
        customdata=list(zip(pnl_formatted, df["sector"])),
        marker=dict(
            colors=df["pnl_pct"],
            colorscale=[[0, "#7F1D1D"], [0.5, "#E5E5E5"], [1, "#064E3B"]],
            cmid=0,
            line=dict(width=1.5, color="white"),
        ),
        hovertemplate="<b>%{label}</b><br>Sector: %{customdata[1]}<br>Value: ₪%{value:,.0f}<br>P&L: %{customdata[0]}%<extra></extra>",
    ))
    fig.update_layout(**_base(title="PORTFOLIO MAP", height=460))
    return fig


# ─── 8. Candlestick drill-down ──────────────────────────────────────────────

def fig_individual_detail(hist_df, ticker, cost_price=None):
    from plotly.subplots import make_subplots
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04, row_heights=[0.78, 0.22],
    )
    df = hist_df.copy()
    name = DISPLAY_NAMES.get(ticker, ticker)

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing_line_color=INST["up"], decreasing_line_color=INST["dn"],
        increasing_fillcolor=INST["up"], decreasing_fillcolor=INST["dn"],
        line=dict(width=1),
        name="Price",
    ), row=1, col=1)

    if len(df) >= 20:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["close"].rolling(20).mean(), name="MA 20",
            line=dict(color="#1E3A8A", width=1),
        ), row=1, col=1)
    if len(df) >= 50:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["close"].rolling(50).mean(), name="MA 50",
            line=dict(color=INST["dim"], width=1, dash="dash"),
        ), row=1, col=1)
    if cost_price and cost_price > 0:
        fig.add_hline(
            y=cost_price, line_dash="dot", line_color=INST["hold"], line_width=1,
            annotation_text=f"Cost ${cost_price:.2f}",
            annotation_font=dict(color=INST["hold"], size=10, family="IBM Plex Mono"),
            row=1, col=1,
        )

    vol_colors = [INST["up"] if c >= o else INST["dn"] for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"],
        marker_color=vol_colors, marker_line_width=0,
        opacity=0.5, name="Volume",
    ), row=2, col=1)

    fig.update_layout(**_base(
        title=f"{name.upper()} ({ticker})",
        height=480, xaxis_rangeslider_visible=False, showlegend=True,
        legend=dict(orientation="h", yanchor="top", y=1.06, xanchor="right", x=1),
    ))
    fig.update_xaxes(gridcolor=INST["grid"], linecolor=INST["border"])
    fig.update_yaxes(gridcolor=INST["grid"], linecolor=INST["border"])
    return fig
