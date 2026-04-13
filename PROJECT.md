# Amit's Investment Portfolio Dashboard

## Overview
Interactive web dashboard for tracking and analyzing a personal investment portfolio. Built with Streamlit + Plotly, managed via Claude Code, with live data from Yahoo Finance.

The dashboard provides executive-level visibility into portfolio performance, risk metrics, sector exposure, and individual stock analysis — all with a professional dark-themed financial UI.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    app.py (Streamlit)                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │   KPIs   │  │  Charts  │  │  Tables  │  │  Drill  │ │
│  │  Cards   │  │  Plotly  │  │  Pandas  │  │  Down   │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬────┘ │
│       └──────────────┼───────────┼──────────────┘      │
│                      │           │                       │
│              ┌───────┴───────────┴────────┐              │
│              │     portfolio_calc.py       │              │
│              │  Risk, Returns, Correlation │              │
│              └─────────────┬──────────────┘              │
│                            │                             │
│              ┌─────────────┴──────────────┐              │
│              │      data_loader.py         │              │
│              │  JSON + Yahoo Finance API   │              │
│              └──────┬──────────┬──────────┘              │
│                     │          │                          │
│            ┌────────┴──┐  ┌───┴─────────┐                │
│            │portfolio  │  │Yahoo Finance│                │
│            │  .json    │  │  v8 API     │                │
│            └───────────┘  └─────────────┘                │
└─────────────────────────────────────────────────────────┘
```

## File Structure

| File | Purpose |
|------|---------|
| `app.py` | Main Streamlit entry point — layout, sections, orchestration |
| `data_loader.py` | Data pipeline: JSON parsing, Yahoo Finance API, portfolio building |
| `portfolio_calc.py` | Financial calculations: Sharpe, beta, volatility, correlations |
| `charts.py` | 9 Plotly chart factory functions with consistent dark theme |
| `config.py` | All constants: ticker maps, sectors, colors, API config |
| `style.css` | Custom CSS dark theme for Streamlit |
| `portfolio.json` | Portfolio holdings data — updated via Claude Code |
| `requirements.txt` | Python dependencies |
| `portfolio.py` | Original terminal-based dashboard (archived) |

## Dashboard Sections

### 1. Recommendations & Alerts (Top)
When Claude Code analyzes the portfolio, recommendations are saved to `portfolio.json` and displayed at the top of the dashboard. Includes buy/sell/hold alerts with reasoning.

### 2. KPI Header (6 Cards)
- **Total Value** — USD & ILS
- **Total P&L** — absolute + percentage
- **Today's Change** — real-time
- **Sharpe Ratio** — risk-adjusted performance
- **Portfolio Beta** — market sensitivity
- **Max Drawdown** — worst decline

### 3. Period Performance
P&L for: Today, 1 Week, 1 Month, 3 Months, 6 Months — each showing:
- USD nominal + percentage
- ILS nominal + percentage
- **FX Impact badge** — shows how USD/ILS exchange rate affected returns

### 4. Performance Chart
Interactive line chart: Portfolio cumulative return vs S&P 500 benchmark. Switchable to daily returns bar chart. Built-in range selector (1M/3M/6M/YTD/ALL).

### 5. Allocation
- **Donut chart** — toggleable by: Ticker / Sector / Asset Type
- **Sector bar chart** — horizontal bars showing sector weights

### 6. P&L Analysis
- **Waterfall bar chart** — P&L per holding, sorted, color-coded
- **Treemap** — hierarchical view: sector → ticker, size = value, color = P&L%

### 7. Risk Analytics
- **Risk/Return scatter** — X=volatility, Y=return, bubble size=weight, color=sector
- **Correlation heatmap** — pairwise correlation matrix
- **Additional metrics cards:** Alpha, Ann. Volatility, Sortino, Calmar

### 8. Holdings Table
Full sortable table with conditional formatting (green/red P&L), AI recommendations from Extrade Pro, portfolio weights.

### 9. Stock Drill-Down
Select any holding to see:
- **Candlestick chart** with volume bars, 20/50-day moving averages, cost price line
- **Position summary** — shares, cost, current, P&L
- **Individual risk metrics** — beta, Sharpe, volatility, max drawdown

---

## Data Sources

### Current Sources

| Source | What We Get | How | Reliability |
|--------|-------------|-----|-------------|
| **Yahoo Finance v8/chart API** | Live prices, OHLCV history, 52-week range, volume, daily change | Direct HTTP to `query1.finance.yahoo.com` | High — unofficial but stable. Works via `requests` library (yfinance has SSL issues with corporate proxy) |
| **Yahoo Finance USDILS=X** | USD/ILS exchange rate (live + historical) | Same v8/chart API | High |
| **Extrade Pro (Excel export)** | Initial portfolio snapshot: holdings, cost prices, AI recommendations | Manual export → parsed by `data_loader.py` | One-time — subsequent updates via JSON |
| **portfolio.json** | Current holdings, quantities, cost prices | Direct JSON file, updated by Claude Code | Always current |
| **Claude Code (Web Search)** | Analyst forecasts, market news, sentiment, fundamental analysis | On-demand when user requests portfolio review | Depends on sources found |

### API Endpoint Details

**v8 Chart API (currently used):**
```
GET https://query1.finance.yahoo.com/v8/finance/chart/{ticker}
    ?range=1y          # 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, max
    &interval=1d       # 1d, 1wk, 1mo
