"""
Recommendations — clean card grid with click-to-modal detail view.

Layout order:
  1. Hero strip (stats)
  2. Strong BUY holdings (highest conviction first)
  3. New Ideas (outside portfolio)
  4. SELL signals
  5. Everything else (HOLD + lower conviction BUY)
  6. Filter slider
  7. Ideas accuracy scorecard

Click any card → modal with full analysis: rationale, score breakdown, sector.
"""

from _bootstrap import inject_css, inject_header, handle_actions, load_json, minify

import html as _html
from datetime import datetime, timezone
from pathlib import Path
import streamlit as st
from config import DISPLAY_NAMES, SECTOR_MAP, ASSET_TYPE_MAP

inject_css()
inject_header("recommendations")
handle_actions()

recs = load_json("recommendations.json")
if not recs:
    st.markdown("""
    <div class="below-section">
      <div class="sect-head"><div>
        <h2>Recommendations</h2>
        <div class="sect-sub">No recommendations yet</div>
      </div></div>
      <div style="border:1px solid var(--hair);padding:24px;background:var(--bg-softer);font-size:13px;">
        Click <b>Run analysis →</b> in the topbar to generate recommendations.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ─── Parse ───────────────────────────────────────────────────────────────────
profile = recs.get("profile_name", "—")
holdings = recs.get("holdings", [])
new_ideas = recs.get("new_ideas", [])
updated_raw = recs.get("updated") or ""
updated = updated_raw[:16].replace("T", " ") if updated_raw else "—"
is_dry_run = bool(recs.get("dry_run", False))

# Freshness
_freshness_html = ""
if updated_raw:
    try:
        _ts = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        _age_h = (datetime.now(timezone.utc) - _ts).total_seconds() / 3600
        if _age_h < 12:
            _fl, _fc = f"{int(_age_h)}h ago" if _age_h >= 1 else "Just now", "fresh-green"
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

VERDICT_CLS = {"buy": "pill-buy", "sell": "pill-sell", "hold": "pill-hold"}
VERDICT_COLOR = {"buy": "#047857", "sell": "#B91C1C", "hold": "#92400E"}

# Load scoring weights
_sw_settings = load_json("settings.json") or {}
_SCORE_WEIGHTS = _sw_settings.get("scoring_weights", {
    "quality": 30, "valuation": 25, "risk": 20, "macro": 15, "sentiment": 5, "technical": 5,
})
_SCORE_ORDER = sorted(
    ["quality", "valuation", "risk", "macro", "sentiment", "technical"],
    key=lambda k: -_SCORE_WEIGHTS.get(k, 0),
)

SCORE_LABELS = {"quality": "Quality", "valuation": "Valuation", "risk": "Risk",
                "macro": "Macro", "sentiment": "Sentiment", "technical": "Trend"}
SCORE_ICONS = {"quality": "🏛️", "valuation": "💰", "risk": "🛡️",
               "macro": "🌍", "sentiment": "📊", "technical": "📈"}


def _score_color(val: int) -> str:
    if val >= 65: return "#047857"
    elif val >= 40: return "#b45309"
    return "#b91c1c"


def _score_bars_html(scores: dict) -> str:
    if not scores:
        return ""
    rows = []
    for key in _SCORE_ORDER:
        val = scores.get(key, 50)
        color = _score_color(val)
        label = SCORE_LABELS.get(key, key)
        weight = _SCORE_WEIGHTS.get(key, 0)
        whtml = f'<span class="score-weight">{weight}%</span>' if weight else ''
        rows.append(
            f'<div class="score-row">'
            f'<span class="score-label">{label}{whtml}</span>'
            f'<div class="score-bar"><div class="score-fill" style="width:{val}%;background:{color};"></div></div>'
            f'<span class="score-val" style="color:{color};">{val}</span>'
            f'</div>')
    return f'<div class="score-bars">{"".join(rows)}</div>'


def _conviction_bar(pct: int, verdict: str) -> str:
    color = VERDICT_COLOR.get(verdict, "#6B7280")
    pct = max(0, min(100, int(pct)))
    return (f'<div class="conv-bar" title="Conviction {pct}%">'
            f'<div class="conv-bar-fill" style="width:{pct}%;background:{color};"></div></div>')


def _sector_of(ticker: str) -> str:
    return SECTOR_MAP.get(ticker, "Other")


# ─── Score history sparkline ─────────────────────────────────────────────────

@st.cache_data(ttl=600)
def _load_score_history(ticker: str, days: int = 30) -> list:
    """Load score history for a ticker — returns list of weighted averages over time."""
    try:
        from score_history import get_score_trend
        entries = get_score_trend(ticker, days=days)
        w = _SCORE_WEIGHTS
        total_w = sum(w.values()) or 1
        series = []
        for e in entries:
            s = e.get("scores") or {}
            if not s:
                continue
            wavg = sum(s.get(k, 50) * w.get(k, 0) for k in s) / total_w
            series.append(round(wavg, 1))
        return series
    except Exception:
        return []


def _sparkline_svg(values: list, width: int = 60, height: int = 18) -> str:
    """Tiny inline SVG sparkline for a list of values (0-100)."""
    if not values or len(values) < 2:
        return ""
    n = len(values)
    min_v = min(values)
    max_v = max(values)
    rng = max(1, max_v - min_v)
    pts = []
    for i, v in enumerate(values):
        x = (i / (n - 1)) * width
        y = height - ((v - min_v) / rng) * height
        pts.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(pts)
    last = values[-1]
    first = values[0]
    color = "#047857" if last >= first else "#b91c1c"
    last_x = width
    last_y = height - ((last - min_v) / rng) * height
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="display:inline-block;vertical-align:middle;" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2" fill="{color}"/>'
        f'</svg>'
    )


# ─── Detail Modal ────────────────────────────────────────────────────────────

@st.dialog("Analysis", width="large")
def _show_detail(item: dict, is_idea: bool = False):
    """Modal with full analysis: rationale, score breakdown, sector context."""
    tk = item.get("ticker", "")
    name = DISPLAY_NAMES.get(tk, item.get("name", tk))
    v = (item.get("verdict") or ("buy" if is_idea else "hold")).lower()
    c = int(item.get("conviction", 0))
    scores = item.get("scores", {})
    rationale = item.get("rationale", "")
    sector = _sector_of(tk) if not is_idea else "New Idea"
    asset_type = ASSET_TYPE_MAP.get(tk, "")
    if is_idea and not asset_type:
        asset_type = "Suggested"

    verdict_label = {"buy": "BUY — Recommended", "sell": "SELL — Reduce/Exit",
                     "hold": "HOLD — No Action"}.get(v, v.upper())
    verdict_color = VERDICT_COLOR.get(v, "#6B7280")

    # Header
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;">'
        f'<div>'
        f'<div style="font-size:28px;font-weight:700;font-family:\'IBM Plex Mono\',monospace;color:var(--text);">{tk}</div>'
        f'<div style="font-size:14px;color:var(--text-dim);margin-top:2px;">{_html.escape(name)}</div>'
        f'<div style="font-size:11px;color:var(--text-mute);text-transform:uppercase;letter-spacing:0.1em;margin-top:4px;">{sector} · {asset_type}</div>'
        f'</div>'
        f'<div style="text-align:right;">'
        f'<div style="font-size:14px;font-weight:700;color:{verdict_color};letter-spacing:0.04em;">{verdict_label}</div>'
        f'<div style="font-size:32px;font-weight:700;color:var(--text);font-family:\'IBM Plex Mono\',monospace;">{c}%</div>'
        f'<div style="font-size:10px;color:var(--text-mute);">CONVICTION</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Rationale
    if rationale:
        is_hebrew = any('\u0590' <= ch <= '\u05FF' for ch in rationale[:80])
        rtl = ' dir="rtl" style="text-align:right;"' if is_hebrew else ''
        st.markdown(
            f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;'
            f'padding:16px 20px;margin-bottom:20px;font-size:14px;line-height:1.8;">'
            f'<div style="font-size:11px;font-weight:600;color:var(--text-dim);text-transform:uppercase;'
            f'letter-spacing:0.12em;margin-bottom:8px;">Analysis</div>'
            f'<div{rtl}>{_html.escape(rationale)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Wall Street Analyst Consensus widget
    analyst = item.get("analyst_consensus", {})
    if analyst and (analyst.get("buy") or analyst.get("hold") or analyst.get("sell")):
        ab = analyst.get("buy", 0)
        ah = analyst.get("hold", 0)
        asl = analyst.get("sell", 0)
        total = max(1, ab + ah + asl)
        buy_pct = ab / total * 100
        hold_pct = ah / total * 100
        sell_pct = asl / total * 100

        # Determine consensus label
        if buy_pct >= 70:
            consensus_label, consensus_color = "Strong Buy", "#047857"
        elif buy_pct >= 50:
            consensus_label, consensus_color = "Buy", "#16a34a"
        elif sell_pct >= 30:
            consensus_label, consensus_color = "Sell", "#b91c1c"
        elif sell_pct >= 15:
            consensus_label, consensus_color = "Underperform", "#ea580c"
        else:
            consensus_label, consensus_color = "Hold", "#b45309"

        # Price target upside
        target = analyst.get("target")
        price = analyst.get("price")
        target_html = ""
        if target and price and price > 0:
            upside = ((target / price) - 1) * 100
            up_color = "#047857" if upside > 0 else "#b91c1c"
            target_html = (
                f'<div style="margin-top:10px;padding-top:10px;border-top:1px solid #e2e8f0;'
                f'display:flex;justify-content:space-between;align-items:center;font-size:12px;">'
                f'<span style="color:var(--text-dim);">Analyst price target</span>'
                f'<span><b style="font-family:\'IBM Plex Mono\',monospace;">${target:.0f}</b> '
                f'<span style="color:{up_color};font-weight:600;">({upside:+.1f}%)</span></span>'
                f'</div>'
            )

        st.markdown(
            f'<div style="border:1px solid #e2e8f0;border-radius:8px;padding:16px 20px;'
            f'margin-bottom:20px;background:#fafbfc;">'
            # Header
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;">'
            f'<div>'
            f'<div style="font-size:11px;font-weight:600;color:var(--text-dim);'
            f'text-transform:uppercase;letter-spacing:0.12em;">Wall Street Consensus</div>'
            f'<div style="font-size:11px;color:var(--text-mute);margin-top:2px;">{total} analysts covering</div>'
            f'</div>'
            f'<div style="font-size:14px;font-weight:700;color:{consensus_color};'
            f'letter-spacing:0.03em;">{consensus_label}</div>'
            f'</div>'
            # Stacked bar
            f'<div style="display:flex;height:10px;border-radius:5px;overflow:hidden;background:#f0f0f0;margin-bottom:8px;">'
            f'<div style="width:{buy_pct}%;background:#047857;" title="Buy: {ab} ({buy_pct:.0f}%)"></div>'
            f'<div style="width:{hold_pct}%;background:#d97706;" title="Hold: {ah} ({hold_pct:.0f}%)"></div>'
            f'<div style="width:{sell_pct}%;background:#b91c1c;" title="Sell: {asl} ({sell_pct:.0f}%)"></div>'
            f'</div>'
            # Counts row
            f'<div style="display:flex;justify-content:space-between;font-size:11px;font-family:\'IBM Plex Mono\',monospace;">'
            f'<span style="color:#047857;font-weight:600;">● BUY {ab} ({buy_pct:.0f}%)</span>'
            f'<span style="color:#d97706;font-weight:600;">● HOLD {ah} ({hold_pct:.0f}%)</span>'
            f'<span style="color:#b91c1c;font-weight:600;">● SELL {asl} ({sell_pct:.0f}%)</span>'
            f'</div>'
            f'{target_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Social Sentiment (Twitter/X via Perplexity) — optional
    social = item.get("social_sentiment", {})
    if social and social.get("sentiment_score") is not None:
        s_score = social.get("sentiment_score", 50)
        label = social.get("label", "neutral").lower()
        themes = social.get("top_themes", [])
        accounts = social.get("key_accounts", [])

        if label == "bullish":
            s_color, s_emoji = "#047857", "🐂"
        elif label == "bearish":
            s_color, s_emoji = "#b91c1c", "🐻"
        else:
            s_color, s_emoji = "#b45309", "⚖️"

        themes_html = ""
        if themes:
            themes_html = (
                '<div style="margin-top:10px;padding-top:10px;border-top:1px solid #e2e8f0;">'
                '<div style="font-size:10px;color:var(--text-mute);text-transform:uppercase;'
                'letter-spacing:0.1em;margin-bottom:6px;">Top discussion themes</div>'
            )
            for t in themes:
                themes_html += (
                    f'<div style="font-size:12px;color:#334155;padding:2px 0;">'
                    f'<span style="color:{s_color};margin-right:6px;">▸</span>'
                    f'{_html.escape(str(t))}</div>'
                )
            themes_html += '</div>'

        accounts_html = ""
        if accounts:
            accounts_html = (
                f'<div style="margin-top:6px;font-size:11px;color:var(--text-dim);">'
                f'Tracked by: <b>{", ".join(_html.escape(str(a)) for a in accounts)}</b>'
                f'</div>'
            )

        st.markdown(
            f'<div style="border:1px solid #e2e8f0;border-radius:8px;padding:16px 20px;'
            f'margin-bottom:20px;background:#fafbfc;">'
            # Header
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;">'
            f'<div style="display:flex;align-items:center;gap:8px;">'
            f'<span style="font-size:18px;">{s_emoji}</span>'
            f'<div>'
            f'<div style="font-size:11px;font-weight:600;color:var(--text-dim);'
            f'text-transform:uppercase;letter-spacing:0.12em;">Social Sentiment (X + News)</div>'
            f'<div style="font-size:10px;color:var(--text-mute);margin-top:2px;">'
            f'Past 48 hours · Perplexity</div>'
            f'</div></div>'
            f'<div style="text-align:right;">'
            f'<div style="font-size:22px;font-weight:700;color:{s_color};'
            f'font-family:\'IBM Plex Mono\',monospace;">{s_score}</div>'
            f'<div style="font-size:11px;font-weight:600;color:{s_color};'
            f'text-transform:uppercase;letter-spacing:0.08em;">{label}</div>'
            f'</div>'
            f'</div>'
            # Score bar
            f'<div style="height:6px;background:#f0f0f0;border-radius:3px;overflow:hidden;">'
            f'<div style="width:{s_score}%;height:100%;background:{s_color};border-radius:3px;"></div>'
            f'</div>'
            f'{themes_html}'
            f'{accounts_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Position Sizing recommendation
    position = item.get("position_sizing", {})
    if position and position.get("target_pct") is not None:
        action = position.get("action", "hold")
        target = position.get("target_pct", 0)
        delta = position.get("delta_pct", 0)
        reason = position.get("reason", "")

        action_config = {
            "add": ("ADD TO POSITION", "#047857", "📈"),
            "add_small": ("ADD SMALL", "#047857", "➕"),
            "hold": ("HOLD — No Change", "#b45309", "⏸️"),
            "reduce": ("REDUCE", "#b91c1c", "📉"),
            "reduce_small": ("TRIM SMALL", "#b91c1c", "➖"),
            "exit": ("EXIT POSITION", "#b91c1c", "🚪"),
        }
        a_label, a_color, a_icon = action_config.get(action, ("HOLD", "#b45309", "⏸️"))
        delta_sign = "+" if delta > 0 else ""

        st.markdown(
            f'<div style="border:1px solid #e2e8f0;border-left:4px solid {a_color};'
            f'border-radius:8px;padding:16px 20px;margin-bottom:20px;background:#fafbfc;">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;">'
            f'<div style="display:flex;align-items:center;gap:8px;">'
            f'<span style="font-size:18px;">{a_icon}</span>'
            f'<span style="font-size:11px;font-weight:700;color:var(--text-dim);'
            f'text-transform:uppercase;letter-spacing:0.12em;">Position Sizing</span>'
            f'</div>'
            f'<span style="font-size:13px;font-weight:700;color:{a_color};'
            f'letter-spacing:0.03em;">{a_label}</span>'
            f'</div>'
            f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:10px;">'
            f'<div><div style="font-size:10px;color:var(--text-mute);text-transform:uppercase;'
            f'letter-spacing:0.08em;">Target</div>'
            f'<div style="font-size:18px;font-weight:700;color:var(--text);'
            f'font-family:\'IBM Plex Mono\',monospace;">{target:.1f}%</div></div>'
            f'<div><div style="font-size:10px;color:var(--text-mute);text-transform:uppercase;'
            f'letter-spacing:0.08em;">Change</div>'
            f'<div style="font-size:18px;font-weight:700;color:{a_color};'
            f'font-family:\'IBM Plex Mono\',monospace;">{delta_sign}{delta:.1f}pp</div></div>'
            f'<div><div style="font-size:10px;color:var(--text-mute);text-transform:uppercase;'
            f'letter-spacing:0.08em;">Max Allowed</div>'
            f'<div style="font-size:18px;font-weight:700;color:var(--text-dim);'
            f'font-family:\'IBM Plex Mono\',monospace;">{position.get("max_allowed", 0)}%</div></div>'
            f'</div>'
            f'<div style="font-size:12px;color:#475569;padding-top:8px;'
            f'border-top:1px solid #e2e8f0;">{_html.escape(reason)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Exit Triggers (stop-loss / take-profit)
    triggers = item.get("exit_triggers", {})
    if triggers and triggers.get("stop_loss_price"):
        sl_price = triggers.get("stop_loss_price", 0)
        sl_pct = triggers.get("stop_loss_pct", 0)
        tp_price = triggers.get("take_profit_price", 0)
        tp_pct = triggers.get("take_profit_pct", 0)
        trailing = triggers.get("trailing_enabled", False)
        re_eval = triggers.get("re_evaluate_if", "")

        st.markdown(
            f'<div style="border:1px solid #e2e8f0;border-radius:8px;padding:16px 20px;'
            f'margin-bottom:20px;background:#fafbfc;">'
            f'<div style="font-size:11px;font-weight:700;color:var(--text-dim);'
            f'text-transform:uppercase;letter-spacing:0.12em;margin-bottom:10px;">'
            f'Exit Triggers {"(trailing)" if trailing else ""}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">'
            # Stop-loss
            f'<div style="padding:10px 14px;background:#fef2f2;border-left:3px solid #b91c1c;border-radius:4px;">'
            f'<div style="font-size:10px;color:#991b1b;text-transform:uppercase;letter-spacing:0.1em;'
            f'font-weight:600;">🛑 Stop Loss</div>'
            f'<div style="font-size:20px;font-weight:700;color:#b91c1c;'
            f'font-family:\'IBM Plex Mono\',monospace;margin-top:4px;">${sl_price:,.2f}</div>'
            f'<div style="font-size:11px;color:#991b1b;">{sl_pct:+.1f}% from current</div>'
            f'</div>'
            # Take-profit
            f'<div style="padding:10px 14px;background:#ecfdf5;border-left:3px solid #047857;border-radius:4px;">'
            f'<div style="font-size:10px;color:#065f46;text-transform:uppercase;letter-spacing:0.1em;'
            f'font-weight:600;">🎯 Take Profit</div>'
            f'<div style="font-size:20px;font-weight:700;color:#047857;'
            f'font-family:\'IBM Plex Mono\',monospace;margin-top:4px;">${tp_price:,.2f}</div>'
            f'<div style="font-size:11px;color:#065f46;">+{tp_pct:.1f}% from current</div>'
            f'</div>'
            f'</div>'
            f'<div style="font-size:11px;color:#64748b;padding-top:12px;margin-top:10px;'
            f'border-top:1px solid #e2e8f0;font-style:italic;">'
            f'💡 Re-evaluate if: {_html.escape(re_eval)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Score breakdown — card-per-category with full detail
    details = item.get("score_details", {})
    if scores:
        st.markdown(
            '<div style="font-size:11px;font-weight:700;color:var(--text-dim);text-transform:uppercase;'
            'letter-spacing:0.12em;margin:8px 0 12px;">Score Breakdown — 6 categories weighted by your strategy</div>',
            unsafe_allow_html=True,
        )

        # Category descriptions for context
        CAT_DESCRIPTIONS = {
            "quality": "How strong is the underlying business? ROE, margins, debt, growth.",
            "valuation": "Is the stock cheap or expensive? P/E vs sector, PEG, analyst targets.",
            "risk": "How safe is this position in your portfolio? Concentration, beta, sector weight.",
            "macro": "Does the economic environment favor this asset? Rates, VIX, yield curve.",
            "sentiment": "What does Wall Street think? Analyst consensus (Buy/Hold/Sell counts).",
            "technical": "What is the price trend saying? MA50/200 crossovers, RSI, momentum.",
        }

        for key in _SCORE_ORDER:
            val = scores.get(key, 50)
            color = _score_color(val)
            icon = SCORE_ICONS.get(key, "")
            label = SCORE_LABELS.get(key, key)
            weight = _SCORE_WEIGHTS.get(key, 0)
            signal = "Bullish" if val > 60 else "Bearish" if val < 40 else "Neutral"
            bar_w = max(2, val)
            description = CAT_DESCRIPTIONS.get(key, "")

            # Weight's contribution to final verdict
            contribution = (val * weight / 100) if weight else 0

            # Full category card
            card_html = (
                f'<div style="border:1px solid #e2e8f0;border-left:4px solid {color};'
                f'border-radius:6px;padding:14px 18px;margin-bottom:10px;background:#fafbfc;">'
                # Header row
                f'<div style="display:flex;align-items:center;justify-content:space-between;'
                f'gap:12px;margin-bottom:8px;">'
                f'<div style="display:flex;align-items:center;gap:10px;">'
                f'<span style="font-size:20px;">{icon}</span>'
                f'<div>'
                f'<div style="font-size:15px;font-weight:700;color:var(--text);">{label}</div>'
                f'<div style="font-size:11px;color:var(--text-mute);">{description}</div>'
                f'</div>'
                f'</div>'
                f'<div style="text-align:right;">'
                f'<div style="font-size:22px;font-weight:700;color:{color};'
                f'font-family:\'IBM Plex Mono\',monospace;line-height:1;">{val}</div>'
                f'<div style="font-size:10px;color:{color};font-weight:600;'
                f'text-transform:uppercase;letter-spacing:0.1em;margin-top:2px;">{signal}</div>'
                f'</div>'
                f'</div>'
                # Bar + weight info
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
                f'<div style="flex:1;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden;">'
                f'<div style="width:{bar_w}%;height:100%;background:{color};border-radius:3px;"></div>'
                f'</div>'
                f'<span style="font-size:10px;color:var(--text-mute);white-space:nowrap;">'
                f'Weight {weight}% · Contributes {contribution:.1f} pts</span>'
                f'</div>'
            )

            # Reasoning bullets
            reasons = details.get(key, [])
            if reasons:
                bullets_html = (
                    '<div style="border-top:1px solid #e2e8f0;padding-top:10px;">'
                    '<div style="font-size:10px;font-weight:600;color:var(--text-mute);'
                    'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">Why this score?</div>'
                )
                for r in reasons:
                    bullets_html += (
                        f'<div style="font-size:13px;color:#334155;padding:3px 0;line-height:1.6;">'
                        f'<span style="color:{color};font-weight:700;margin-right:6px;">▸</span>'
                        f'{_html.escape(r)}'
                        f'</div>'
                    )
                bullets_html += '</div>'
                card_html += bullets_html

            card_html += '</div>'
            st.markdown(card_html, unsafe_allow_html=True)

        # Weighted average
        total_w = sum(_SCORE_WEIGHTS.get(k, 0) for k in scores)
        if total_w > 0:
            wavg = sum(scores.get(k, 50) * _SCORE_WEIGHTS.get(k, 0) for k in scores) / total_w
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
                f'margin-top:12px;padding-top:12px;border-top:2px solid var(--hair);">'
                f'<div style="font-size:12px;font-weight:600;color:var(--text);">Weighted Average</div>'
                f'<div style="font-size:20px;font-weight:700;color:{_score_color(int(wavg))};'
                f'font-family:\'IBM Plex Mono\',monospace;">{wavg:.0f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ─── Card renderer (no expander, just a button) ─────────────────────────────

def _render_card(item: dict, accent: str, is_idea: bool = False) -> None:
    """Render a card + 'Details' button that opens the modal."""
    tk = item.get("ticker", "")
    name = DISPLAY_NAMES.get(tk, item.get("name", tk))
    v = (item.get("verdict") or ("buy" if is_idea else "hold")).lower()
    c = int(item.get("conviction", 0))
    scores = item.get("scores", {})
    sector = _sector_of(tk) if not is_idea else ""

    pill_cls = VERDICT_CLS.get(v, "pill-hold") if not is_idea else "pill-new"
    pill_text = f"{v.upper()} {c}" if not is_idea else f"NEW {c}"

    # Score history sparkline (30-day trend)
    history = _load_score_history(tk, days=30)
    sparkline_html = ""
    if len(history) >= 2:
        spark = _sparkline_svg(history)
        first = history[0]
        last = history[-1]
        diff = last - first
        diff_color = "#047857" if diff >= 0 else "#b91c1c"
        diff_sign = "+" if diff >= 0 else ""
        sparkline_html = (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'gap:6px;margin:4px 0 -2px;font-size:10px;color:var(--text-mute);">'
            f'<span style="text-transform:uppercase;letter-spacing:0.08em;">30d</span>'
            f'{spark}'
            f'<span style="color:{diff_color};font-weight:600;font-family:\'IBM Plex Mono\',monospace;">'
            f'{diff_sign}{diff:.0f}</span>'
            f'</div>'
        )

    st.markdown(
        f'<div class="mini-card mini-card-{accent}">'
        f'  <div class="mini-card-top">'
        f'    <div class="mini-ticker mono">{tk}</div>'
        f'    <span class="pill {pill_cls}">{pill_text}</span>'
        f'  </div>'
        f'  <div class="mini-name txt-dim">{_html.escape(name)}</div>'
        f'  {"<div class=\"priority-sector txt-mute\">" + sector + "</div>" if sector else ""}'
        f'  {_score_bars_html(scores)}'
        f'  {sparkline_html}'
        f'  {_conviction_bar(c, v)}'
        f'</div>',
        unsafe_allow_html=True,
    )
    if st.button("View analysis", key=f"btn_{tk}_{accent}", use_container_width=True):
        _show_detail(item, is_idea)


# ═══════════════════════════════════════════════════════════════════════════════
# HERO
# ═══════════════════════════════════════════════════════════════════════════════
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
    Generated {updated} {_freshness_html}
  </div>
</section>
"""), unsafe_allow_html=True)

