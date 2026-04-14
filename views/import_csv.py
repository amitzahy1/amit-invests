"""
Import — upload a portfolio CSV, see the diff vs the current portfolio.json, apply.

Known formats that work out-of-the-box:
  • Yahoo Finance portfolio export (multi-lot, watchlist rows, currency rows, Israeli agorot)
  • Extrade Pro / Excellence Trade export (Hebrew headers)
  • Any CSV with Symbol + Quantity (+ optional Purchase Price / Current Price / Trade Date)

What the parser handles automatically:
  • Multiple rows for the same ticker (tax lots) → aggregate quantity + weighted-avg cost
  • Watchlist rows (blank Quantity or blank Purchase Price) → skipped
  • Currency rows (Symbol contains "=" like ILS=X) → skipped
  • Israeli tickers (.TA suffix) → Purchase Price converted from agorot (÷100) to ILS
  • English and Hebrew column headers
  • utf-8, utf-8-sig, cp1255, iso-8859-8 encodings
"""

from _bootstrap import ROOT, inject_css, inject_header, handle_actions, load_json, minify

import io
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from config import DISPLAY_NAMES, ISRAELI_TICKERS

inject_css()
inject_header("import_csv")
handle_actions()

PORTFOLIO_PATH = ROOT / "portfolio.json"

# Hero
st.markdown("""
<section class="hero">
  <div class="hero-top">
    <h1 class="lbl">Portfolio Import</h1>
    <div class="mono" style="font-size:12px;color:var(--text-mute);">CSV · XLSX · Yahoo · Extrade Pro · Hebrew</div>
  </div>
  <div style="border-top:1px solid var(--hair);padding:24px 0;font-size:14px;color:var(--text-dim);line-height:1.7;">
    Upload a fresh snapshot from your broker. The parser auto-aggregates tax lots, skips watchlist rows,
    converts Israeli agorot to ILS, and shows you a diff before writing anything to <code style="background:var(--bg-softer);padding:1px 6px;font-family:'IBM Plex Mono';font-size:12px;">portfolio.json</code>.
  </div>
</section>
""", unsafe_allow_html=True)

st.markdown('<div class="below-section">', unsafe_allow_html=True)


# ─── Column alias tables ────────────────────────────────────────────────────
TICKER_ALIASES = {"symbol", "ticker", "security", "שם נייר", "security name", "sec name",
                  "סימול", "שם"}
NAME_ALIASES = {"name", "description", "company", "company name", "display name", "סימול מלא"}
QTY_ALIASES = {"quantity", "qty", "shares", "כמות", "units", "position", "מספר יחידות",
               "number of shares"}
COST_ALIASES = {"purchase price", "cost", "cost basis", "cost price", "avg price",
                "average price", "average cost", "מחיר עלות", "עלות", "מחיר קנייה",
                "avg cost", "book cost", "price paid"}
CURRENT_PRICE_ALIASES = {"current price", "last", "last price", "price", "market price",
                         "מחיר אחרון", "מחיר שוק", "מחיר"}
TRADE_DATE_ALIASES = {"trade date", "date", "purchase date", "תאריך"}
TX_TYPE_ALIASES = {"transaction type", "type", "side", "action"}


def _normalize(s: str) -> str:
    return str(s).strip().lower().replace("_", " ").replace("-", " ")


def _match_column(columns: list[str], aliases: set[str]) -> str | None:
    """Exact match first, then token-aware substring (word boundaries)."""
    # 1) Exact match on normalized
    for col in columns:
        if _normalize(col) in aliases:
            return col
    # 2) Word-boundary substring — avoid "price" matching "purchase price"
    for col in columns:
        norm_tokens = _normalize(col).split()
        for a in aliases:
            a_tokens = a.split()
            if all(t in norm_tokens for t in a_tokens):
                return col
    return None