```
Returns: timestamps, OHLCV, meta (price, 52w range, volume, currency)

**v10 quoteSummary API (NOT currently used — requires cookie auth):**
```
GET https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}
    ?modules=assetProfile,defaultKeyStatistics,financialData
```
Could provide: sector, industry, beta, P/E, dividend yield, market cap — but requires crumb/cookie authentication which is unreliable.

### Potential Additional Data Sources

| Source | What It Provides | Difficulty | Value |
|--------|-----------------|------------|-------|
| **Alpha Vantage API** (free key) | Fundamentals, earnings, balance sheet, income statement, technical indicators | Easy — REST API with free tier (25 calls/day) | High — fills the gap left by Yahoo v10 |
| **Financial Modeling Prep API** | P/E, P/B, DCF valuation, analyst estimates, financial statements | Easy — REST API, free tier available | High — deep fundamental data |
| **FRED (Federal Reserve)** | Interest rates, inflation, GDP, economic indicators | Easy — free API | Medium — macro context for portfolio decisions |
| **News API / Google News** | Real-time news headlines per ticker | Easy — various free APIs | Medium — sentiment context |
| **Fear & Greed Index** (CNN) | Market sentiment gauge | Easy — scraping or API | Low-Medium — general market mood |
| **Finviz** | Stock screener data, analyst ratings, technical indicators | Medium — scraping needed | High — concentrated financial data |
| **SEC EDGAR** | Company filings (10-K, 10-Q, 8-K) | Medium — XML parsing | Medium — deep due diligence |
| **TASE (Tel Aviv Stock Exchange)** | Israeli stock data for 5108.TA and future Israeli holdings | Hard — no public API, scraping needed | High — currently we have no live data for Israeli ETFs |
| **Polygon.io** | Real-time and historical market data | Easy — REST API, free tier | High — could replace Yahoo Finance |
| **OpenBB Platform** | Open-source financial data aggregator (combines multiple sources) | Medium — Python library | Very High — one library to access many sources |

---

## How Portfolio Updates Work

### Workflow
1. User tells Claude Code: "bought 5 shares of TSLA at $250"
2. Claude Code updates `portfolio.json`:
   - Adds/modifies the holding entry
   - Adds a transaction record with date
   - If it's a new ticker: adds to `config.py` (SECTOR_MAP, ASSET_TYPE_MAP, DISPLAY_NAMES)
3. Dashboard automatically picks up changes on next refresh

### Transaction Format in portfolio.json
```json
{
  "date": "2026-03-26",
  "type": "buy",
  "ticker": "TSLA",
  "quantity": 5,
  "price": 250.00,
  "description": "Bought 5 shares of Tesla"
}
```

### Recommendation Updates
When Claude Code performs market analysis, it updates the `recommendations` section:
```json
{
  "recommendations": {
    "updated": "2026-03-25",
    "summary": "Overall market outlook summary...",
    "alerts": [
      {
        "ticker": "CPNG",
        "type": "sell",
        "message": "Coupang faces increased competition in Korea..."
      }
    ]
  }
}
```

---

## GitHub Projects — Inspiration & Comparison

### Similar Projects Analyzed

1. **[BouaklineMahdi/Stock-Price-Prediction-Portfolio-Management-Dashboard](https://github.com/BouaklineMahdi/Stock-Price-Prediction-Portfolio-Management-Dashboard)**
   - Tech: Dash + Plotly, LSTM prediction models
   - Features: Candlestick charts, moving averages, portfolio management, email alerts
   - What we can learn: **LSTM price prediction model** — could add a "forecast" section

2. **[jarvijaakko/Portfolio_analysis_dashboard](https://github.com/jarvijaakko/Portfolio_analysis_dashboard)**
   - Tech: Plotly/Dash
   - Features: Portfolio metrics, dynamic visualizations
   - What we can learn: **Multi-portfolio comparison** — could compare current vs hypothetical allocations

3. **[kdboller/pythonsp500-plotly-dash](https://github.com/kdboller/pythonsp500-plotly-dash)**
   - Tech: Jupyter + Dash
   - Features: S&P 500 analysis, portfolio tracking
   - What we can learn: **Benchmark comparison depth** — compare vs multiple benchmarks (QQQ, ACWI, etc.)

4. **[ScottMorgan85/portfolio-dashboard](https://github.com/ScottMorgan85/portfolio-dashboard)**
   - Tech: Streamlit
   - Features: Asset overview, historical performance, sentiment analysis, predictive forecasting
   - What we can learn: **Sentiment analysis integration** — scrape news and display sentiment scores

5. **[ranaroussi/yfinance](https://github.com/ranaroussi/yfinance)**
   - The library we couldn't use directly (SSL), but its API patterns inform our direct HTTP approach

---

## Recommended Improvements (Roadmap)

### High Priority
- [ ] **Alpha Vantage integration** — Add P/E, EPS, dividend yield, market cap per holding. Free API key, 25 calls/day is sufficient for 14 holdings
- [ ] **Israeli ETF live data** — Find a source for TASE data (currently using static Excel data for 5108.TA)
- [ ] **Transaction history tracking** — Track buy/sell history over time, compute time-weighted return (TWR)
- [ ] **Earnings calendar** — Show upcoming earnings dates for held stocks
- [ ] **Multiple portfolio snapshots** — Save daily snapshots to track portfolio value over time (currently we rely on Yahoo historical prices with current weights)

### Medium Priority
- [ ] **Sector deep-dive page** — Click a sector to see detailed breakdown, top holdings in sector ETFs
- [ ] **Dividend tracker** — Show dividend income, yield, ex-dates, payment schedule
- [ ] **Monte Carlo simulation** — Project future portfolio value with confidence intervals
- [ ] **News feed** — Real-time news headlines per ticker using News API
- [ ] **Rebalancing suggestions** — Show how far current allocation drifts from a target allocation
- [ ] **Tax lot tracking** — Track cost basis per purchase for tax optimization (FIFO/LIFO)
- [ ] **Multi-currency P&L** — Separate "investment P&L" from "FX P&L" more precisely

### Nice to Have
- [ ] **PDF report export** — Generate a monthly/weekly PDF report of portfolio status
- [ ] **Email/Slack alerts** — Notify when a holding drops/rises beyond a threshold
- [ ] **Backtesting engine** — "What if I had bought X instead of Y 6 months ago?"
- [ ] **Options tracking** — If the portfolio expands to include options
- [ ] **Mobile-responsive design** — Optimize for phone viewing
- [ ] **OpenBB integration** — Replace multiple API sources with one unified platform
- [ ] **Benchmark customization** — Compare vs custom benchmarks (60/40 portfolio, ACWI, etc.)

---

## Technical Notes

### Why Streamlit + Plotly?
- **Streamlit**: Zero-config web server, native Python, perfect for personal dashboards. No need for domain, hosting, or deployment complexity.
- **Plotly**: Best-in-class interactive financial charts. Built-in dark theme, hover tooltips, zoom/pan, range selectors.
- **Alternative considered**: Dash (more powerful but heavier setup) — overkill for a personal tool.

### Why Direct Yahoo API vs yfinance Library?
The `yfinance` Python library uses `curl_cffi` internally which fails with SSL certificate errors through corporate proxy/VPN. Direct `requests.get()` to `query1.finance.yahoo.com` works fine because Python's `requests` library uses the system certificate store.

### Israeli ETF Limitation
The Kesem Insurance ETF (`5108.TA`) is not available on Yahoo Finance. No free API provides TASE ETF data reliably. Current workaround: store price in `portfolio.json` and update manually when refreshing data.

### Caching Strategy
- `@st.cache_data(ttl=300)` — API data cached for 5 minutes
- Portfolio JSON is re-read on each session (no cache — should always be fresh)
- "Refresh Data" button in sidebar clears all caches

---

## Running the Dashboard

```bash
cd "Amit Invests"
streamlit run app.py
```

Opens at `http://localhost:8501` — no deployment needed. Three pages in the sidebar: **Portfolio**, **Recommendations**, **Settings**.

