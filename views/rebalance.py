"""Rebalance suggestions — Hierarchical Risk Parity + CVaR-min optimizer.

Shows the user a "suggested target" allocation vs. current, with delta rows
(add / trim / hold). Pure-Python via Riskfolio-Lib — no paid data.
"""

from _bootstrap import inject_css, inject_header, handle_actions
import streamlit as st

from portfolio_optimizer import build_rebalance_summary

inject_css()
inject_header("rebalance")
handle_actions()


# ─── Header ──────────────────────────────────────────────────────────────────
st.markdown(
    '<div class="below-section">'
    '<div class="sect-head"><div>'
    '<h2>⚖️ Rebalance suggestions</h2>'
    '<div class="sect-sub">'
    'Hierarchical Risk Parity (Lopez de Prado) and CVaR-minimization — '
    'both run on 1-year price history from Yahoo + pymaya.'
    '</div></div></div>',
    unsafe_allow_html=True,
)

mode = st.radio(
    "Optimization model",
    ["HRP — Hierarchical Risk Parity", "CVaR — minimize worst-5% loss"],
    index=0, horizontal=True,
    help="HRP is more stable with few tickers; CVaR minimizes tail risk.",
)
mode_key = "HRP" if mode.startswith("HRP") else "CVaR"

with st.spinner("Fetching price history and solving..."):
    summary = build_rebalance_summary(mode=mode_key)

if summary.get("error"):
    st.warning(f"⚠️ {summary['error']}")
    st.info(
        "The optimizer needs at least 30 days of price history across all "
        "tickers. If yfinance is rate-limited, retry in a few minutes."
    )
    st.stop()

st.caption(f"Model: **{summary['mode']}**")

# ─── Deltas table — the main output ──────────────────────────────────────────
deltas = summary.get("deltas", [])
if deltas:
    import pandas as pd

    df = pd.DataFrame(deltas)
    # Friendly colouring — add for +ve deltas, trim for -ve
    def _row_colour(row):
        if row["action"] == "add":
            return ["background-color: #ecfdf5"] * len(row)
        if row["action"] == "trim":
            return ["background-color: #fef2f2"] * len(row)
        return [""] * len(row)

    st.subheader("Suggested rebalance")
    st.dataframe(
        df.style.apply(_row_colour, axis=1).format({
            "current_pct": "{:.2f}%",
            "target_pct": "{:.2f}%",
            "delta_pct": "{:+.2f}%",
        }),
        use_container_width=True, hide_index=True,
    )

    # Summary — sum of add/trim actions
    to_add = sum(r["delta_pct"] for r in deltas if r["delta_pct"] > 0)
    to_trim = sum(-r["delta_pct"] for r in deltas if r["delta_pct"] < 0)
    col1, col2 = st.columns(2)
    col1.metric("Total allocation to add", f"{to_add:.2f}%")
    col2.metric("Total allocation to trim", f"{to_trim:.2f}%")

# ─── Target weights chart ────────────────────────────────────────────────────
targets = summary.get("target_weights", {})
if targets:
    st.subheader("Target allocation")
    st.bar_chart(targets)

# ─── Caveats ─────────────────────────────────────────────────────────────────
st.caption(
    "⚠️ The optimizer is a risk-first model — it does not forecast returns. "
    "Treat the output as a starting point for discussion, not a trade list. "
    "Implementation has transaction costs + tax implications not modelled here."
)