def _parse_csv(content: bytes, filename: str) -> tuple[pd.DataFrame | None, str | None, dict]:
    """Return (aggregated_df, error, stats).

    aggregated_df columns: ticker, name, quantity, cost_price_usd (or cost_price_ils for .TA),
                           is_israeli, lots (int).
    stats = {'raw_rows': N, 'skipped_currency': N, 'skipped_watchlist': N,
             'lots_aggregated': N, 'final_holdings': N}
    """
    stats = {"raw_rows": 0, "skipped_currency": 0, "skipped_watchlist": 0,
             "lots_aggregated": 0, "final_holdings": 0}
    try:
        for enc in ("utf-8-sig", "utf-8", "cp1255", "iso-8859-8"):
            try:
                if filename.lower().endswith((".xlsx", ".xls")):
                    raw = pd.read_excel(io.BytesIO(content))
                else:
                    raw = pd.read_csv(io.BytesIO(content), encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            return None, "Unable to decode file — please save as UTF-8 CSV.", stats
    except Exception as e:
        return None, f"Failed to parse file: {e}", stats

    stats["raw_rows"] = len(raw)
    cols = list(raw.columns)
    tk_col = _match_column(cols, TICKER_ALIASES)
    qty_col = _match_column(cols, QTY_ALIASES)
    cost_col = _match_column(cols, COST_ALIASES)
    name_col = _match_column(cols, NAME_ALIASES)
    price_col = _match_column(cols, CURRENT_PRICE_ALIASES)
    tx_col = _match_column(cols, TX_TYPE_ALIASES)

    if not tk_col or not qty_col:
        return None, (
            f"Could not detect Ticker/Symbol and Quantity columns.\n\n"
            f"Detected: ticker=`{tk_col}` quantity=`{qty_col}` cost=`{cost_col}`\n\n"
            f"Columns in file: {cols}"
        ), stats

    # Build a working frame with canonical column names
    df = pd.DataFrame({
        "ticker_raw": raw[tk_col].astype(str).str.strip(),
        "name": raw[name_col].astype(str).str.strip() if name_col else raw[tk_col].astype(str),
        "quantity": pd.to_numeric(raw[qty_col], errors="coerce"),
        "cost_price_raw": pd.to_numeric(raw[cost_col], errors="coerce") if cost_col else pd.NA,
        "current_price_raw": pd.to_numeric(raw[price_col], errors="coerce") if price_col else pd.NA,
        "tx_type": raw[tx_col].astype(str).str.upper().str.strip() if tx_col else "",
    })

    # Skip currency rows (e.g. ILS=X, EUR=X)
    is_currency = df["ticker_raw"].str.contains("=", na=False)
    stats["skipped_currency"] = int(is_currency.sum())
    df = df[~is_currency]

    # Skip watchlist rows (no quantity OR no cost basis)
    has_qty = df["quantity"].notna() & (df["quantity"] > 0)
    stats["skipped_watchlist"] = int((~has_qty).sum())
    df = df[has_qty]

    # Skip SELL rows (we model current holdings, not transaction history)
    if tx_col:
        df = df[df["tx_type"] != "SELL"]

    # Uppercase tickers
    df["ticker"] = df["ticker_raw"].str.upper()
    df["is_israeli"] = df["ticker"].str.endswith(".TA")

    # Israeli prices: CSV reports in agorot (1/100 ILS). Convert.
    for col in ("cost_price_raw", "current_price_raw"):
        df.loc[df["is_israeli"] & df[col].notna(), col] /= 100.0

    # Aggregate tax lots — group by ticker, weighted-average cost
    stats["lots_aggregated"] = int(len(df) - df["ticker"].nunique())

    rows = []
    for tk, grp in df.groupby("ticker"):
        total_qty = float(grp["quantity"].sum())
        # Weighted avg cost — only rows with non-null cost contribute
        cost_rows = grp[grp["cost_price_raw"].notna()]
        if len(cost_rows) > 0 and cost_rows["quantity"].sum() > 0:
            weighted_cost = float(
                (cost_rows["quantity"] * cost_rows["cost_price_raw"]).sum()
                / cost_rows["quantity"].sum()
            )
        else:
            weighted_cost = None
        current_price = (float(grp["current_price_raw"].iloc[0])
                         if grp["current_price_raw"].notna().any() else None)
        row = {
            "ticker": tk,
            "name": str(grp["name"].iloc[0]),
            "quantity": total_qty,
            "is_israeli": bool(grp["is_israeli"].iloc[0]),
            "lots": int(len(grp)),
        }
        if row["is_israeli"]:
            row["cost_price_ils"] = weighted_cost
            row["current_price_ils"] = current_price
        else:
            row["cost_price_usd"] = weighted_cost
            row["current_price_usd"] = current_price
        rows.append(row)

    agg = pd.DataFrame(rows).sort_values("ticker").reset_index(drop=True)
    stats["final_holdings"] = len(agg)
    return agg, None, stats


# ─── Step 1: Upload ─────────────────────────────────────────────────────────
st.markdown(minify("""
<div style="display:grid;grid-template-columns:auto 1fr;gap:20px;align-items:center;margin-bottom:16px;">
<div style="width:40px;height:40px;border:2px solid var(--text);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:16px;font-family:'IBM Plex Mono',monospace;">1</div>
<div>
<div style="font-size:18px;font-weight:600;color:var(--text);">Upload your CSV</div>
<div style="font-size:13px;color:var(--text-dim);margin-top:2px;">Yahoo Finance export (multi-lot), Extrade Pro, or any CSV with Symbol + Quantity</div>
</div>
</div>
"""), unsafe_allow_html=True)

uploaded = st.file_uploader(
    "Choose a file", type=["csv", "xlsx", "xls"],
    accept_multiple_files=False,
    label_visibility="collapsed",
)

if not uploaded:
    with st.expander("📖 Supported CSV formats"):
        st.markdown("""
**Yahoo Finance export** (recommended — what the app is tuned for):
```
Symbol,Current Price,...,Trade Date,Purchase Price,Quantity,...,Transaction Type
VOO,624.60,...,20260410,624.16,3.0,...,BUY
NVDA,188.63,...,20250929,183.00,2.0,...,BUY   ← lot 1
NVDA,188.63,...,20250611,144.20,4.68,...,BUY  ← lot 2 (auto-aggregated)
KSM-F34.TA,133180,...,20250305,48100,7,...,BUY ← prices in agorot, auto-converted
ILS=X,3.06,...                                 ← currency row, auto-skipped
PLTK,3.13,...                                  ← empty qty = watchlist, auto-skipped
```

**Minimal format** also works:
```
Symbol,Quantity,Purchase Price
GOOGL,4.46,179.13
VOO,3,624.16
```

**Hebrew headers** (from Extrade Pro):
```
שם נייר,כמות,מחיר עלות,...
```
""")

    # Offer the bundled sample
    sample = ROOT / "sample_csv" / "yahoo_portfolio_example.csv"
    if sample.exists():
        st.download_button(
            "📄 Download sample Yahoo CSV",
            data=sample.read_bytes(),
            file_name="yahoo_portfolio_example.csv",
            mime="text/csv",
        )
    st.stop()

# ─── Step 2: Review ─────────────────────────────────────────────────────────
st.markdown('<div style="height:32px;"></div>', unsafe_allow_html=True)
st.markdown(minify(f"""
<div style="display:grid;grid-template-columns:auto 1fr;gap:20px;align-items:center;margin-bottom:16px;">
<div style="width:40px;height:40px;border:2px solid var(--text);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:16px;font-family:'IBM Plex Mono',monospace;">2</div>
<div>
<div style="font-size:18px;font-weight:600;color:var(--text);">Review parsed data</div>
<div style="font-size:13px;color:var(--text-dim);margin-top:2px;">Aggregated tax lots, skipped watchlist + currency rows</div>
</div>
</div>
"""), unsafe_allow_html=True)

new_df, err, stats = _parse_csv(uploaded.getvalue(), uploaded.name)
if err:
    st.error(err)
    st.stop()

# Parse stats as 4 institutional KPI cells
st.markdown(minify(f"""
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0;border:1px solid var(--hair);background:white;margin-bottom:16px;">
<div style="padding:14px 18px;border-right:1px solid var(--hair-soft);">
<div class="lbl">Raw Rows</div>
<div style="font-size:22px;font-weight:400;font-family:'IBM Plex Mono',monospace;margin-top:4px;">{stats['raw_rows']}</div>
</div>
<div style="padding:14px 18px;border-right:1px solid var(--hair-soft);">
<div class="lbl">Unique Holdings</div>
<div style="font-size:22px;font-weight:400;font-family:'IBM Plex Mono',monospace;margin-top:4px;">{stats['final_holdings']}</div>
</div>
<div style="padding:14px 18px;border-right:1px solid var(--hair-soft);">
<div class="lbl">Lots Merged</div>
<div style="font-size:22px;font-weight:400;font-family:'IBM Plex Mono',monospace;margin-top:4px;">{stats['lots_aggregated']}</div>
</div>
<div style="padding:14px 18px;">
<div class="lbl">Skipped Rows</div>
<div style="font-size:22px;font-weight:400;font-family:'IBM Plex Mono',monospace;margin-top:4px;">{stats['skipped_currency'] + stats['skipped_watchlist']}</div>
<div style="font-size:11px;color:var(--text-mute);font-family:'IBM Plex Mono',monospace;margin-top:2px;">{stats['skipped_currency']} currency · {stats['skipped_watchlist']} watchlist</div>
</div>
</div>
"""), unsafe_allow_html=True)

with st.expander("Show aggregated holdings from the CSV", expanded=False):
    display_cols = ["ticker", "name", "quantity", "lots"]
    if "cost_price_usd" in new_df.columns:
        display_cols.append("cost_price_usd")
    if "cost_price_ils" in new_df.columns:
        display_cols.append("cost_price_ils")
    st.dataframe(new_df[display_cols], use_container_width=True, hide_index=True)


# ─── Diff ───────────────────────────────────────────────────────────────────
current = load_json("portfolio.json")
current_by_ticker = {h["ticker"].upper(): h for h in current.get("holdings", [])}
new_by_ticker = {row["ticker"]: row for _, row in new_df.iterrows()}

added, removed, changed = [], [], []
all_tickers = set(current_by_ticker) | set(new_by_ticker)

for tk in sorted(all_tickers):
    old = current_by_ticker.get(tk)
    new = new_by_ticker.get(tk)
    if old is not None and new is None:
        removed.append({"ticker": tk, "name": old.get("name", tk),
                        "quantity": old.get("quantity")})
    elif new is not None and old is None:
        added.append({
            "ticker": tk, "name": new["name"],
            "quantity": new["quantity"],
            "cost_price_usd": new.get("cost_price_usd"),
            "cost_price_ils": new.get("cost_price_ils"),
            "is_israeli": new["is_israeli"],
        })
    else:
        old_qty = float(old.get("quantity") or 0)
        new_qty = float(new["quantity"])
        diffs = {}
        if abs(old_qty - new_qty) > 0.0001:
            diffs["quantity"] = (old_qty, new_qty)

        if new.get("is_israeli"):
            old_cost = float(old.get("cost_price_ils") or 0)
            new_cost = new.get("cost_price_ils")
            if new_cost is not None and old_cost and abs(old_cost - float(new_cost)) > 0.01:
                diffs["cost_price_ils"] = (old_cost, float(new_cost))
        else:
            old_cost = float(old.get("cost_price_usd") or 0)
            new_cost = new.get("cost_price_usd")
            if new_cost is not None and old_cost and abs(old_cost - float(new_cost)) > 0.01:
                diffs["cost_price_usd"] = (old_cost, float(new_cost))

        if diffs:
            changed.append({"ticker": tk, "name": old.get("name", tk), "diffs": diffs})

unchanged_count = len(all_tickers) - len(added) - len(removed) - len(changed)

# ─── Diff UI ────────────────────────────────────────────────────────────────
st.markdown('<div style="height:24px;"></div>', unsafe_allow_html=True)
st.markdown(minify(f"""
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0;border:1px solid var(--hair);background:white;margin-bottom:16px;">
<div style="padding:16px 20px;border-right:1px solid var(--hair-soft);border-top:3px solid var(--up);">
<div class="lbl" style="color:var(--up);">Added</div>
<div style="font-size:28px;font-weight:400;font-family:'IBM Plex Mono',monospace;margin-top:4px;color:var(--up);">+{len(added)}</div>
<div style="font-size:11px;color:var(--text-mute);margin-top:2px;">New positions</div>
</div>
<div style="padding:16px 20px;border-right:1px solid var(--hair-soft);border-top:3px solid var(--dn);">
<div class="lbl" style="color:var(--dn);">Removed</div>
<div style="font-size:28px;font-weight:400;font-family:'IBM Plex Mono',monospace;margin-top:4px;color:var(--dn);">−{len(removed)}</div>
<div style="font-size:11px;color:var(--text-mute);margin-top:2px;">Closed positions</div>
</div>
<div style="padding:16px 20px;border-right:1px solid var(--hair-soft);border-top:3px solid var(--hold);">
<div class="lbl" style="color:var(--hold);">Changed</div>
<div style="font-size:28px;font-weight:400;font-family:'IBM Plex Mono',monospace;margin-top:4px;color:var(--hold);">~{len(changed)}</div>
<div style="font-size:11px;color:var(--text-mute);margin-top:2px;">Qty or cost shifted</div>
</div>
<div style="padding:16px 20px;border-top:3px solid var(--text-mute);">
<div class="lbl">Unchanged</div>
<div style="font-size:28px;font-weight:400;font-family:'IBM Plex Mono',monospace;margin-top:4px;color:var(--text-dim);">{unchanged_count}</div>
<div style="font-size:11px;color:var(--text-mute);margin-top:2px;">Same as before</div>
</div>
</div>
"""), unsafe_allow_html=True)

if added:
    st.markdown("**New holdings**")
    for a in added:
        if a.get("is_israeli"):
            cost_note = (f"cost ₪{a['cost_price_ils']:,.2f}/share" if a.get("cost_price_ils")
                         else "**cost unknown**")
        else:
            cost_note = (f"cost ${a['cost_price_usd']:,.2f}/share" if a.get("cost_price_usd")
                         else "**cost unknown**")
        display = DISPLAY_NAMES.get(a['ticker'], a['name'])
        st.markdown(
            f"<div style='padding:8px 12px;border-left:3px solid #059669;background:#f0fdf4;border-radius:8px;margin:4px 0;font-size:13px;'>"
            f"➕ <b>{a['ticker']}</b> ({display}) — {a['quantity']:g} shares · {cost_note}"
            f"</div>",
            unsafe_allow_html=True,
        )

if removed:
    st.markdown("**Removed holdings**")
    for r in removed:
        st.markdown(
            f"<div style='padding:8px 12px;border-left:3px solid #e11d48;background:#fff1f2;border-radius:8px;margin:4px 0;font-size:13px;'>"
            f"➖ <b>{r['ticker']}</b> ({DISPLAY_NAMES.get(r['ticker'], r['name'])}) — "
            f"was {r['quantity']} shares"
            f"</div>",
            unsafe_allow_html=True,
        )

if changed:
    st.markdown("**Changed holdings**")
    for c in changed:
        parts = []
        for k, (old_v, new_v) in c["diffs"].items():
            if k == "quantity":
                parts.append(f"quantity {old_v:g} → <b>{new_v:g}</b>")
            elif k == "cost_price_usd":
                parts.append(f"cost ${old_v:,.2f} → <b>${new_v:,.2f}</b>")
            elif k == "cost_price_ils":
                parts.append(f"cost ₪{old_v:,.2f} → <b>₪{new_v:,.2f}</b>")
        st.markdown(
            f"<div style='padding:8px 12px;border-left:3px solid #f59e0b;background:#fffbeb;border-radius:8px;margin:4px 0;font-size:13px;'>"
            f"🔄 <b>{c['ticker']}</b> ({DISPLAY_NAMES.get(c['ticker'], c['name'])}): {' · '.join(parts)}"
            f"</div>",
            unsafe_allow_html=True,
        )

if not (added or removed or changed):
    st.info("No changes detected — the CSV matches your current portfolio exactly.")


# ─── Apply ──────────────────────────────────────────────────────────────────
st.markdown('<div style="height:32px;"></div>', unsafe_allow_html=True)
st.markdown(minify("""
<div style="display:grid;grid-template-columns:auto 1fr;gap:20px;align-items:center;margin-bottom:16px;">
<div style="width:40px;height:40px;border:2px solid var(--text);background:var(--text);color:white;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:16px;font-family:'IBM Plex Mono',monospace;">3</div>
<div>
<div style="font-size:18px;font-weight:600;color:var(--text);">Apply changes</div>
<div style="font-size:13px;color:var(--text-dim);margin-top:2px;">Writes to <code style="background:var(--bg-softer);padding:1px 6px;font-family:'IBM Plex Mono',monospace;font-size:12px;">portfolio.json</code> + logs to Inbox</div>
</div>
</div>
"""), unsafe_allow_html=True)

c1, c2 = st.columns([3, 1])
with c1:
    mode = st.radio(
        "Merge mode",
        ["Replace (CSV is source of truth — safest for broker exports)",
         "Add/update only (keep stocks not in CSV)"],
        index=0,
        help="Replace = any holding missing from the CSV gets removed. "
             "Add/update = only touch tickers that appear in the CSV.",
    )
with c2:
    st.markdown("")
    st.markdown("")
    apply_btn = st.button("💾 Apply", type="primary", use_container_width=True,
                          disabled=not (added or removed or changed))

if apply_btn:
    if mode.startswith("Replace"):
        final_tickers = set(new_by_ticker.keys())
    else:
        final_tickers = set(current_by_ticker.keys()) | set(new_by_ticker.keys())

    new_holdings = []
    for tk in sorted(final_tickers):
        existing = current_by_ticker.get(tk, {})
        fresh = new_by_ticker.get(tk)

        if not fresh:
            new_holdings.append(existing)
            continue

        record = {
            "ticker": tk,
            "name": fresh["name"] or existing.get("name") or tk,
            "quantity": float(fresh["quantity"]),
        }
        if fresh["is_israeli"]:
            cost = fresh.get("cost_price_ils")
            if cost is not None:
                record["cost_price_ils"] = float(cost)
            else:
                record["cost_price_ils"] = existing.get("cost_price_ils")
            if fresh.get("current_price_ils") is not None:
                record["current_price_ils"] = float(fresh["current_price_ils"])
            elif existing.get("current_price_ils"):
                record["current_price_ils"] = existing["current_price_ils"]
        else:
            cost = fresh.get("cost_price_usd")
            if cost is not None:
                record["cost_price_usd"] = float(cost)
            elif existing.get("cost_price_usd"):
                record["cost_price_usd"] = existing["cost_price_usd"]
            else:
                record["cost_price_usd"] = None
                record["cost_unknown"] = True

        record["ai_recommendation"] = existing.get("ai_recommendation", "-")
        record["ai_rating"] = existing.get("ai_rating", "-")
        if existing.get("notes"):
            record["notes"] = existing["notes"]
        # Record lot count for context
        if fresh["lots"] > 1:
            record["notes"] = (
                f"{fresh['lots']} tax lots aggregated via CSV import on "
                f"{datetime.utcnow().strftime('%Y-%m-%d')}"
            )
        new_holdings.append(record)

    updated = dict(current)
    updated["holdings"] = new_holdings
    updated["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d")
    updated.setdefault("transactions", []).append({
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "type": "csv_import",
        "description": (
            f"CSV import from `{uploaded.name}` — "
            f"+{len(added)} / -{len(removed)} / ~{len(changed)}. "
            f"{stats['lots_aggregated']} lots aggregated."
        ),
    })

    PORTFOLIO_PATH.write_text(json.dumps(updated, indent=2, ensure_ascii=False))
    st.cache_data.clear()
    st.success(
        f"✅ Applied — portfolio.json now has {len(new_holdings)} holdings "
        f"(added {len(added)}, removed {len(removed)}, changed {len(changed)})."
    )
    st.info("Switch to the Portfolio tab to see updated numbers.")