## Autonomous Pipeline (Setup)

The app is wired to an automated daily pipeline. UI stays simple; intelligence is behind the scenes.

### Stack (current — as of Apr 2026)
- **Portfolio sync** — **manual CSV upload** via the `📥 Import` page in the UI. Auto-handles multi-lot aggregation, Yahoo Finance / Extrade Pro formats, Hebrew headers, Israeli agorot conversion. (Browser-use + Gemini scraping was built but dropped — CAPTCHA/2FA too fragile; manual is more reliable.)
- **AI recommendations (daily at 16:35 IDT)** — `scripts/run_recommendations.py` calls **Gemini directly** via `langchain-google-genai`. One call per (persona × ticker) — 5 personas × 16 holdings = 80 calls + 1 for new ideas + 1 for summary = ~82 calls per run. Model: **`gemini-flash-latest`** (auto-aliased by Google to the newest stable Flash — upgrades for free). We do **not** use virattt/ai-hedge-fund — the persona prompt system is ours, inspired by theirs but standalone.
- **Telegram digest (daily at 16:35 IDT, after recommendations)** — bare Telegram Bot API call; no extra dependency.
- **Snapshot** — `scripts/snapshot_portfolio.py` appends daily value/sector-weights to `snapshots.jsonl`.
- **Scheduling** — macOS `launchd` plist runs the pipeline at **16:35 Israel time = 5 min after US market open**.

