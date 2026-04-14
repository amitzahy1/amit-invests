"""
Settings — investment profile, scoring strategy, engine mode, and notifications.
Saved to settings.json. Next scheduled run picks up changes automatically.
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, minify

import json
import streamlit as st
from config import SECTOR_COLORS

inject_css()
inject_header("settings")
handle_actions()

SETTINGS_PATH = ROOT / "settings.json"

# ─── Scoring category definitions ────────────────────────────────────────────

SCORE_CATEGORIES = {
    "quality": {
        "label": "Quality", "icon": "🏛️",
        "short": "Business quality",
        "how_he": "ROE > 15%, שולי רווח > 20%, חוב/הון < 0.5, צמיחת הכנסות > 10%",
        "how_en": "Profitability (ROE >15%, net margin >20%), financial health (debt/equity <0.5), revenue & EPS growth >10%.",
    },
    "valuation": {
        "label": "Valuation", "icon": "💰",
        "short": "Is it cheap?",
        "how_he": "P/E מול ממוצע סקטור, PEG, יעד אנליסטים, יחסי מחיר (P/E>25, P/B>3)",
        "how_en": "4 sub-methods weighted 30/25/25/20: P/E vs sector avg, PEG ratio, analyst target upside, price ratio elevation.",
    },
    "risk": {
        "label": "Risk", "icon": "🛡️",
        "short": "Portfolio safety",
        "how_he": "ריכוזיות > 15% = עודף משקל, Beta > 1.5 = תנודתי, סקטור > 35%, מדיניות קריפטו",
        "how_en": "Concentration >15%, beta >1.5, sector >35%, crypto cap enforcement. Higher score = safer.",
    },
    "macro": {
        "label": "Macro", "icon": "🌍",
        "short": "Economic environment",
        "how_he": "VIX (< 15 רגוע, > 25 פחד), עקום תשואות (הפוך = מיתון), ריבית פד, אינפלציה",
        "how_en": "VIX regime, yield curve shape, Fed rate impact on stocks/bonds, inflation level.",
    },
    "sentiment": {
        "label": "Sentiment", "icon": "📊",
        "short": "Analyst consensus",
        "how_he": "קונצנזוס וול סטריט: > 70% Buy = חיובי, > 30% Sell = שלילי, בונוס לכיסוי רחב (20+)",
        "how_en": "Wall Street consensus: >70% Buy = bullish, >30% Sell = bearish. Coverage breadth bonus.",
    },
    "technical": {
        "label": "Trend", "icon": "📈",
        "short": "Price momentum",
        "how_he": "MA50/MA200 (Golden/Death Cross), RSI (< 30 oversold, > 70 overbought), סטייה מ-MA200",
        "how_en": "Triple MA trend (40%), RSI momentum (35%), deviation from 200-day MA (25%). Weighted by confidence.",
    },
}
SCORE_KEYS = list(SCORE_CATEGORIES.keys())

STRATEGY_PRESETS = {
    "conservative_longterm": {
        "label": "Conservative Long-Term (1+ years)",
        "desc": "Quality businesses at fair prices. Minimal short-term noise.",
        "weights": {"quality": 30, "valuation": 25, "risk": 20, "macro": 15, "sentiment": 5, "technical": 5},
    },
    "balanced": {
        "label": "Balanced",
        "desc": "Even consideration across all dimensions.",
        "weights": {"quality": 20, "valuation": 20, "risk": 15, "macro": 15, "sentiment": 15, "technical": 15},
    },
    "value": {
        "label": "Deep Value (Buffett / Graham)",
        "desc": "Cheap, high-quality businesses. Price is king.",
        "weights": {"quality": 25, "valuation": 35, "risk": 15, "macro": 10, "sentiment": 5, "technical": 10},
    },
    "growth": {
        "label": "Growth (Cathie Wood / ARK)",
        "desc": "High-growth with momentum. Tolerates high valuations.",
        "weights": {"quality": 15, "valuation": 10, "risk": 15, "macro": 15, "sentiment": 20, "technical": 25},
    },
    "income": {
        "label": "Income / Defensive",
        "desc": "Stable dividends, low volatility, capital preservation.",
        "weights": {"quality": 25, "valuation": 20, "risk": 30, "macro": 15, "sentiment": 5, "technical": 5},
    },
    "custom": {
        "label": "Custom",
        "desc": "Set your own weights manually.",
        "weights": {"quality": 17, "valuation": 17, "risk": 17, "macro": 17, "sentiment": 16, "technical": 16},
    },
}

DEFAULT = {
    "profile_name": "Conservative AI Bull",
    "style": "conservative", "horizon_years": 3, "trading_frequency": "bi-monthly",
    "contribution_ils": 4000, "contribution_frequency_days": 60,
    "theses": [], "preferred_sectors": [], "avoid_sectors": [],
    "crypto_cap_pct": 10, "risk_level": "medium-low",
    "recommendation_mode": "personas",
    "scoring_strategy": "conservative_longterm",
    "scoring_weights": STRATEGY_PRESETS["conservative_longterm"]["weights"].copy(),
    "personas_active": ["warren_buffett", "charlie_munger", "cathie_wood", "peter_lynch", "risk_manager"],
    "telegram": {"enabled": False, "send_daily_digest": True, "send_alerts_on_strong_verdicts": True},
}

ALL_PERSONAS = [
    "warren_buffett", "charlie_munger", "cathie_wood", "peter_lynch",
    "michael_burry", "ben_graham",
    "technical_analyst", "fundamentals_analyst", "valuation", "sentiment", "macro", "risk_manager",
]
PERSONA_LABELS = {
    "warren_buffett": "Warren Buffett (Value)", "charlie_munger": "Charlie Munger (Quality)",
    "cathie_wood": "Cathie Wood (Innovation)", "peter_lynch": "Peter Lynch (GARP)",
    "michael_burry": "Michael Burry (Contrarian)", "ben_graham": "Ben Graham (Deep Value)",
    "technical_analyst": "Technical Analyst", "fundamentals_analyst": "Fundamentals Analyst",
    "valuation": "Valuation Analyst", "sentiment": "Sentiment Analyst",
    "macro": "Macro Analyst", "risk_manager": "Risk Manager",
}

ALL_SECTORS = list(SECTOR_COLORS.keys())
STYLES = ["conservative", "balanced", "aggressive"]
FREQS = ["daily", "weekly", "bi-monthly", "monthly"]
RISK_LEVELS = ["low", "medium-low", "medium", "medium-high", "high"]

# ─── Load ────────────────────────────────────────────────────────────────────
def _load():
    if SETTINGS_PATH.exists():
        try:
            return {**DEFAULT, **json.loads(SETTINGS_PATH.read_text())}
        except Exception:
            return DEFAULT.copy()
    return DEFAULT.copy()

s = _load()

# ─── Hero strip ──────────────────────────────────────────────────────────────
_sw = s.get("scoring_weights", DEFAULT["scoring_weights"])
_top = max(_sw.items(), key=lambda x: x[1]) if _sw else ("quality", 30)
_cat_label = SCORE_CATEGORIES.get(_top[0], {}).get("label", _top[0])

st.markdown(minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Settings</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">{s.get('profile_name', '—')}</div>
</div>
<div class="hero-grid" style="grid-template-columns: repeat(4, 1fr);">
<div class="hero-cell">
<div class="lbl">Style</div>
<div class="hero-value hero-value-light" style="font-size:22px;">{s.get('style','—').title()}</div>
<div class="hero-sub">{s.get('horizon_years','—')} year horizon</div>
</div>
<div class="hero-cell">
<div class="lbl">Strategy</div>
<div class="hero-value hero-value-light" style="font-size:14px;">{s.get('scoring_strategy','custom').replace('_',' ').title()}</div>
<div class="hero-sub">Top weight: {_cat_label} ({_top[1]}%)</div>
</div>
<div class="hero-cell">
<div class="lbl">Engine</div>
<div class="hero-value hero-value-light" style="font-size:18px;">{s.get('recommendation_mode','personas').title()}</div>
<div class="hero-sub">{len(s.get('personas_active',[]))} personas</div>
</div>
<div class="hero-cell">
<div class="lbl">Contribution</div>
<div class="hero-value tab">₪{s.get('contribution_ils',0):,.0f}</div>
<div class="hero-sub">Every {s.get('contribution_frequency_days',60)} days</div>
</div>
</div>
</section>
"""), unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# FORM
# ═══════════════════════════════════════════════════════════════════════════════

