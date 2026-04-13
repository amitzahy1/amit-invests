"""
Settings — edit your personality profile. Saved to settings.json.
Next scheduled run of ai-hedge-fund picks up the changes automatically.
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, minify

import json
import streamlit as st
from config import SECTOR_COLORS

inject_css()
inject_header("settings")
handle_actions()

SETTINGS_PATH = ROOT / "settings.json"

DEFAULT = {
    "profile_name": "Amit — Conservative AI Bull (default)",
    "style": "conservative",
    "horizon_years": 3,
    "trading_frequency": "bi-monthly",
    "contribution_ils": 4000,
    "contribution_frequency_days": 60,
    "theses": [],
    "preferred_sectors": [],
    "avoid_sectors": [],
    "crypto_cap_pct": 10,
    "risk_level": "medium-low",
    "personas_active": ["warren_buffett", "charlie_munger", "cathie_wood", "peter_lynch", "risk_manager"],
    "telegram": {"enabled": False, "send_daily_digest": True, "send_alerts_on_strong_verdicts": True},
}

ALL_PERSONAS = [
    "warren_buffett", "charlie_munger", "cathie_wood", "peter_lynch",
    "michael_burry", "ben_graham", "phil_fisher", "bill_ackman", "stanley_druckenmiller",
    "technical_analyst", "fundamentals_analyst", "valuation", "sentiment", "macro", "risk_manager",
]

ALL_SECTORS = list(SECTOR_COLORS.keys())

STYLES = ["conservative", "balanced", "aggressive"]
FREQS = ["daily", "weekly", "bi-monthly", "monthly"]
RISK_LEVELS = ["low", "medium-low", "medium", "medium-high", "high"]

# ─── Load current settings ──────────────────────────────────────────────────
def _load():
    if SETTINGS_PATH.exists():
        try:
            return {**DEFAULT, **json.loads(SETTINGS_PATH.read_text())}
        except Exception:
            return DEFAULT.copy()
    return DEFAULT.copy()

s = _load()

st.markdown(minify(f"""
<section class="hero">
<div class="hero-top">
<div class="lbl">Settings — Trading Personality</div>
<div class="mono" style="font-size:12px;color:var(--text-mute);">{s.get('profile_name', '—')}</div>
</div>
<div class="hero-grid" style="grid-template-columns: repeat(4, 1fr);">
<div class="hero-cell">
<div class="lbl">Style</div>
<div class="hero-value hero-value-light" style="font-size:22px;">{s.get('style', '—').title()}</div>
<div class="hero-sub">Risk: {s.get('risk_level', '—')}</div>
</div>
<div class="hero-cell">
<div class="lbl">Horizon</div>
<div class="hero-value tab">{s.get('horizon_years', '—')}<span class="hero-value-suffix">years</span></div>
<div class="hero-sub">Long-term focus</div>
</div>
<div class="hero-cell">
<div class="lbl">Contribution</div>
<div class="hero-value tab">₪{s.get('contribution_ils', 0):,.0f}</div>
<div class="hero-sub">Every {s.get('contribution_frequency_days', 0)} days</div>
</div>
<div class="hero-cell">
<div class="lbl">Active Personas</div>
<div class="hero-value tab">{len(s.get('personas_active', []))}</div>
<div class="hero-sub">Inject into each AI run</div>
</div>
</div>
</section>
"""), unsafe_allow_html=True)
st.markdown('<div class="below-section">', unsafe_allow_html=True)

with st.form("settings_form"):
    st.markdown("### Profile")
    profile_name = st.text_input("Profile name", s.get("profile_name", ""))

    col1, col2 = st.columns(2)
    with col1:
        style = st.selectbox("Trading style", STYLES, index=STYLES.index(s.get("style", "conservative")) if s.get("style") in STYLES else 0)
        horizon_years = st.number_input("Investment horizon (years)", min_value=0, max_value=50, value=int(s.get("horizon_years", 3)), step=1)
        risk_level = st.selectbox("Risk level", RISK_LEVELS, index=RISK_LEVELS.index(s.get("risk_level", "medium-low")) if s.get("risk_level") in RISK_LEVELS else 1)
    with col2:
        trading_frequency = st.selectbox("Trading frequency", FREQS, index=FREQS.index(s.get("trading_frequency", "bi-monthly")) if s.get("trading_frequency") in FREQS else 2)
        contribution_ils = st.number_input("Contribution amount (ILS)", min_value=0, value=int(s.get("contribution_ils", 4000)), step=100)
        contribution_frequency_days = st.number_input("Contribution frequency (days)", min_value=1, max_value=365, value=int(s.get("contribution_frequency_days", 60)), step=1)

    crypto_cap_pct = st.slider("Crypto cap (% of portfolio)", min_value=0, max_value=100, value=int(s.get("crypto_cap_pct", 10)), step=1)

    st.markdown("### Sectors")
    preferred_sectors = st.multiselect(
        "Preferred sectors (overweight these)",
        ALL_SECTORS,
        default=[x for x in s.get("preferred_sectors", []) if x in ALL_SECTORS],
    )
    avoid_sectors = st.multiselect(
        "Avoid sectors (never recommend)",
        ALL_SECTORS,
        default=[x for x in s.get("avoid_sectors", []) if x in ALL_SECTORS],
    )

    st.markdown("### Investment Theses")
    st.caption("Free-text theses — each line is injected verbatim into the recommendation prompt.")
    theses_raw = st.text_area(
        "One thesis per line",
        value="\n".join(s.get("theses", [])),
        height=180,
    )

    st.markdown("### AI Personas")
    st.caption("Which investor personas should weigh in on each holding?")
    # B5: warn on unknown personas in the JSON
    unknown_personas = [x for x in s.get("personas_active", []) if x not in ALL_PERSONAS]
    if unknown_personas:
        st.warning(f"Unknown personas in settings.json will be dropped on save: `{', '.join(unknown_personas)}`")
    personas_active = st.multiselect(
        "Active personas",
        ALL_PERSONAS,
        default=[x for x in s.get("personas_active", []) if x in ALL_PERSONAS],
    )

    st.markdown("### Telegram")
    tg = s.get("telegram", {})
    tg_enabled = st.checkbox("Enable Telegram delivery", value=bool(tg.get("enabled", False)))
    tg_daily = st.checkbox("Send daily digest", value=bool(tg.get("send_daily_digest", True)))
    tg_alerts = st.checkbox("Send immediate alert on STRONG BUY / STRONG SELL", value=bool(tg.get("send_alerts_on_strong_verdicts", True)))

    st.markdown("**How to receive Telegram messages** — 5-minute setup, one time.")
    st.markdown("""
