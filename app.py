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

st.set_page_config(
    page_title="Portfolio — Amit",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_pages = [
    st.Page("views/portfolio.py",       title="Portfolio",       icon="📊",
            url_path="portfolio", default=True),
    st.Page("views/recommendations.py", title="Recommendations", icon="🎯",
            url_path="recommendations"),
    st.Page("views/import_csv.py",      title="Import",          icon="📥",
            url_path="import_csv"),
    st.Page("views/inbox.py",           title="Inbox",           icon="📬",
            url_path="inbox"),
    st.Page("views/settings.py",        title="Settings",        icon="⚙️",
            url_path="settings"),
    st.Page("views/explainer.py",       title="How It Works",    icon="🧠",
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