with st.form("settings_form"):

    # ── Section 1: Profile ────────────────────────────────────────────────
    st.markdown("""
    <div class="below-section"><div class="sect-head"><div>
    <h2>Investor Profile</h2>
    <div class="sect-sub">Your investment personality drives every recommendation</div>
    </div></div></div>
    """, unsafe_allow_html=True)

    profile_name = st.text_input("Profile name", s.get("profile_name", ""))

    c1, c2, c3 = st.columns(3)
    with c1:
        style = st.selectbox("Style", STYLES,
            index=STYLES.index(s.get("style", "conservative")) if s.get("style") in STYLES else 0)
    with c2:
        horizon_years = st.number_input("Horizon (years)", min_value=1, max_value=50,
            value=int(s.get("horizon_years", 3)))
    with c3:
        risk_level = st.selectbox("Risk level", RISK_LEVELS,
            index=RISK_LEVELS.index(s.get("risk_level", "medium-low")) if s.get("risk_level") in RISK_LEVELS else 1)

    c4, c5, c6 = st.columns(3)
    with c4:
        trading_frequency = st.selectbox("Trading frequency", FREQS,
            index=FREQS.index(s.get("trading_frequency", "bi-monthly")) if s.get("trading_frequency") in FREQS else 2)
    with c5:
        contribution_ils = st.number_input("Contribution (ILS)", min_value=0,
            value=int(s.get("contribution_ils", 4000)), step=500)
    with c6:
        contribution_frequency_days = st.number_input("Every N days", min_value=1, max_value=365,
            value=int(s.get("contribution_frequency_days", 60)))

    crypto_cap_pct = st.slider("Crypto cap (% of portfolio)", 0, 100,
        int(s.get("crypto_cap_pct", 10)), step=1)

    # ── Section 2: Sectors & Theses ───────────────────────────────────────
    st.markdown("""
    <div class="below-section"><div class="sect-head"><div>
    <h2>Sectors & Theses</h2>
    <div class="sect-sub">Sector preferences and investment beliefs injected into every AI prompt</div>
    </div></div></div>
    """, unsafe_allow_html=True)

    sc1, sc2 = st.columns(2)
    with sc1:
        preferred_sectors = st.multiselect("Preferred sectors (overweight)", ALL_SECTORS,
            default=[x for x in s.get("preferred_sectors", []) if x in ALL_SECTORS])
    with sc2:
        avoid_sectors = st.multiselect("Avoid sectors (never recommend)", ALL_SECTORS,
            default=[x for x in s.get("avoid_sectors", []) if x in ALL_SECTORS])

    theses_raw = st.text_area("Investment theses (one per line)",
        value="\n".join(s.get("theses", [])), height=120,
        help="Each line is injected verbatim into the AI recommendation prompt.")

    # ── Section 3: Scoring Engine ─────────────────────────────────────────
    st.markdown("""
    <div class="below-section"><div class="sect-head"><div>
    <h2>Scoring Engine</h2>
    <div class="sect-sub">6 algorithmic scores rated 0-100 per holding — weights determine the final verdict</div>
    </div></div></div>
    """, unsafe_allow_html=True)

    preset_keys = list(STRATEGY_PRESETS.keys())
    current_preset = s.get("scoring_strategy", "conservative_longterm")
    if current_preset not in preset_keys:
        current_preset = "custom"

    scoring_strategy = st.selectbox("Strategy preset", preset_keys,
        index=preset_keys.index(current_preset),
        format_func=lambda x: f"{STRATEGY_PRESETS[x]['label']}  —  {STRATEGY_PRESETS[x]['desc']}")

    preset_weights = STRATEGY_PRESETS[scoring_strategy]["weights"]
    current_weights = s.get("scoring_weights", preset_weights)
    if scoring_strategy != "custom" and scoring_strategy != s.get("scoring_strategy"):
        current_weights = preset_weights

    # Score weight sliders — 2 columns, 3 rows
    scoring_weights = {}
    for row_start in range(0, 6, 2):
        cols = st.columns(2)
        for col_idx, key in enumerate(SCORE_KEYS[row_start:row_start + 2]):
            cat = SCORE_CATEGORIES[key]
            with cols[col_idx]:
                val = st.slider(
                    f"{cat['icon']} {cat['label']} — {cat['short']}",
                    0, 50, int(current_weights.get(key, preset_weights.get(key, 17))),
                    step=5, key=f"sw_{key}",
                    help=cat["how_en"])
                scoring_weights[key] = val

    total_w = sum(scoring_weights.values())
    if total_w == 100:
        st.markdown(f'<div style="font-size:12px;color:var(--up);margin-top:-8px;">Weights: {total_w}%</div>',
                    unsafe_allow_html=True)
    else:
        st.warning(f"Weights sum to {total_w}% — should be 100%.")

    # How each score works
    with st.expander("How is each score calculated?"):
        for key in SCORE_KEYS:
            cat = SCORE_CATEGORIES[key]
            st.markdown(f"**{cat['icon']} {cat['label']}** — {cat['how_he']}")
            st.caption(cat["how_en"])

    # ── Section 4: Engine Mode + Personas ─────────────────────────────────
    st.markdown("""
    <div class="below-section"><div class="sect-head"><div>
    <h2>Recommendation Engine</h2>
    <div class="sect-sub">Choose how recommendations are generated</div>
    </div></div></div>
    """, unsafe_allow_html=True)

    REC_MODES = ["personas", "scoring", "hybrid"]
    REC_LABELS = {
        "personas": "Personas — 9 AI analysts × N holdings (detailed, slower)",
        "scoring": "Scoring — algorithmic scores + 1 Gemini synthesis call (fast, data-driven)",
        "hybrid": "Hybrid — scores first, then personas comment",
    }
    current_mode = s.get("recommendation_mode", "personas")
    recommendation_mode = st.selectbox("Engine mode", REC_MODES,
        index=REC_MODES.index(current_mode) if current_mode in REC_MODES else 0,
        format_func=lambda x: REC_LABELS.get(x, x))

    if recommendation_mode in ("personas", "hybrid"):
        personas_active = st.multiselect("Active personas (used in personas/hybrid mode)",
            ALL_PERSONAS,
            default=[x for x in s.get("personas_active", []) if x in ALL_PERSONAS],
            format_func=lambda x: PERSONA_LABELS.get(x, x))
    else:
        personas_active = s.get("personas_active", DEFAULT["personas_active"])
        st.caption("Personas are not used in scoring mode — the engine uses algorithmic scores + 1 Gemini synthesis call per holding.")

    # ── Section 5: Telegram ───────────────────────────────────────────────
    st.markdown("""
    <div class="below-section"><div class="sect-head"><div>
    <h2>Telegram Notifications</h2>
    <div class="sect-sub">Daily digest with market context, scores, and daily lesson</div>
    </div></div></div>
    """, unsafe_allow_html=True)

    tg = s.get("telegram", {})
    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        tg_enabled = st.checkbox("Enable Telegram", value=bool(tg.get("enabled", False)))
    with tc2:
        tg_daily = st.checkbox("Daily digest", value=bool(tg.get("send_daily_digest", True)))
    with tc3:
        tg_alerts = st.checkbox("Strong BUY/SELL alerts", value=bool(tg.get("send_alerts_on_strong_verdicts", True)))

    st.caption("Credentials (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`) are in your `.env` file.")

    # ── Save ──────────────────────────────────────────────────────────────
    st.markdown('<div style="height:16px;"></div>', unsafe_allow_html=True)
    submitted = st.form_submit_button("Save settings", use_container_width=True, type="primary")

    if submitted:
        out = {
            "profile_name": profile_name.strip() or DEFAULT["profile_name"],
            "style": style,
            "horizon_years": int(horizon_years),
            "trading_frequency": trading_frequency,
            "contribution_ils": int(contribution_ils),
            "contribution_frequency_days": int(contribution_frequency_days),
            "theses": [line.strip() for line in theses_raw.splitlines() if line.strip()],
            "preferred_sectors": preferred_sectors,
            "avoid_sectors": avoid_sectors,
            "crypto_cap_pct": int(crypto_cap_pct),
            "risk_level": risk_level,
            "recommendation_mode": recommendation_mode,
            "scoring_strategy": scoring_strategy,
            "scoring_weights": scoring_weights,
            "personas_active": personas_active,
            "telegram": {
                "enabled": bool(tg_enabled),
                "send_daily_digest": bool(tg_daily),
                "send_alerts_on_strong_verdicts": bool(tg_alerts),
            },
        }
        SETTINGS_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        st.success("Saved. Next recommendation run will use these settings.")

# ── Raw JSON (debug) ──────────────────────────────────────────────────────────
with st.expander("Raw settings.json"):
    st.code(json.dumps(s, indent=2, ensure_ascii=False), language="json")
