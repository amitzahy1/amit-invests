"""
Portfolio above-the-fold layout — single HTML string matching
design_demos/concept2_institutional.html 1:1.

Builds: Hero strip + Positions table + Smart Insights sidebar + Allocation.
Below-fold (Performance/P&L/Risk/Drill-down) is rendered separately by Streamlit widgets.
"""

from __future__ import annotations
from datetime import datetime, timedelta

import pandas as pd

from config import DISPLAY_NAMES


import math


def minify(html: str) -> str:
    """
    Strip leading whitespace from every line and remove blank lines.
    Critical: Streamlit's markdown parser interprets 4+ space indents as code blocks
    and renders them as <pre><code> with the literal HTML visible. Removing all
    leading whitespace prevents this.
    """
    return "".join(
        line.lstrip() for line in html.splitlines() if line.strip()
    )


def render_pie(sectors_dict: dict, holdings_by_sector: dict, size: int = 260) -> str:
    """
    Render a pie chart as inline SVG with labels on each slice.
    Large slices (>=12%) show: percentage + short category name.
    Hover shows a small floating tooltip with the holdings list (CSS-only,
    self-contained inside the SVG via <style>).
    """
    total = sum(sectors_dict.values())
    if total <= 0:
        return ""
    svg_w = size
    svg_h = size
    cx = size / 2
    cy = size / 2
    r = size / 2 - 6
    label_r = r * 0.65
    angle = -90.0

    groups, labels = [], []
    for sec, val in sectors_dict.items():
        pct = val / total * 100
        sweep_deg = (val / total) * 360
        if sweep_deg <= 0:
            continue
        end_angle = angle + sweep_deg
        mid_angle = angle + sweep_deg / 2
        a1 = math.radians(angle)
        a2 = math.radians(end_angle)
        am = math.radians(mid_angle)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        lx, ly = cx + label_r * math.cos(am), cy + label_r * math.sin(am)
        large = 1 if sweep_deg > 180 else 0
        if sweep_deg >= 360 - 0.001:
            d = (f"M {cx-r} {cy} a {r} {r} 0 1 0 {2*r} 0 "
                 f"a {r} {r} 0 1 0 {-2*r} 0 z")
        else:
            d = f"M {cx} {cy} L {x1:.2f} {y1:.2f} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f} Z"
        color = SECTOR_PALETTE.get(sec, "#9CA3AF")

        holdings_list = holdings_by_sector.get(sec, [])
        n_holdings = len(holdings_list)
        def _esc(s: str) -> str:
            return (s.replace("&", "&amp;").replace('"', "&quot;")
                     .replace("<", "&lt;").replace(">", "&gt;"))
        # Native SVG <title> → browser shows a tooltip on hover. Simple, reliable.
        plain = (f"{sec} — {pct:.0f}% (${val:,.0f})\n"
                 + "\n".join(f"• {h}" for h in holdings_list))
        groups.append(
            f'<path d="{d}" fill="{color}" stroke="white" stroke-width="2" '
            f'class="pie-slice" style="cursor:pointer;">'
            f'<title>{_esc(plain)}</title>'
            f'</path>'
        )

        # Labels ONLY on large slices (>=12%) to keep the chart clean
        if pct >= 12:
            short_name = (sec
                          .replace("Consumer Discretionary", "Consumer")
                          .replace("Aerospace & Defense", "Aerospace")
                          .replace("Energy / Uranium", "Uranium")
                          .replace("Energy / Nuclear", "Nuclear")
                          .replace("Insurance (Israel)", "Insurance IL")
                          .replace("Broad Market (Israel)", "Broad IL"))
            labels.append(
                f'<text x="{lx:.2f}" y="{ly - 3:.2f}" text-anchor="middle" '
                f'font-size="15" font-weight="600" fill="white" '
                f'style="pointer-events:none;font-family:\'IBM Plex Mono\',monospace;">'
                f'{pct:.0f}%</text>'
                f'<text x="{lx:.2f}" y="{ly + 12:.2f}" text-anchor="middle" '
                f'font-size="10" fill="white" opacity="0.95" '
                f'style="pointer-events:none;font-family:\'Inter\',sans-serif;">'
                f'{short_name}</text>'
            )
        angle = end_angle

    return (
        f'<svg viewBox="0 0 {svg_w} {svg_h}" '
        f'class="pie-svg" '
        f'style="width:100%;max-width:{svg_w}px;height:auto;display:block;margin:0 auto;overflow:visible;">'
        f'{"".join(groups)}'
        f'{"".join(labels)}'
        f'</svg>'
    )


