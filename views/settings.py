"""
Settings — minimal investment profile.
One strategy choice defines everything. No redundant fields.
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
        "sub": "Quality businesses at fair prices",
        "horizon_years": 5,
        "style": "conservative",
        "risk_level": "medium-low",
        "weights": {"quality": 30, "valuation": 25, "risk": 20, "macro": 15, "sentiment": 5, "technical": 5},
    },
    "balanced": {
        "label": "Balanced",
        "sub": "Equal weight across fundamentals, valuation, and signals",
        "horizon_years": 4,
        "style": "balanced",
        "risk_level": "medium",
        "weights": {"quality": 20, "valuation": 20, "risk": 15, "macro": 15, "sentiment": 15, "technical": 15},
    },
    "value": {
        "label": "Deep Value (Buffett / Graham)",
        "sub": "Cheap, high-quality businesses — price is everything",
        "horizon_years": 7,
        "style": "conservative",
        "risk_level": "medium-low",
        "weights": {"quality": 25, "valuation": 35, "risk": 15, "macro": 10, "sentiment": 5, "technical": 10},
    },
    "growth": {
        "label": "Growth (Cathie Wood / ARK)",
        "sub": "High-growth with momentum — tolerates high valuations",
        "horizon_years": 5,
        "style": "aggressive",
        "risk_level": "medium-high",
        "weights": {"quality": 15, "valuation": 10, "risk": 15, "macro": 15, "sentiment": 20, "technical": 25},
    },
    "income": {
        "label": "Income / Defensive",
        "sub": "Capital preservation, low volatility, stable dividends",
        "horizon_years": 10,
        "style": "conservative",
        "risk_level": "low",
        "weights": {"quality": 25, "valuation": 20, "risk": 30, "macro": 15, "sentiment": 5, "technical": 5},
    },
}

ALL_SECTORS = list(SECTOR_COLORS.keys())
FREQS = ["weekly", "bi-monthly", "monthly", "quarterly"]
SCORE_LABELS = {"quality": "Quality", "valuation": "Valuation", "risk": "Risk",
                "macro": "Macro", "sentiment": "Sentiment", "technical": "Trend"}
FREQ_DAYS = {"weekly": 7, "bi-monthly": 60, "monthly": 30, "quarterly": 90}

DEFAULT = {
    "profile_name": "Conservative AI Bull",
    "scoring_strategy": "conservative_longterm",
    "contribution_ils": 4000,
    "trading_frequency": "bi-monthly",
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
_strat_key = s.get("scoring_strategy", "conservative_longterm")
if _strat_key not in STRATEGIES:
    _strat_key = "conservative_longterm"
_strat = STRATEGIES[_strat_key]

# ─── Hero ────────────────────────────────────────────────────────────────────

_top = max(_strat["weights"].items(), key=lambda x: x[1])
_tg_on = s.get("telegram", {}).get("enabled")

st.markdown(minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Settings</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">{s.get('profile_name','—')}</div>
</div>
<div class="hero-grid" style="grid-template-columns: repeat(4, 1fr);">
<div class="hero-cell">
<div class="lbl">Strategy</div>
<div class="hero-value hero-value-light" style="font-size:16px;">{_strat['label']}</div>
<div class="hero-sub">{_strat['horizon_years']} year horizon · {_strat['style']}</div>
</div>
<div class="hero-cell">
<div class="lbl">Top Weight</div>
<div class="hero-value tab">{SCORE_LABELS.get(_top[0], _top[0])}</div>
<div class="hero-sub">{_top[1]}% of verdict</div>
</div>
<div class="hero-cell">
<div class="lbl">Contribution</div>
<div class="hero-value tab">₪{s.get('contribution_ils',0):,.0f}</div>
<div class="hero-sub">Every {FREQ_DAYS.get(s.get('trading_frequency','bi-monthly'), 60)} days</div>
</div>
<div class="hero-cell">
<div class="lbl">Telegram</div>
<div class="hero-value hero-value-light" style="font-size:22px;">{'On' if _tg_on else 'Off'}</div>
<div class="hero-sub">Daily digest at 16:35</div>
</div>
</div>
</section>
"""), unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# FORM
# ═══════════════════════════════════════════════════════════════════════════════

# ── Section 1: Strategy (outside form for live preview) ──────────────────
st.markdown(
    '<div class="below-section"><div class="sect-head"><div>'
    '<h2>Strategy</h2>'
    '<div class="sect-sub">One choice defines everything: horizon, risk, and scoring weights</div>'
    '</div></div></div>', unsafe_allow_html=True)

profile_name = st.text_input("Profile name", s.get("profile_name", ""), key="profile_name_input")

