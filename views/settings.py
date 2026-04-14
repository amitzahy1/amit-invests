"""
Settings — 3 sections: Investor Profile, Scoring Strategy, Notifications.
Saved to settings.json. The daily pipeline reads this on every run.
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, minify

import json
import streamlit as st
from config import SECTOR_COLORS

inject_css()
inject_header("settings")
handle_actions()

SETTINGS_PATH = ROOT / "settings.json"

# ─── Scoring categories ──────────────────────────────────────────────────────

SCORE_CATEGORIES = {
    "quality": {
        "label": "Quality", "icon": "🏛️",
        "short_he": "איכות עסקית",
        "what_he": "האם העסק רווחי, יציב ובריא? ROE, שולי רווח, חוב, צמיחה.",
        "what_en": "ROE >15%, margins >20%, debt/equity <0.5, revenue growth >10%.",
    },
    "valuation": {
        "label": "Valuation", "icon": "💰",
        "short_he": "תמחור",
        "what_he": "האם המניה זולה או יקרה? P/E מול סקטור, PEG, יעד אנליסטים.",
        "what_en": "P/E vs sector, PEG, analyst target upside, price ratio check.",
    },
    "risk": {
        "label": "Risk", "icon": "🛡️",
        "short_he": "סיכון תיק",
        "what_he": "האם הפוזיציה גדולה מדי? ריכוזיות, Beta, סקטור, מדיניות קריפטו.",
        "what_en": "Concentration >15%, beta >1.5, sector >35%, crypto cap.",
    },
    "macro": {
        "label": "Macro", "icon": "🌍",
        "short_he": "כלכלה",
        "what_he": "מה המצב הכלכלי? ריבית, VIX, עקום תשואות, אינפלציה.",
        "what_en": "Fed rate, VIX regime, yield curve, inflation level.",
    },
    "sentiment": {
        "label": "Sentiment", "icon": "📊",
        "short_he": "קונצנזוס",
        "what_he": "מה אומרים האנליסטים? אחוז Buy/Hold/Sell מוול סטריט.",
        "what_en": "Wall Street consensus: >70% Buy = bullish, >30% Sell = bearish.",
    },
    "technical": {
        "label": "Trend", "icon": "📈",
        "short_he": "מגמה",
        "what_he": "לאיזה כיוון נע המחיר? ממוצעים נעים, RSI, סטייה מממוצע ארוך.",
        "what_en": "MA50/MA200 crossovers, RSI momentum, distance from MA200.",
    },
}
SCORE_KEYS = list(SCORE_CATEGORIES.keys())

STRATEGY_PRESETS = {
    "conservative_longterm": {
        "label": "Conservative Long-Term (1+ years)",
        "desc_he": "מתמקד באיכות עסקית ותמחור הוגן. משקל מינימלי לאותות קצרי-טווח.",
        "weights": {"quality": 30, "valuation": 25, "risk": 20, "macro": 15, "sentiment": 5, "technical": 5},
    },
    "balanced": {
        "label": "Balanced",
        "desc_he": "שקלול שווה בין כל הפרמטרים.",
        "weights": {"quality": 20, "valuation": 20, "risk": 15, "macro": 15, "sentiment": 15, "technical": 15},
    },
    "value": {
        "label": "Deep Value (Buffett / Graham)",
        "desc_he": "עסקים איכותיים במחירים זולים. התמחור הוא המלך.",
        "weights": {"quality": 25, "valuation": 35, "risk": 15, "macro": 10, "sentiment": 5, "technical": 10},
    },
    "growth": {
        "label": "Growth (Cathie Wood / ARK)",
        "desc_he": "חברות צמיחה עם מומנטום. סובל תמחור גבוה.",
        "weights": {"quality": 15, "valuation": 10, "risk": 15, "macro": 15, "sentiment": 20, "technical": 25},
    },
    "income": {
        "label": "Income / Defensive",
        "desc_he": "יציבות, דיבידנדים, שימור הון.",
        "weights": {"quality": 25, "valuation": 20, "risk": 30, "macro": 15, "sentiment": 5, "technical": 5},
    },
    "custom": {
        "label": "Custom",
        "desc_he": "הגדר משקלות ידנית.",
        "weights": {"quality": 17, "valuation": 17, "risk": 17, "macro": 17, "sentiment": 16, "technical": 16},
    },
}

DEFAULT = {
    "profile_name": "Conservative AI Bull",
    "style": "conservative", "horizon_years": 4, "trading_frequency": "bi-monthly",
    "contribution_ils": 4000, "contribution_frequency_days": 60,
    "theses": [], "preferred_sectors": [], "avoid_sectors": [],
    "crypto_cap_pct": 3, "risk_level": "medium",
    "recommendation_mode": "scoring",
    "scoring_strategy": "conservative_longterm",
    "scoring_weights": STRATEGY_PRESETS["conservative_longterm"]["weights"].copy(),
    "telegram": {"enabled": True, "send_daily_digest": True, "send_alerts_on_strong_verdicts": True},
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

# ─── Hero ────────────────────────────────────────────────────────────────────
_sw = s.get("scoring_weights", DEFAULT["scoring_weights"])
_top = max(_sw.items(), key=lambda x: x[1]) if _sw else ("quality", 30)
_cat_icon = SCORE_CATEGORIES.get(_top[0], {}).get("icon", "")
_cat_label = SCORE_CATEGORIES.get(_top[0], {}).get("label", _top[0])
_tg_status = "On" if s.get("telegram", {}).get("enabled") else "Off"

st.markdown(minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Settings</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">{s.get('profile_name','—')}</div>
</div>
<div class="hero-grid" style="grid-template-columns: repeat(3, 1fr);">
<div class="hero-cell">
<div class="lbl">Profile</div>
<div class="hero-value hero-value-light" style="font-size:22px;">{s.get('style','—').title()}</div>
<div class="hero-sub">{s.get('horizon_years','—')} year horizon · Risk: {s.get('risk_level','—')}</div>
</div>
<div class="hero-cell">
<div class="lbl">Scoring Strategy</div>
<div class="hero-value hero-value-light" style="font-size:15px;">{s.get('scoring_strategy','custom').replace('_',' ').title()}</div>
<div class="hero-sub">Top: {_cat_icon} {_cat_label} ({_top[1]}%)</div>
</div>
<div class="hero-cell">
<div class="lbl">Telegram</div>
<div class="hero-value hero-value-light" style="font-size:22px;">{_tg_status}</div>
<div class="hero-sub">Daily digest + alerts</div>
</div>
</div>
</section>
"""), unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# FORM — 3 clean sections
# ═══════════════════════════════════════════════════════════════════════════════