1. Open Telegram and search for **@BotFather** (the official bot that creates bots).
2. Send `/newbot`. Follow the prompts: pick a name (e.g. *Amit Portfolio*) and a username ending in `bot` (e.g. `amit_portfolio_bot`).
3. BotFather replies with a **token** that looks like `1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`. Copy it.
4. In the project folder, copy `.env.example` → `.env` and paste the token:
   ```
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
   ```
5. Open a chat with your new bot in Telegram and send it **any message** (e.g. "hi"). This is required — Telegram won't let a bot message you until you've messaged it first.
6. In a browser visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` (replace `<TOKEN>` with yours).
7. Find `"chat":{"id": 123456789, …}` in the JSON response. That number is your **chat ID**. Paste it into `.env`:
   ```
   TELEGRAM_CHAT_ID=123456789
   ```
8. Tick **Enable Telegram delivery** above and click Save. Test with:
   ```
   python scripts/telegram_digest.py --once
   ```
You should receive the daily digest in your Telegram chat. After this, the launchd scheduler sends it automatically every morning.
""")

    st.markdown("")
    submitted = st.form_submit_button("💾 Save settings", use_container_width=True, type="primary")

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
            "personas_active": personas_active,
            "telegram": {
                "enabled": bool(tg_enabled),
                "send_daily_digest": bool(tg_daily),
                "send_alerts_on_strong_verdicts": bool(tg_alerts),
            },
        }
        SETTINGS_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        st.success(f"Saved to {SETTINGS_PATH.name}. Next recommendation run will use this profile.")

st.markdown("---")
with st.expander("Raw settings.json"):
    st.code(json.dumps(s, indent=2, ensure_ascii=False), language="json")
