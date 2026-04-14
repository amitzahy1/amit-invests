"""
Portfolio Dashboard — navigation entry point.
Uses Streamlit's st.navigation API with top-positioned tabs.
"""

import sys
from pathlib import Path

# Ensure the project root is importable from any page (_bootstrap, config, data_loader, etc.)
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#1e293b"/>'
    '<stop offset="100%" stop-color="#0f172a"/>'
    '</linearGradient></defs>'
    '<rect width="32" height="32" rx="6" fill="url(#g)"/>'
    '<path d="M8 22 L16 8 L24 22" stroke="#22d3ee" stroke-width="2.5" '
    'fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    '<line x1="11" y1="17" x2="21" y2="17" stroke="#22d3ee" '
    'stroke-width="1.5" stroke-linecap="round" opacity="0.6"/>'
    '</svg>'
)

st.set_page_config(
    page_title="Amit Capital",
    page_icon=_FAVICON_SVG,
    layout="wide",
    initial_sidebar_state="collapsed",
)

_V = _ROOT / "views"
_pages = [
    st.Page(_V / "portfolio.py",       title="Portfolio",       icon="📊",
            url_path="portfolio", default=True),
    st.Page(_V / "recommendations.py", title="Recommendations", icon="🎯",
            url_path="recommendations"),
    st.Page(_V / "import_csv.py",      title="Import",          icon="📥",
            url_path="import_csv"),
    st.Page(_V / "inbox.py",           title="Inbox",           icon="📬",
            url_path="inbox"),
    st.Page(_V / "settings.py",        title="Settings",        icon="⚙️",
            url_path="settings"),
    st.Page(_V / "explainer.py",       title="How It Works",    icon="🧠",
            url_path="explainer"),
]

# We render our OWN topbar in _bootstrap.inject_header(), so hide Streamlit's
# default navigation widget entirely (position="hidden" when supported).
try:
    nav = st.navigation(_pages, position="hidden")
except TypeError:
    try:
        nav = st.navigation(_pages, position="top")
    except TypeError:
        nav = st.navigation(_pages)
nav.run()
