"""
Settings — Investment strategy + notifications.
One strategy choice drives both the Gemini prompt and the scoring weights.
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, minify

import json
import streamlit as st
from config import SECTOR_COLORS

inject_css()
inject_header("settings")
handle_actions()

SETTINGS_PATH = ROOT / "settings.json"

# ─── Strategy presets — one choice drives everything ─────────────────────────

STRATEGIES = {
    "conservative_longterm": {
        "label": "Conservative Long-Term",
        "sub": "Quality businesses at fair prices. 1+ year horizon.",
        "style": "conservative",
        "risk_level": "medium-low",
        "weights": {"quality": 30, "valuation": 25, "risk": 20, "macro": 15, "sentiment": 5, "technical": 5},
    },
    "balanced": {
        "label": "Balanced",
        "sub": "Equal consideration of fundamentals, valuation, and market signals.",
        "style": "balanced",
        "risk_level": "medium",
        "weights": {"quality": 20, "valuation": 20, "risk": 15, "macro": 15, "sentiment": 15, "technical": 15},
    },
    "value": {
        "label": "Deep Value",
        "sub": "Buffett / Graham — cheap, high-quality businesses. Price is everything.",
        "style": "conservative",
        "risk_level": "medium-low",
        "weights": {"quality": 25, "valuation": 35, "risk": 15, "macro": 10, "sentiment": 5, "technical": 10},
    },
    "growth": {
        "label": "Growth",
        "sub": "Cathie Wood / ARK style — high-growth with momentum. Tolerates high valuations.",
        "style": "aggressive",
        "risk_level": "medium-high",
        "weights": {"quality": 15, "valuation": 10, "risk": 15, "macro": 15, "sentiment": 20, "technical": 25},
    },
    "income": {
        "label": "Income / Defensive",
        "sub": "Capital preservation, low volatility, stable dividends.",
        "style": "conservative",
        "risk_level": "low",
        "weights": {"quality": 25, "valuation": 20, "risk": 30, "macro": 15, "sentiment": 5, "technical": 5},
    },
}

STRATEGY_KEYS = list(STRATEGIES.keys())
ALL_SECTORS = list(SECTOR_COLORS.keys())
FREQS = ["weekly", "bi-monthly", "monthly", "quarterly"]
SCORE_LABELS = {"quality": "Quality", "valuation": "Valuation", "risk": "Risk",
                "macro": "Macro", "sentiment": "Sentiment", "technical": "Trend"}

DEFAULT = {
    "profile_name": "Conservative AI Bull",
    "scoring_strategy": "conservative_longterm",
    "horizon_years": 4,
    "trading_frequency": "bi-monthly",
    "contribution_ils": 4000,
    "contribution_frequency_days": 60,
    "crypto_cap_pct": 3,
    "preferred_sectors": [],
    "avoid_sectors": [],
    "theses": [],
    "telegram": {"enabled": True, "send_daily_digest": True, "send_alerts_on_strong_verdicts": True},
}


def _load():
    if SETTINGS_PATH.exists():
        try:
            return {**DEFAULT, **json.loads(SETTINGS_PATH.read_text())}
        except Exception:
            return DEFAULT.copy()
    return DEFAULT.copy()


s = _load()

# Resolve current strategy
_strat_key = s.get("scoring_strategy", "conservative_longterm")
if _strat_key not in STRATEGIES:
    _strat_key = "conservative_longterm"
_strat = STRATEGIES[_strat_key]
_weights = _strat["weights"]

# ─── Hero ────────────────────────────────────────────────────────────────────

_top = max(_weights.items(), key=lambda x: x[1])
_tg = "On" if s.get("telegram", {}).get("enabled") else "Off"

st.markdown(minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Settings</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">{s.get('profile_name','—')}</div>
</div>
<div class="hero-grid" style="grid-template-columns: repeat(4, 1fr);">
<div class="hero-cell">
<div class="lbl">Strategy</div>
<div class="hero-value hero-value-light" style="font-size:18px;">{_strat['label']}</div>
<div class="hero-sub">{_strat['style'].title()} · {_strat['risk_level']}</div>
</div>
<div class="hero-cell">
<div class="lbl">Horizon</div>
<div class="hero-value tab">{s.get('horizon_years', 4)}<span class="hero-value-suffix">years</span></div>
<div class="hero-sub">Investment timeframe</div>
</div>
<div class="hero-cell">
<div class="lbl">Top Weight</div>
<div class="hero-value tab">{SCORE_LABELS.get(_top[0], _top[0])}</div>
<div class="hero-sub">{_top[1]}% of verdict</div>
</div>
<div class="hero-cell">
<div class="lbl">Telegram</div>
<div class="hero-value hero-value-light" style="font-size:22px;">{_tg}</div>
<div class="hero-sub">Daily digest</div>
</div>
</div>
</section>
"""), unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# FORM
# ═══════════════════════════════════════════════════════════════════════════════

