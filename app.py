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
    st.Page("pages/portfolio.py", title="Portfolio", icon="📊", default=True),
    st.Page("pages/recommendations.py", title="Recommendations", icon="🎯"),
    st.Page("pages/import_csv.py", title="Import", icon="📥"),
    st.Page("pages/inbox.py", title="Inbox", icon="📬"),
    st.Page("pages/settings.py", title="Settings", icon="⚙️"),
    st.Page("pages/explainer.py", title="How It Works", icon="🧠"),
]

# `position="top"` puts the nav as tabs across the top; falls back to sidebar
# on Streamlit versions that don't support it.
try:
    nav = st.navigation(_pages, position="top")
except TypeError:
    nav = st.navigation(_pages)
nav.run()