# Sector palette — institutional muted colors (match demo's tone)
SECTOR_PALETTE = {
    "Technology":             "#1D4ED8",
    "Consumer Discretionary": "#4C1D95",
    "Financials":             "#0E7490",
    "Crypto":                 "#C2410C",
    "Healthcare":             "#064E3B",
    "Broad Market":           "#374151",
    "Aerospace & Defense":    "#A16207",
    "Energy / Uranium":       "#BE185D",
    "Energy / Nuclear":       "#7C2D12",
    "Insurance (Israel)":     "#0F766E",
    "Broad Market (Israel)":  "#0F766E",
    "Other":                  "#9CA3AF",
}


def _sparkline(closes, is_up: bool) -> str:
    """30-day sparkline as inline SVG, demo style."""
    if len(closes) < 2:
        return "—"
    closes = list(closes[-30:])
    mn, mx = min(closes), max(closes)
    rng = mx - mn if mx > mn else 1
    w, h = 100, 30
    pts = " ".join(
        f"{i * w / (len(closes) - 1):.1f},{h - (c - mn) / rng * h:.1f}"
        for i, c in enumerate(closes)
    )
    color = "#047857" if is_up else "#B91C1C"
    return (
        f'<svg viewBox="0 0 {w} {h}" style="width:80px;height:28px;display:inline-block;">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="1.25" stroke-linecap="round"/>'
        f'</svg>'
    )


def _fmt_money(v: float, ccy: str = "USD") -> str:
    if pd.isna(v):
        return "—"
    if ccy == "USD":
        return f"${v:,.2f}"
    return f"₪{v:,.2f}"


def _fmt_value(v: float, ccy: str = "USD") -> str:
    if pd.isna(v):
        return "—"
    if ccy == "USD":
        return f"${v:,.0f}"
    return f"₪{v:,.0f}"


PILL_CLS = {"buy": "pill-buy", "sell": "pill-sell", "hold": "pill-hold", "new": "pill-new"}


def _vote_dots(rec: dict) -> str:
    """Show score-based dots instead of persona votes."""
    scores = rec.get("scores", {})
    if not scores:
        return ""
    bullish = sum(1 for v in scores.values() if v > 60)
    neutral = sum(1 for v in scores.values() if 40 <= v <= 60)
    bearish = sum(1 for v in scores.values() if v < 40)
    title = f"{bullish} bullish · {neutral} neutral · {bearish} bearish (6 scores)"
    dots = (
        '<span class="pos-vote-dot buy"></span>' * bullish +
        '<span class="pos-vote-dot hold"></span>' * neutral +
        '<span class="pos-vote-dot sell"></span>' * bearish
    )
    return f'<div class="pos-votes" title="{title}">{dots}</div>'