if is_dry_run:
    st.markdown(
        '<div class="recs-dry-note">'
        'Dry-run data — live AI output runs daily at 16:35.'
        '</div>', unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# SMART INSIGHTS — compact headline + expandable full analysis
# ═══════════════════════════════════════════════════════════════════════════════
_insights = recs.get("smart_insights", {})
if _insights and _insights.get("insights"):
    _headline = _insights.get("headline", "")
    _body = _insights.get("insights", "")
    _is_hebrew = any('\u0590' <= ch <= '\u05FF' for ch in _body[:80])
    _dir = "rtl" if _is_hebrew else "ltr"
    _text_align = "right" if _is_hebrew else "left"

    import re as _re

    # COMPACT BANNER — just headline in 1-2 lines
    # Icon on the RIGHT side when RTL, left when LTR
    if _is_hebrew:
        # RTL layout: icon on right, text flows right-to-left
        _banner_html = (
            f'<div dir="rtl" style="background:linear-gradient(135deg,#eff6ff 0%,#dbeafe 100%);'
            f'border:1px solid #93c5fd;border-radius:10px;padding:12px 18px;margin:12px 0;'
            f'display:flex;align-items:center;gap:14px;text-align:right;'
            f'box-shadow:0 1px 4px rgba(59,130,246,0.08);">'
            f'<div style="flex-shrink:0;width:36px;height:36px;display:flex;align-items:center;'
            f'justify-content:center;font-size:18px;background:#1e40af;border-radius:10px;'
            f'order:2;">🧠</div>'
            f'<div style="flex:1;order:1;">'
            f'<div style="font-size:10px;font-weight:700;color:#1e40af;'
            f'text-transform:uppercase;letter-spacing:0.12em;">Smart Analyst Brief</div>'
            f'<div style="font-size:14px;font-weight:600;color:#1e3a8a;margin-top:2px;'
            f'line-height:1.45;">{_html.escape(_headline) if _headline else "ניתוח יומי"}</div>'
            f'</div>'
            f'</div>'
        )
    else:
        _banner_html = (
            f'<div style="background:linear-gradient(135deg,#eff6ff 0%,#dbeafe 100%);'
            f'border:1px solid #93c5fd;border-radius:10px;padding:12px 18px;margin:12px 0;'
            f'display:flex;align-items:center;gap:14px;'
            f'box-shadow:0 1px 4px rgba(59,130,246,0.08);">'
            f'<div style="flex-shrink:0;width:36px;height:36px;display:flex;align-items:center;'
            f'justify-content:center;font-size:18px;background:#1e40af;border-radius:10px;">🧠</div>'
            f'<div style="flex:1;">'
            f'<div style="font-size:10px;font-weight:700;color:#1e40af;'
            f'text-transform:uppercase;letter-spacing:0.12em;">Smart Analyst Brief</div>'
            f'<div style="font-size:14px;font-weight:600;color:#1e3a8a;margin-top:2px;'
            f'line-height:1.45;">{_html.escape(_headline) if _headline else "Daily Brief"}</div>'
            f'</div>'
            f'</div>'
        )

    st.markdown(_banner_html, unsafe_allow_html=True)

    # Expandable full analysis
    with st.expander("Read full analysis", expanded=False):
        _paragraphs = [p.strip() for p in _body.split("\n\n") if p.strip()]

        SECTION_ICONS = {
            "Portfolio Health": "💪", "Hidden Risks": "⚠️", "Market Context": "🌍",
            "Opportunities": "💡", "Action Items": "🎯",
            "בריאות התיק": "💪", "סיכונים נסתרים": "⚠️", "הקשר שוק": "🌍",
            "הזדמנויות": "💡", "פעולות מומלצות": "🎯",
        }

        _section_html = ""
        for para in _paragraphs:
            m = _re.match(r'^\*\*([^*]+)\*\*\s*[—\-–:]?\s*(.*)$', para, _re.DOTALL)
            if m:
                title = m.group(1).strip()
                content = m.group(2).strip()
                icon = next((ic for key, ic in SECTION_ICONS.items()
                            if key.lower() in title.lower()), "•")
                content_html = _html.escape(content)
                content_html = _re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', content_html)
                content_html = content_html.replace("\n", "<br>")

                if _is_hebrew:
                    # RTL: icon on the right
                    _section_html += (
                        f'<div dir="rtl" style="display:flex;gap:12px;margin-bottom:14px;'
                        f'align-items:flex-start;text-align:right;">'
                        f'<div style="flex:1;order:1;">'
                        f'<div style="font-size:13px;font-weight:700;color:#1e3a8a;'
                        f'margin-bottom:3px;">{_html.escape(title)}</div>'
                        f'<div style="font-size:13px;color:#334155;line-height:1.75;">{content_html}</div>'
                        f'</div>'
                        f'<div style="flex-shrink:0;width:28px;height:28px;display:flex;'
                        f'align-items:center;justify-content:center;font-size:16px;'
                        f'background:rgba(30,64,175,0.08);border-radius:6px;order:2;">{icon}</div>'
                        f'</div>'
                    )
                else:
                    _section_html += (
                        f'<div style="display:flex;gap:12px;margin-bottom:14px;align-items:flex-start;">'
                        f'<div style="flex-shrink:0;width:28px;height:28px;display:flex;'
                        f'align-items:center;justify-content:center;font-size:16px;'
                        f'background:rgba(30,64,175,0.08);border-radius:6px;">{icon}</div>'
                        f'<div style="flex:1;">'
                        f'<div style="font-size:13px;font-weight:700;color:#1e3a8a;'
                        f'margin-bottom:3px;">{_html.escape(title)}</div>'
                        f'<div style="font-size:13px;color:#334155;line-height:1.75;">{content_html}</div>'
                        f'</div>'
                        f'</div>'
                    )
            elif para.startswith("_") and para.endswith("_"):
                txt = para.strip("_").strip()
                _section_html += (
                    f'<div dir="{_dir}" style="text-align:{_text_align};'
                    f'font-size:11px;color:#94a3b8;font-style:italic;'
                    f'padding-top:10px;border-top:1px solid rgba(148,163,184,0.25);'
                    f'margin-top:8px;">{_html.escape(txt)}</div>'
                )
            else:
                content_html = _html.escape(para)
                content_html = _re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', content_html)
                content_html = content_html.replace("\n", "<br>")
                _section_html += (
                    f'<div dir="{_dir}" style="text-align:{_text_align};'
                    f'font-size:13px;color:#334155;line-height:1.75;margin-bottom:10px;">'
                    f'{content_html}</div>'
                )

        st.markdown(_section_html, unsafe_allow_html=True)

min_conv: int = st.session_state.get("min_conv", 0)


# ─── Sort all holdings into ordered groups ───────────────────────────────────

buys = sorted(
    [h for h in holdings if (h.get("verdict") or "").lower() == "buy"
     and int(h.get("conviction", 0)) >= min_conv],
    key=lambda h: -int(h.get("conviction", 0)),
)
sells = sorted(
    [h for h in holdings if (h.get("verdict") or "").lower() == "sell"
     and int(h.get("conviction", 0)) >= min_conv],
    key=lambda h: -int(h.get("conviction", 0)),
)
rest = sorted(
    [h for h in holdings if (h.get("verdict") or "").lower() == "hold"
     and int(h.get("conviction", 0)) >= min_conv],
    key=lambda h: -int(h.get("conviction", 0)),
)
ideas_filtered = sorted(
    [i for i in new_ideas if int(i.get("conviction", 0)) >= min_conv],
    key=lambda x: -int(x.get("conviction", 0)),
)


def _render_grid(items: list, accent: str, is_idea: bool = False):
    """Render items in a 3-column grid."""
    for i in range(0, len(items), 3):
        row = items[i:i + 3]
        cols = st.columns(3, gap="small")
        for ci, item in enumerate(row):
            with cols[ci]:
                _render_card(item, accent, is_idea)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. BUY — strongest recommendations first
# ═══════════════════════════════════════════════════════════════════════════════
if buys:
    st.markdown(
        f'<div class="below-section"><div class="sect-head"><div>'
        f'<h2>Buy Recommendations</h2>'
        f'<div class="sect-sub">Strongest conviction first — click any card for full analysis</div>'
        f'</div><div class="sect-side">{len(buys)}</div></div></div>',
        unsafe_allow_html=True)
    _render_grid(buys, "buy")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. NEW IDEAS — outside portfolio
# ═══════════════════════════════════════════════════════════════════════════════
if ideas_filtered:
    st.markdown(
        f'<div class="below-section"><div class="sect-head"><div>'
        f'<h2>New Ideas</h2>'
        f'<div class="sect-sub">Pre-screened: only ideas scoring ≥60 appear here</div>'
        f'</div><div class="sect-side">{len(ideas_filtered)}</div></div></div>',
        unsafe_allow_html=True)
    _render_grid(ideas_filtered[:6], "idea", is_idea=True)
else:
    # No ideas passed the threshold this run
    st.markdown(
        '<div class="below-section"><div class="sect-head"><div>'
        '<h2>New Ideas</h2>'
        '<div class="sect-sub">None qualified this run</div>'
        '</div></div></div>'
        '<div style="border:1px dashed var(--hair);background:var(--bg-softer);'
        'padding:20px 24px;font-size:13px;color:var(--text-dim);border-radius:8px;">'
        "The AI scanned candidates for new positions but none scored ≥60 on your "
        "strategy weights. Rather than suggest sub-par ideas, we show nothing. "
        "Tomorrow's run will search again."
        '</div>',
        unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SELL — reduce or exit
# ═══════════════════════════════════════════════════════════════════════════════
if sells:
    st.markdown(
        f'<div class="below-section"><div class="sect-head"><div>'
        f'<h2>Sell Signals</h2>'
        f'<div class="sect-sub">Consider reducing or exiting these positions</div>'
        f'</div><div class="sect-side">{len(sells)}</div></div></div>',
        unsafe_allow_html=True)
    _render_grid(sells, "sell")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. HOLD — no action needed
# ═══════════════════════════════════════════════════════════════════════════════
if rest:
    st.markdown(
        f'<div class="below-section"><div class="sect-head"><div>'
        f'<h2>Hold</h2>'
        f'<div class="sect-sub">No action recommended — continue holding</div>'
        f'</div><div class="sect-side">{len(rest)}</div></div></div>',
        unsafe_allow_html=True)
    _render_grid(rest, "hold")


# ═══════════════════════════════════════════════════════════════════════════════
# FILTER
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown('<div style="height:24px;"></div>', unsafe_allow_html=True)
fc1, fc2 = st.columns([2, 5], gap="small")
with fc1:
    st.slider("Minimum conviction", min_value=0, max_value=100,
              value=0, step=5, label_visibility="collapsed", key="min_conv")
with fc2:
    st.markdown(
        f'<div class="recs-filter-caption" style="margin-top:8px;">'
        f'Filter · showing items with conviction ≥ <b>{min_conv}%</b></div>',
        unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# IDEAS ACCURACY
# ═══════════════════════════════════════════════════════════════════════════════
try:
    from accuracy_tracker import compute_ideas_accuracy
    import json as _json
    _hist_file = Path(__file__).resolve().parent.parent / "ideas_history.json"
    if _hist_file.exists():
        _hist = _json.loads(_hist_file.read_text())
        if _hist:
            try:
                from data_loader import fetch_live_quotes
                _tks = list({i["ticker"] for i in _hist if i.get("ticker")})
                _quotes = fetch_live_quotes(_tks)
                _prices = {}
                if not _quotes.empty:
                    for _, row in _quotes.iterrows():
                        _prices[row["ticker"]] = row.get("price", 0)
                _acc = compute_ideas_accuracy(_hist, _prices)
                if _acc["total"] > 0:
                    with st.expander(
                        f"Ideas Scorecard — {_acc['total']} tracked, "
                        f"{_acc['hit_rate']*100:.0f}% profitable", expanded=False):
                        _rows = []
                        for idea in _acc["ideas"]:
                            e = "✅" if idea["profitable"] else "❌"
                            _rows.append(
                                f'<tr><td class="mono">{idea["ticker"]}</td>'
                                f'<td>{idea["suggested_date"]}</td>'
                                f'<td class="r mono">${idea["suggested_price"]:.1f}</td>'
                                f'<td class="r mono">${idea["current_price"]:.1f}</td>'
                                f'<td class="r mono" style="color:{"var(--up)" if idea["profitable"] else "var(--dn)"};">'
                                f'{idea["return_pct"]:+.1f}%</td><td>{e}</td></tr>')
                        st.markdown(
                            '<table class="recs-table"><thead><tr>'
                            '<th>Ticker</th><th>Date</th><th class="r">Entry</th>'
                            '<th class="r">Now</th><th class="r">Return</th><th>Hit</th>'
                            f'</tr></thead><tbody>{"".join(_rows)}</tbody></table>',
                            unsafe_allow_html=True)
            except Exception:
                pass
except Exception:
    pass

st.markdown("""
<footer class="page-footer">
  <div>AMIT CAPITAL · Recommendations · Market commentary, not financial advice.</div>
  <div class="right">Scoring Engine + Gemini Synthesis</div>
</footer>
""", unsafe_allow_html=True)