with st.form("settings_form"):

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 1: Investor Profile
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("""
    <div class="below-section"><div class="sect-head"><div>
    <h2>Investor Profile</h2>
    <div class="sect-sub">Who you are as an investor — drives every recommendation and score</div>
    </div></div></div>
    """, unsafe_allow_html=True)

    profile_name = st.text_input("Profile name", s.get("profile_name", ""))

    c1, c2, c3 = st.columns(3)
    with c1:
        style = st.selectbox("Style", STYLES,
            index=STYLES.index(s.get("style", "conservative")) if s.get("style") in STYLES else 0)
    with c2:
        horizon_years = st.number_input("Horizon (years)", min_value=1, max_value=50,
            value=int(s.get("horizon_years", 4)))
    with c3:
        risk_level = st.selectbox("Risk level", RISK_LEVELS,
            index=RISK_LEVELS.index(s.get("risk_level", "medium")) if s.get("risk_level") in RISK_LEVELS else 2)

    c4, c5, c6 = st.columns(3)
    with c4:
        trading_frequency = st.selectbox("Contribution frequency", FREQS,
            index=FREQS.index(s.get("trading_frequency", "bi-monthly")) if s.get("trading_frequency") in FREQS else 2)
    with c5:
        contribution_ils = st.number_input("Contribution (ILS)", min_value=0,
            value=int(s.get("contribution_ils", 4000)), step=500)
    with c6:
        crypto_cap_pct = st.number_input("Crypto cap (%)", min_value=0, max_value=100,
            value=int(s.get("crypto_cap_pct", 3)), step=1)

    sc1, sc2 = st.columns(2)
    with sc1:
        preferred_sectors = st.multiselect("Preferred sectors", ALL_SECTORS,
            default=[x for x in s.get("preferred_sectors", []) if x in ALL_SECTORS])
    with sc2:
        avoid_sectors = st.multiselect("Avoid sectors", ALL_SECTORS,
            default=[x for x in s.get("avoid_sectors", []) if x in ALL_SECTORS])

    theses_raw = st.text_area("Investment theses (one per line)",
        value="\n".join(s.get("theses", [])), height=100,
        help="Injected into the AI prompt. Example: 'AI is the dominant growth story of the next 3 years'")

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 2: Scoring Strategy
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("""
    <div class="below-section"><div class="sect-head"><div>
    <h2>Scoring Strategy</h2>
    <div class="sect-sub">How the engine evaluates your holdings</div>
    </div></div></div>
    """, unsafe_allow_html=True)

    # Explanation banner
    st.markdown("""
    <div style="background:#f0f4f8;border:1px solid #e2e8f0;border-radius:8px;padding:16px 20px;margin-bottom:16px;font-size:13px;line-height:1.7;color:#1e293b;">
    <div style="font-weight:600;margin-bottom:6px;">How it works</div>
    Every day, each holding gets <b>6 scores</b> (0-100): Quality, Valuation, Risk, Macro, Sentiment, and Trend.<br>
    The <b>weights</b> below control how much each score influences the final <b>BUY / HOLD / SELL</b> verdict.<br><br>
    <div style="font-size:12px;color:#64748b;">
    This affects: <b>Recommendations page</b> (score bars + verdicts) · <b>Telegram messages</b> (daily verdicts) · <b>Charts</b> (which holdings get charted)
    </div>
    </div>
    """, unsafe_allow_html=True)

    # Strategy preset
    preset_keys = list(STRATEGY_PRESETS.keys())
    current_preset = s.get("scoring_strategy", "conservative_longterm")
    if current_preset not in preset_keys:
        current_preset = "custom"

    scoring_strategy = st.selectbox("Strategy preset", preset_keys,
        index=preset_keys.index(current_preset),
        format_func=lambda x: STRATEGY_PRESETS[x]["label"])

    st.caption(STRATEGY_PRESETS[scoring_strategy]["desc_he"])

    # Weight sliders — 2 columns × 3 rows
    preset_weights = STRATEGY_PRESETS[scoring_strategy]["weights"]
    current_weights = s.get("scoring_weights", preset_weights)
    if scoring_strategy != "custom" and scoring_strategy != s.get("scoring_strategy"):
        current_weights = preset_weights

    scoring_weights = {}
    for row_start in range(0, 6, 2):
        cols = st.columns(2)
        for col_idx, key in enumerate(SCORE_KEYS[row_start:row_start + 2]):
            cat = SCORE_CATEGORIES[key]
            with cols[col_idx]:
                val = st.slider(
                    f"{cat['icon']} {cat['label']} — {cat['short_he']}",
                    0, 50, int(current_weights.get(key, preset_weights.get(key, 17))),
                    step=5, key=f"sw_{key}",
                    help=cat["what_he"])
                scoring_weights[key] = val

    total_w = sum(scoring_weights.values())
    if total_w == 100:
        st.markdown(
            f'<div style="font-size:12px;color:#047857;margin-top:-4px;">'
            f'Total: {total_w}%</div>', unsafe_allow_html=True)
    else:
        st.warning(f"Total: {total_w}% — should be 100%. Adjust sliders.")

    # How each score is calculated
    with st.expander("How is each score calculated?"):
        for key in SCORE_KEYS:
            cat = SCORE_CATEGORIES[key]
            st.markdown(
                f"**{cat['icon']} {cat['label']}** — {cat['what_he']}\n\n"
                f"<span style='font-size:12px;color:#6b7280;'>{cat['what_en']}</span>",
                unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════
    # SECTION 3: Notifications
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("""
    <div class="below-section"><div class="sect-head"><div>
    <h2>Notifications</h2>
    <div class="sect-sub">Daily Telegram messages with scores, market context, and lessons</div>
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

    st.caption("Bot credentials (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID) are stored in your .env file.")

    # ── Save ──────────────────────────────────────────────────────────────
    st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
    submitted = st.form_submit_button("Save settings", use_container_width=True, type="primary")

    if submitted:
        out = {
            "profile_name": profile_name.strip() or DEFAULT["profile_name"],
            "style": style,
            "horizon_years": int(horizon_years),
            "trading_frequency": trading_frequency,
            "contribution_ils": int(contribution_ils),
            "contribution_frequency_days": {"daily": 1, "weekly": 7, "bi-monthly": 60, "monthly": 30}.get(trading_frequency, 60),
            "theses": [line.strip() for line in theses_raw.splitlines() if line.strip()],
            "preferred_sectors": preferred_sectors,
            "avoid_sectors": avoid_sectors,
            "crypto_cap_pct": int(crypto_cap_pct),
            "risk_level": risk_level,
            "recommendation_mode": "scoring",
            "scoring_strategy": scoring_strategy,
            "scoring_weights": scoring_weights,
            "telegram": {
                "enabled": bool(tg_enabled),
                "send_daily_digest": bool(tg_daily),
                "send_alerts_on_strong_verdicts": bool(tg_alerts),
            },
        }
        SETTINGS_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        st.success("Saved. Changes take effect on the next daily run (16:35).")