with st.form("settings_form"):

    # ── Section 1: Strategy ──────────────────────────────────────────────
    st.markdown(
        '<div class="below-section"><div class="sect-head"><div>'
        '<h2>Investment Strategy</h2>'
        '<div class="sect-sub">Drives the scoring engine, Gemini prompts, and Telegram verdicts</div>'
        '</div></div></div>', unsafe_allow_html=True)

    profile_name = st.text_input("Profile name", s.get("profile_name", ""))

    # Strategy selector
    scoring_strategy = st.selectbox(
        "Strategy",
        STRATEGY_KEYS,
        index=STRATEGY_KEYS.index(_strat_key),
        format_func=lambda k: f"{STRATEGIES[k]['label']}  —  {STRATEGIES[k]['sub']}",
    )

    # Show what weights this strategy uses (read-only, no sliders)
    sel_strat = STRATEGIES[scoring_strategy]
    sel_weights = sel_strat["weights"]
    _w_parts = "  ·  ".join(
        f"{SCORE_LABELS.get(k, k)} {v}%" for k, v in
        sorted(sel_weights.items(), key=lambda x: -x[1])
    )
    st.markdown(
        f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;'
        f'padding:12px 16px;font-size:12px;color:#475569;margin-bottom:8px;">'
        f'<span style="font-weight:600;">Scoring weights:</span> {_w_parts}'
        f'</div>', unsafe_allow_html=True)

    # Details
    c1, c2 = st.columns(2)
    with c1:
        horizon_years = st.number_input("Horizon (years)", min_value=1, max_value=30,
            value=int(s.get("horizon_years", 4)))
    with c2:
        crypto_cap_pct = st.number_input("Crypto cap (%)", min_value=0, max_value=100,
            value=int(s.get("crypto_cap_pct", 3)), step=1)

    c3, c4 = st.columns(2)
    with c3:
        trading_frequency = st.selectbox("Contribution frequency", FREQS,
            index=FREQS.index(s.get("trading_frequency", "bi-monthly")) if s.get("trading_frequency") in FREQS else 1)
    with c4:
        contribution_ils = st.number_input("Contribution (ILS)", min_value=0,
            value=int(s.get("contribution_ils", 4000)), step=500)

    # Sectors
    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
    sc1, sc2 = st.columns(2)
    with sc1:
        preferred_sectors = st.multiselect("Preferred sectors", ALL_SECTORS,
            default=[x for x in s.get("preferred_sectors", []) if x in ALL_SECTORS])
    with sc2:
        avoid_sectors = st.multiselect("Avoid sectors", ALL_SECTORS,
            default=[x for x in s.get("avoid_sectors", []) if x in ALL_SECTORS])

    # Theses
    theses_raw = st.text_area("Investment theses (one per line)",
        value="\n".join(s.get("theses", [])), height=100,
        help="Injected into every AI prompt. E.g. 'AI is the dominant growth story'")

    # ── Section 2: Notifications ─────────────────────────────────────────
    st.markdown(
        '<div class="below-section"><div class="sect-head"><div>'
        '<h2>Notifications</h2>'
        '<div class="sect-sub">Telegram daily digest with market context, scores, and lessons</div>'
        '</div></div></div>', unsafe_allow_html=True)

    tg = s.get("telegram", {})
    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        tg_enabled = st.checkbox("Enable Telegram", value=bool(tg.get("enabled", False)))
    with tc2:
        tg_daily = st.checkbox("Daily digest", value=bool(tg.get("send_daily_digest", True)))
    with tc3:
        tg_alerts = st.checkbox("Strong BUY/SELL alerts", value=bool(tg.get("send_alerts_on_strong_verdicts", True)))

    st.caption("Bot credentials are in your .env file (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID).")

    # ── Save ─────────────────────────────────────────────────────────────
    st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
    submitted = st.form_submit_button("Save settings", use_container_width=True, type="primary")

    if submitted:
        sel = STRATEGIES[scoring_strategy]
        freq_days = {"weekly": 7, "bi-monthly": 60, "monthly": 30, "quarterly": 90}
        out = {
            "profile_name": profile_name.strip() or DEFAULT["profile_name"],
            "scoring_strategy": scoring_strategy,
            "style": sel["style"],
            "risk_level": sel["risk_level"],
            "scoring_weights": sel["weights"],
            "horizon_years": int(horizon_years),
            "trading_frequency": trading_frequency,
            "contribution_ils": int(contribution_ils),
            "contribution_frequency_days": freq_days.get(trading_frequency, 60),
            "crypto_cap_pct": int(crypto_cap_pct),
            "preferred_sectors": preferred_sectors,
            "avoid_sectors": avoid_sectors,
            "theses": [line.strip() for line in theses_raw.splitlines() if line.strip()],
            "recommendation_mode": "scoring",
            "telegram": {
                "enabled": bool(tg_enabled),
                "send_daily_digest": bool(tg_daily),
                "send_alerts_on_strong_verdicts": bool(tg_alerts),
            },
        }
        SETTINGS_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        st.success("Saved. Changes take effect on the next daily run (16:35).")