### Available personas (12; pick any subset in Settings)

| Persona | Hebrew display | Style |
|---|---|---|
| `warren_buffett` | וורן באפט (ערך) | Durable moats, quality, long-term |
| `charlie_munger` | צ'ארלי מנגר (ערך) | Quality-at-fair-price, margin of safety |
| `cathie_wood` | קתי ווד (חדשנות) | AI / innovation / disruption |
| `peter_lynch` | פיטר לינץ' (צמיחה) | GARP — growth at reasonable price |
| `michael_burry` | מייקל בורי (קונטרי) | Contrarian, bubble-spotter |
| `ben_graham` | בן גראהם (ערך עמוק) | Deep value, net-nets |
| `technical_analyst` | ניתוח טכני | MA, RSI, patterns, momentum |
| `fundamentals_analyst` | ניתוח פונדמנטלי | P/E, EPS growth, margins, debt |
| `valuation` | הערכת שווי | DCF, EV/EBITDA, SOTP |
| `sentiment` | סנטימנט שוק | News, analyst ratings, social |
| `macro` | מאקרו | Rates, inflation, geopolitics |
| `risk_manager` | מנהל סיכונים | Position sizing, caps, drawdowns |

Your default active set: **Buffett, Munger, Wood, Lynch, Risk Manager**. Swap/add from Settings page.

### First-time setup

```bash
# 1. Python 3.12 + venv (Streamlit + browser deps)
brew install python@3.12
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Credentials — copy .env.example to .env and fill in:
#    GEMINI_API_KEY (required), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
cp .env.example .env && $EDITOR .env

# 3. Test each script manually
.venv/bin/python scripts/run_recommendations.py --once    # real Gemini run
.venv/bin/python scripts/snapshot_portfolio.py             # record today's value
.venv/bin/python scripts/telegram_digest.py --once         # push to Telegram

# 4. Enable daily scheduler (16:35 Israel time)
cp launchd/com.amit.invest.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.amit.invest.daily.plist

# 5. Run the UI
.venv/bin/python -m streamlit run app.py
```

### Files
| Path | Role |
|---|---|
| `app.py` | Streamlit nav entry point (top-tab navigation) |
| `pages/portfolio.py` | Portfolio view — sticky header, 8 KPIs, rebalancing, Risk tabs |
| `pages/recommendations.py` | AI recommendations — block grid, persona reasoning |
| `pages/import_csv.py` | CSV upload with diff/apply flow |
| `pages/inbox.py` | Timeline of events (syncs, runs, changes) |
| `pages/settings.py` | Personality profile editor + Telegram setup guide |
| `pages/explainer.py` | How It Works — agent deep-dives, last-run status |
| `_bootstrap.py` | Shared `ROOT` + helpers (at project root, not in `pages/`) |
| `settings.json` | Current personality profile |
| `portfolio.json` | Holdings (edited via Import page or manually) |
| `recommendations.json` | Written by `scripts/run_recommendations.py` |
| `snapshots.jsonl` | Daily value history |
| `scripts/run_recommendations.py` | Calls Gemini per (persona × ticker) |
| `scripts/snapshot_portfolio.py` | Records daily portfolio value |
| `scripts/telegram_digest.py` | Telegram delivery |
| `scripts/run_daily.sh` | Pipeline: recommend → snapshot → digest |
| `launchd/com.amit.invest.daily.plist` | 16:35 IDT schedule |
| `design_demos/` | 3 HTML mockups of the candidate UI redesigns |

## Updating Portfolio

Tell Claude Code:
- "קניתי 10 מניות של TSLA ב-250 דולר"
- "מכרתי את כל הCPNG"
- "תעדכן את הדאשבורד ותנתח את התיק"

Claude Code will update `portfolio.json` and optionally refresh recommendations.