def render_above_fold(
    pf: pd.DataFrame,
    hist: dict,
    risk: dict,
    periods: dict,
    settings: dict,
    recs: dict,
    usd_ils: float,
    last_updated: str,
    current_filter: str = "all",
) -> str:
    """Return one big HTML string (hero + main grid + allocation)."""

    # ─── Hero values ───────────────────────────────────────────────────────
    tv_u = float(pf["value_usd"].sum())
    tv_i = float(pf["value_ils"].sum())
    tp_u = float(pf["pnl_usd"].sum())
    tc   = float(pf["cost_total_usd"].sum())
    tp_pct = (tp_u / tc) * 100 if tc > 0 else 0

    # Daily change — fall back to last trading day from history if periods is stale
    dp = periods.get("1d", {})
    daily_pct = dp.get("pnl_pct", 0)
    daily_usd = dp.get("pnl_usd", 0)
    market_closed = False
    daily_label = "Daily Change"
    daily_note = "USD close 16:00 ET"
    if daily_pct == 0 and daily_usd == 0:
        # Recompute from last 2 historical closes (per-holding × weight)
        try:
            recomputed_usd = 0.0
            for _, r in pf.iterrows():
                tk = r["ticker"]
                if tk in hist:
                    closes = hist[tk]["close"].dropna()
                    if len(closes) >= 2:
                        ch = float(closes.iloc[-1] - closes.iloc[-2])
                        recomputed_usd += ch * float(r["quantity"])
            if abs(recomputed_usd) > 0.01:
                # Approx % using starting-of-day value
                prior_val = tv_u - recomputed_usd
                daily_usd = recomputed_usd
                daily_pct = (recomputed_usd / prior_val * 100) if prior_val else 0
                market_closed = True
                daily_label = "Daily Change"
                daily_note = "Last trading day · market closed"
        except Exception:
            pass

    daily_cls = "up" if daily_pct >= 0 else "dn"
    pnl_cls = "up" if tp_u >= 0 else "dn"

    horizon_y = settings.get("horizon_years", 3)
    profile_name = settings.get("profile_name", "—")
    contrib_ils = settings.get("contribution_ils", 0)
    contrib_days = settings.get("contribution_frequency_days", 60)
    next_contrib_date = (datetime.now() + timedelta(days=contrib_days // 2))
    next_contrib_str = next_contrib_date.strftime("%b %d")
    days_until = (next_contrib_date - datetime.now()).days

    today_str = datetime.now().strftime("%b %d, %Y")

    daily_value_html = (
        f'<div class="hero-value {daily_cls} tab">{"+" if daily_pct >= 0 else ""}{daily_pct:.2f}%</div>'
    )
    daily_sub_text = f'{"+" if daily_usd >= 0 else ""}${daily_usd:,.2f} · {daily_note}'
    daily_sub = f'<div class="hero-sub tab {daily_cls}">{daily_sub_text}</div>'

    hero = minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Portfolio</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">As of {today_str}</div>
</div>
<div class="hero-grid">
<div class="hero-cell">
<div class="lbl">Total Value</div>
<div class="hero-value tab">${tv_u:,.0f}<span class="hero-value-suffix">USD</span></div>
<div class="hero-sub tab">₪{tv_i:,.0f}<span class="{pnl_cls}" style="margin-left:10px;font-weight:500;">{"+" if tp_u >= 0 else ""}${tp_u:,.0f} gain</span></div>
</div>
<div class="hero-cell">
<div class="lbl">Daily Change</div>
{daily_value_html}
{daily_sub}
</div>
<div class="hero-cell">
<div class="lbl">Total P&amp;L</div>
<div class="hero-value {pnl_cls} tab">{"+" if tp_pct >= 0 else ""}{tp_pct:.1f}%</div>
<div class="hero-sub {pnl_cls} tab">{"+" if tp_u >= 0 else ""}${tp_u:,.0f} vs cost basis</div>
</div>
<div class="hero-cell">
<div class="lbl">Risk Level</div>
<div class="hero-value hero-value-light">Medium</div>
<div class="hero-sub" style="white-space:nowrap;font-size:11px;">σ {risk['ann_volatility']*100:.1f}% · β {risk['beta']:.2f} · Sharpe {risk['sharpe']:.2f}</div>
</div>
<div class="hero-cell">
<div class="lbl">Cash Flow</div>
<div class="hero-value tab">₪{contrib_ils:,.0f}</div>
<div class="hero-sub">Every {contrib_days} days · next {next_contrib_str}</div>
</div>
</div>
</section>
""")

    # ─── Positions table rows ──────────────────────────────────────────────
    recs_by_ticker = {h["ticker"]: h for h in recs.get("holdings", [])}

    rows_html = []
    for _, r in pf.iterrows():
        tk = r["ticker"]
        is_up = r["pnl_pct"] >= 0
        pnl_cls_row = "up" if is_up else "dn"
        rec = recs_by_ticker.get(tk, {})
        verdict = (rec.get("verdict") or "").lower()
        conf = int(rec.get("conviction", 0))
        votes_html = _vote_dots(rec)

        if verdict:
            signal_html = f'<span class="pill {PILL_CLS.get(verdict, "pill-hold")}">{verdict.upper()} {conf}%</span>'
        else:
            signal_html = '<span style="font-size:11px;color:var(--text-mute);">—</span>'

        is_il = r.get("is_israeli", False)
        if is_il:
            cost_str  = f"₪{r['cost_price']:,.0f}" if pd.notna(r['cost_price']) else "—"
            price_str = f"₪{r['live_price']:,.0f}"
        else:
            cost_str  = f"${r['cost_price']:,.2f}" if pd.notna(r['cost_price']) else "—"
            price_str = f"${r['live_price']:,.2f}"

        # Sparkline from real history
        spark = "—"
        if tk in hist:
            closes = hist[tk]["close"].tolist()
            spark = _sparkline(closes, is_up)

        # Friendly ticker display: strip .TA suffix for Israeli ETFs (and use prefix for 5108)
        display_ticker = tk
        if tk == "5108.TA":
            display_ticker = "ISR-INS"
        elif tk.endswith(".TA"):
            display_ticker = tk.replace(".TA", "")  # e.g. KSM-F34.TA → KSM-F34

        rows_html.append(
            f'<tr>'
            f'<td><div class="pos-ticker-row"><div class="pos-ticker">{display_ticker}</div>{votes_html}</div>'
            f'<div class="pos-name">{r["display_name"]}</div></td>'
            f'<td class="r">{r["quantity"]:g}</td>'
            f'<td class="r">{cost_str}</td>'
            f'<td class="r pos-strong">{price_str}</td>'
            f'<td class="r pos-strong">${r["value_usd"]:,.0f}</td>'
            f'<td class="r pos-pct {pnl_cls_row}">{"+" if is_up else ""}{r["pnl_pct"]:.1f}%</td>'
            f'<td class="c">{spark}</td>'
            f'<td class="r">{signal_html}</td>'
            f'</tr>'
        )

    positions_count = len(pf)

    # ─── Smart Insights items ──────────────────────────────────────────────
    holdings_recs = recs.get("holdings", [])
    new_ideas = recs.get("new_ideas", [])
    strong_buys = sum(
        1 for h in holdings_recs
        if (h.get("verdict") or "").lower() == "buy" and int(h.get("conviction", 0)) >= 75
    )
    strong_sells = sum(
        1 for h in holdings_recs
        if (h.get("verdict") or "").lower() == "sell" and int(h.get("conviction", 0)) >= 60
    )

    # Build top insights: new ideas first, then highest-conviction movers
    insight_items = []
    for ni in new_ideas[:3]:
        insight_items.append({
            "t": ni.get("ticker", ""),
            "name": ni.get("name", ni.get("ticker", "")),
            "verdict": "NEW",
            "conf": int(ni.get("conviction", 0)),
            "rationale": ni.get("rationale", ""),
            "cls": "pill-new",
        })
    sorted_h = sorted(holdings_recs, key=lambda h: -int(h.get("conviction", 0)))
    for h in sorted_h:
        if any(i["t"] == h["ticker"] for i in insight_items):
            continue
        v = (h.get("verdict") or "").lower()
        if v in ("buy", "sell") and int(h.get("conviction", 0)) >= 70:
            first_rat = h.get("rationale", "")
            insight_items.append({
                "t": h["ticker"],
                "name": DISPLAY_NAMES.get(h["ticker"], h["ticker"]),
                "verdict": v.upper(),
                "conf": int(h.get("conviction", 0)),
                "rationale": first_rat,
                "cls": "pill-buy" if v == "buy" else "pill-sell",
            })
        if len(insight_items) >= 5:
            break

    insights_html = ""
    for it in insight_items:
        rat = it["rationale"][:200] + ("…" if len(it["rationale"]) > 200 else "")
        is_hebrew = any('\u0590' <= c <= '\u05FF' for c in rat[:80])
        rtl_attr = ' dir="rtl"' if is_hebrew else ''
        insights_html += (
            f'<div class="insight-item">'
            f'<div class="insight-row">'
            f'<div class="insight-ticker"><b>{it["t"]}</b>'
            f'<span class="pill {it["cls"]}">{it["verdict"]} · {it["conf"]}%</span>'
            f'</div></div>'
            f'<div class="insight-rationale"{rtl_attr}>{rat}</div>'
            f'</div>'
        )

    reviewed_when = recs.get("updated", "")[:16].replace("T", " ")

    # ─── Sector allocation ─────────────────────────────────────────────────
    sector_weights = pf.groupby("sector")["value_usd"].sum().sort_values(ascending=False)
    total_w = float(sector_weights.sum())

    # Group holdings per sector for hover tooltip
    holdings_by_sector = {}
    for _, r in pf.sort_values("value_usd", ascending=False).iterrows():
        sec = r["sector"]
        tk = r["ticker"]
        # Friendly ticker for Israeli
        if tk == "5108.TA":
            tk_disp = "ISR-INS"
        elif tk.endswith(".TA"):
            tk_disp = tk.replace(".TA", "")
        else:
            tk_disp = tk
        holdings_by_sector.setdefault(sec, []).append(f"{tk_disp} (${r['value_usd']:,.0f})")

    bar_segments = ""
    legend_items = ""
    for sec, val in sector_weights.items():
        pct = (val / total_w * 100) if total_w else 0
        color = SECTOR_PALETTE.get(sec, "#9CA3AF")
        label = f"{pct:.1f}%" if pct >= 5 else ""
        # Tooltip: sector + % + holdings list
        holdings_in_sector = holdings_by_sector.get(sec, [])
        holdings_str = ", ".join(holdings_in_sector)
        tooltip = f"{sec}: {pct:.1f}% — ${val:,.0f} | {holdings_str}".replace('"', '&quot;')
        bar_segments += (
            f'<div class="alloc-bar-seg" style="width:{pct}%; background:{color};" '
            f'title="{tooltip}">{label}</div>'
        )
        # Legend with holdings preview
        holdings_preview = ", ".join(h.split(" ")[0] for h in holdings_in_sector[:3])
        if len(holdings_in_sector) > 3:
            holdings_preview += f" +{len(holdings_in_sector) - 3}"
        legend_items += (
            f'<div class="alloc-legend-item" title="{tooltip}">'
            f'<span class="alloc-legend-swatch" style="background:{color};"></span>'
            f'<span class="alloc-legend-name">{sec} <span style="color:var(--text-mute);font-size:11px;">· {holdings_preview}</span></span>'
            f'<span class="alloc-legend-val">{pct:.1f}%</span>'
            f'</div>'
        )
    sector_count = len(sector_weights)

    upcoming_html = (
        f'<div class="upcoming-row">'
        f'<div>'
        f'<div class="label">Next contribution</div>'
        f'<div class="meta">{next_contrib_date.strftime("%b %d, %Y")} · ₪{contrib_ils:,.0f}</div>'
        f'</div>'
        f'<span class="badge">{max(0, days_until)}d</span>'
        f'</div>'
        f'<div class="upcoming-row">'
        f'<div>'
        f'<div class="label">Next AI run</div>'
        f'<div class="meta">Tomorrow 16:35 IDT</div>'
        f'</div>'
        f'<span class="badge">~24h</span>'
        f'</div>'
    )

    # ─── Pie chart for Allocation (labels on slices; no legend) ────────────
    pie_svg = render_pie(sector_weights.to_dict(), holdings_by_sector, size=200)
    pie_legend_html = ""  # kept for backward-compat; now unused (was rendered separately)

    # Active filter pill class
    fl = (current_filter or "all").lower()
    pill_cls = {f: "filter-pill active" if f == fl else "filter-pill"
                for f in ("all", "equities", "etfs", "crypto")}

    # ─── Main grid ─────────────────────────────────────────────────────────
    main = minify(f"""
<main class="main-grid">
<div class="col-positions">
<div class="sect-head">
<div>
<h2>Positions</h2>
<div class="sect-sub">Sorted by market value · All-time P&amp;L</div>
</div>
<div class="filter-group">
<a class="{pill_cls['all']}" href="?filter=all" target="_self">All</a>
<a class="{pill_cls['equities']}" href="?filter=equities" target="_self">Equities</a>
<a class="{pill_cls['etfs']}" href="?filter=etfs" target="_self">ETFs</a>
<a class="{pill_cls['crypto']}" href="?filter=crypto" target="_self">Crypto</a>
</div>
</div>
<div class="positions-card">
<table class="positions-table">
<thead><tr>
<th>Ticker</th>
<th class="r">Qty</th>
<th class="r">Cost</th>
<th class="r">Last</th>
<th class="r">Value</th>
<th class="r">P&amp;L %</th>
<th class="c">7-Day</th>
<th class="r">Signal</th>
</tr></thead>
<tbody>{"".join(rows_html)}</tbody>
</table>
<div class="positions-footer">
<div>Showing {positions_count} of {len(pf)} positions{f' · filter: {fl}' if fl != 'all' else ''}</div>
<a href="?filter=all">{'View all →' if fl != 'all' else 'All shown'}</a>
</div>
</div>
</div>
<aside class="col-insights">
<div class="insights-wrap">
<div class="alloc-mini-card">
<div class="alloc-mini-head">
<div class="lbl">Allocation by Sector</div>
<div class="alloc-mini-meta">{sector_count} sectors · ${tv_u:,.0f}</div>
</div>
<div class="alloc-mini-pie" id="alloc-mini-pie">{pie_svg}</div>
<div id="alloc-pie-tooltip" class="alloc-pie-tooltip"></div>
</div>
<div class="insights-card">
<div class="insights-head">
<h2>Smart Insights</h2>
<div class="insights-tag">AI · Gemini</div>
</div>
<div class="insights-summary">
Generated {reviewed_when}. Scoring engine analyzed {len(holdings_recs)} holdings. {strong_buys} strong buys, {strong_sells} sells, {len(new_ideas)} new ideas.
</div>
<div class="insights-list">{insights_html}</div>
<a class="insights-cta" href="/recommendations">Open full recommendations →</a>
</div>
<div class="upcoming-card">
<div class="lbl">Upcoming</div>
<div class="upcoming-list">{upcoming_html}</div>
</div>
</div>
</aside>
</main>
""")
    return hero + main


def render_footer(usd_ils: float) -> str:
    return minify(f"""
<footer class="page-footer">
<div>AMIT CAPITAL · Yahoo Finance · USD/ILS {usd_ils:.4f} · Market commentary, not financial advice.</div>
<div class="right">Helvetica Neue + IBM Plex Mono</div>
</footer>
""")