strategy_keys = list(STRATEGIES.keys())
scoring_strategy = st.selectbox(
    "Investment strategy",
    strategy_keys,
    index=strategy_keys.index(_strat_key),
    format_func=lambda k: f"{STRATEGIES[k]['label']}  —  {STRATEGIES[k]['sub']}",
    key="strategy_select",
)

sel = STRATEGIES[scoring_strategy]

# Live preview — updates immediately when strategy changes
st.markdown(
    f'<div style="background:linear-gradient(135deg,#f8fafc 0%,#f1f5f9 100%);'
    f'border:1px solid #e2e8f0;border-radius:8px;'
    f'padding:16px 20px;font-size:13px;color:#334155;margin:8px 0 16px;">'
    f'<div style="font-size:10px;font-weight:700;color:#64748b;text-transform:uppercase;'
    f'letter-spacing:0.12em;margin-bottom:12px;">Applied parameters</div>'
    f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:14px;">'
    f'<div><div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;">Horizon</div>'
    f'<div style="font-size:16px;font-weight:700;color:#0f172a;">{sel["horizon_years"]} years</div></div>'
    f'<div><div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;">Style</div>'
    f'<div style="font-size:16px;font-weight:700;color:#0f172a;">{sel["style"].title()}</div></div>'
    f'<div><div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;">Risk level</div>'
    f'<div style="font-size:16px;font-weight:700;color:#0f172a;">{sel["risk_level"]}</div></div>'
    f'</div>'
    f'<div style="border-top:1px solid #e2e8f0;padding-top:12px;">'
    f'<div style="font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:8px;">Scoring weights</div>'
    f'<div style="display:flex;flex-wrap:wrap;gap:8px;">'
    + "".join(
        f'<span style="background:#ffffff;border:1px solid #cbd5e1;border-radius:4px;'
        f'padding:4px 10px;font-size:12px;font-weight:600;color:#334155;">'
        f'{SCORE_LABELS.get(k, k)} <span style="color:#6366f1;">{v}%</span></span>'
        for k, v in sorted(sel["weights"].items(), key=lambda x: -x[1])
    )
    + '</div></div></div>', unsafe_allow_html=True)

with st.form("settings_form"):

    # ── Section 2: Portfolio Details ─────────────────────────────────────
    st.markdown(
        '<div class="below-section"><div class="sect-head"><div>'
        '<h2>Portfolio Details</h2>'
        '<div class="sect-sub">Contribution, sectors, and investment beliefs</div>'
        '</div></div></div>', unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        trading_frequency = st.selectbox("Contribution frequency", FREQS,
            index=FREQS.index(s.get("trading_frequency", "bi-monthly")) if s.get("trading_frequency") in FREQS else 1)
    with c2:
        contribution_ils = st.number_input("Contribution amount (ILS)", min_value=0,
            value=int(s.get("contribution_ils", 4000)), step=500)
    with c3:
        crypto_cap_pct = st.number_input("Crypto cap (% of portfolio)", min_value=0, max_value=100,
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
        help="Injected into every AI prompt. Example: 'AI is the dominant growth story'")

    # ── Section 3: Notifications ─────────────────────────────────────────
    st.markdown(
        '<div class="below-section"><div class="sect-head"><div>'
        '<h2>Notifications</h2>'
        '<div class="sect-sub">Daily Telegram digest with scores, charts, and smart insights</div>'
        '</div></div></div>', unsafe_allow_html=True)

    tg = s.get("telegram", {})
    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        tg_enabled = st.checkbox("Enable Telegram", value=bool(tg.get("enabled", False)))
    with tc2:
        tg_daily = st.checkbox("Daily digest", value=bool(tg.get("send_daily_digest", True)))
    with tc3:
        tg_alerts = st.checkbox("Strong BUY/SELL alerts", value=bool(tg.get("send_alerts_on_strong_verdicts", True)))

    st.caption("Credentials are in .env (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID).")

    # ── Save ─────────────────────────────────────────────────────────────
    st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
    submitted = st.form_submit_button("Save settings", use_container_width=True, type="primary")

    if submitted:
        sel = STRATEGIES[scoring_strategy]
        out = {
            "profile_name": profile_name.strip() or DEFAULT["profile_name"],
            "scoring_strategy": scoring_strategy,
            "horizon_years": sel["horizon_years"],
            "style": sel["style"],
            "risk_level": sel["risk_level"],
            "scoring_weights": sel["weights"],
            "trading_frequency": trading_frequency,
            "contribution_ils": int(contribution_ils),
            "contribution_frequency_days": FREQ_DAYS.get(trading_frequency, 60),
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
        st.success("Saved. Next daily run (16:35) will use these settings.")
