# AMIT CAPITAL — Roadmap & Action Plan

> Last updated: 2026-04-14
> Status: Draft — pending prioritization

---

## Vision

Turn "Amit Capital" from a portfolio tracker with hallucinating personas into a
**data-driven investment mentor** that:
1. Teaches you something real every day
2. Bases every recommendation on verifiable data
3. Tracks its own accuracy over time so you know who to trust
4. Gives you transparent reasoning you can challenge

---

## Phase 0 — Foundation Fixes (1-2 days)

> Already partially done. Quick wins that unblock everything else.

### 0.1 Inject real financial data into Gemini prompts
- **File:** `scripts/run_recommendations.py` → `_call_persona()`
- Fetch from Yahoo Finance (already have the API):
  - Current price, 52w high/low, daily change %
  - MA50, MA200, RSI(14) — compute locally from OHLCV
- Inject as structured block before the analysis request
- **Impact:** Eliminates ~60% of hallucinations immediately

### 0.2 Fix aggregation formula
- **File:** `scripts/run_recommendations.py` → `_aggregate_verdict()`
- Change from "average of winning side" to "weighted average of ALL personas"
- Add unanimity score: 9/9 agree = high confidence, 5/4 split = flag as uncertain
- **Impact:** Conviction scores become meaningful

### 0.3 Expand persona system prompts
- **File:** `scripts/run_recommendations.py` → `PERSONA_SYSTEM_PROMPTS`
- From 1 sentence → 10-15 rules with quantitative thresholds
- Example for Buffett: "SELL if P/E > 25 AND no moat, HOLD if P/E 15-25 with moat..."
- Add 2-3 few-shot examples per persona
- **Impact:** Personas behave like actual frameworks, not roleplay

---

## Phase 1 — Real Data Pipeline (1 week)

> The single highest-impact upgrade. Everything downstream improves.

### 1.1 Alpha Vantage integration (fundamentals)
- **New file:** `data_loader_fundamentals.py`
- Free tier: 25 calls/minute, 500/day
- Fetch per ticker: P/E, PEG, EPS, revenue growth, profit margin, debt/equity, ROE, dividend yield
- Cache in `fundamentals_cache.json` (refresh daily)
- **Tickers that benefit most:** GOOGL, AMZN, NVDA, CPNG, BN, BAM

### 1.2 Macro data from FRED API
- **New file:** `data_loader_macro.py`
- Free, unlimited: Federal Funds Rate, 10Y Treasury, CPI, VIX, USD/ILS
- Cache in `macro_cache.json`
- Inject into every Gemini prompt as "Market Environment" block
- **Impact:** Macro persona gets real data; all personas get economic context

### 1.3 News headlines (free tier)
- Source: Google News RSS or NewsAPI.org (free: 100 requests/day)
- Fetch 3-5 recent headlines per ticker
- Inject into Sentiment persona prompt only
- **Impact:** Sentiment analysis becomes grounded in actual events

### 1.4 Analyst consensus
- Source: Yahoo Finance quoteSummary (already partially available in v8 API)
- Fields: analyst target price, recommendation mean, number of analysts
- Inject into Fundamentals and Valuation personas
- **Impact:** Cross-reference LLM verdict with Wall Street consensus

### 1.5 Persona-specific data injection
- Technical Analyst gets: price, MA20/50/200, RSI, volume, support/resistance
- Fundamentals Analyst gets: P/E, EPS, margins, growth, debt, ROE
- Risk Manager gets: portfolio weights, beta, correlation, VaR estimate
- Sentiment gets: news headlines, analyst consensus, Fear & Greed
- Value investors (Buffett/Munger/Graham) get: all fundamentals + moat assessment
- Growth investors (Wood/Lynch) get: revenue growth, TAM, sector trends
- Macro gets: FRED data (rates, inflation, GDP, yield curve)

---

## Phase 2 — Telegram Mentor (3-5 days)

> Transform daily messages from "verdict dump" to "daily financial education".

### 2.1 Market Context Block (new)
```
📊 Market Today (Apr 14)
S&P 500: +0.8% | Nasdaq: +1.2% | VIX: 14.2 (low fear)
Fed Rate: 4.75% | 10Y: 4.1% | USD/ILS: 3.02

Key event: NVDA earnings tomorrow — expect volatility
Impact on your portfolio: NVDA is 4.2% of your portfolio.
If it drops 10%, your total portfolio drops ~0.4%.
```

### 2.2 Daily Lesson (new — rotating 100+ topics)
```
📚 Daily Lesson #42: What is the Sharpe Ratio?

The Sharpe Ratio measures return per unit of risk.
Formula: (Return - Risk Free Rate) / Volatility

Your portfolio Sharpe: 1.2 (good — above 1.0 is considered strong)
SPY Sharpe: 0.9 — you're beating the benchmark on risk-adjusted basis.

Tomorrow: What is Beta and why it matters for your Israeli bonds.
```

Topics database (store in `lessons.json`):
- Valuation: P/E, P/B, PEG, DCF, EV/EBITDA
- Risk: Sharpe, Sortino, Beta, Alpha, Max Drawdown, VaR
- Technical: MA, RSI, MACD, Bollinger, Volume, Support/Resistance
- Portfolio: Diversification, Correlation, Rebalancing, Dollar Cost Averaging
- Macro: Interest rates, Inflation, Yield curve, Business cycles
- Behavioral: Loss aversion, FOMO, Anchoring, Confirmation bias
- Israeli market: TASE structure, Shekel bonds, Agorot pricing
- Strategy: Value vs Growth, Momentum, Factor investing

### 2.3 Change Tracking (new)
```
🔄 Verdict Changes Since Yesterday:
NVDA: BUY 88% → HOLD 62% ⬇️
  Reason: P/E expanded to 55, RSI entered overbought (72)
  
URNM: HOLD 55% → BUY 78% ⬆️
  Reason: Uranium spot price broke $90, new reactor contracts announced
```

Store previous day's recommendations in `recommendations_prev.json`
Compare and highlight changes with reasons.

### 2.4 Ideas Scorecard (new)
```
💡 Ideas Tracker (last 30 days):
MSFT (suggested Mar 15): +8.2% ✅ Still BUY
TSM (suggested Mar 22): -2.1% 🟡 Now HOLD
META (suggested Apr 1): +4.5% ✅ Still BUY

Hit rate: 2/3 (67%) — above random (50%)
```

Store all new_ideas suggestions with date and price at suggestion time.
Track performance weekly.

### 2.5 Improved Charts
- Add earnings date marker on candlestick charts
- Add sector heatmap (one image per week)
- Add portfolio allocation pie chart (monthly)

---

## Phase 3 — Beyond Personas (1-2 weeks)

> The real architectural upgrade: separate DATA from OPINION.

### 3.1 Hybrid Architecture: Algorithms + LLM Synthesis

**Current flow (broken):**
```
User Profile + Ticker Name → LLM → Hallucinated Analysis
```

**New flow (data-driven):**
```
                    ┌─→ Valuation Score (algorithmic)
                    │   DCF estimate, P/E vs sector, PEG ratio
                    │
                    ├─→ Technical Score (algorithmic)
                    │   MA crossovers, RSI zones, volume trend
                    │
Yahoo Finance ──────├─→ Risk Score (algorithmic)
Alpha Vantage       │   Beta, correlation, concentration, VaR
FRED                │
                    ├─→ Sentiment Score (LLM-assisted)
                    │   News headlines + analyst consensus → Gemini
                    │
                    ├─→ Macro Score (algorithmic + LLM)
                    │   Rate environment, yield curve, inflation trend
                    │
                    └─→ Quality Score (algorithmic)
                        ROE, debt/equity, margin stability, moat proxy

All 6 scores ──→ Gemini Synthesis ──→ Final Verdict + Hebrew Rationale
```

**Key insight:** The LLM does what it's good at (synthesis, language, reasoning
over provided data) and NOT what it's bad at (inventing financial metrics).

### 3.2 Scoring Engine (new file: `scoring_engine.py`)
Each score is 0-100, computed algorithmically:

**Valuation Score:**
```python
def valuation_score(pe, peg, sector_avg_pe, dcf_estimate, current_price):
    score = 50  # neutral
    if pe < sector_avg_pe * 0.8: score += 20  # cheap vs sector
    if peg < 1.0: score += 15  # growth at reasonable price
    if dcf_estimate > current_price * 1.2: score += 15  # 20%+ upside
    # ... more rules
    return min(100, max(0, score))
```

**Technical Score:**
```python
def technical_score(price, ma50, ma200, rsi, volume_trend):
    score = 50
    if price > ma50 > ma200: score += 20  # golden cross territory
    if 30 < rsi < 70: score += 10  # not overbought/oversold
    if volume_trend > 0: score += 10  # increasing volume
    # ...
    return min(100, max(0, score))
```

**Risk Score** (portfolio-aware):
```python
def risk_score(ticker, portfolio_weight, beta, correlation_to_spy, sector_concentration):
    score = 50
    if portfolio_weight > 15: score -= 20  # overweight
    if beta > 1.5: score -= 15  # too volatile
    if sector_concentration > 30: score -= 15  # sector risk
    # ...
    return min(100, max(0, score))
```

### 3.3 LLM Synthesis (replaces current persona system)
Instead of 9 personas each hallucinating independently:

```python
prompt = f"""
You are a financial analyst writing in Hebrew for a conservative investor.

Here is the complete data for {ticker}:

VALUATION: Score {valuation_score}/100
- P/E: {pe} (sector avg: {sector_pe})
- PEG: {peg}
- DCF fair value estimate: ${dcf}
- Current price: ${price} ({upside_pct}% upside/downside)

TECHNICAL: Score {technical_score}/100
- Price: ${price} | MA50: ${ma50} | MA200: ${ma200}
- RSI(14): {rsi} | Volume trend: {vol_trend}
- Pattern: {pattern_description}

RISK: Score {risk_score}/100
- Portfolio weight: {weight}% | Beta: {beta}
- Sector concentration: {sector_pct}%
- Correlation to SPY: {corr}

SENTIMENT: Score {sentiment_score}/100
- Analyst consensus: {consensus} | Target: ${target}
- Recent headlines: {headlines}

MACRO ENVIRONMENT:
- Fed Rate: {fed_rate}% | 10Y: {ten_y}%
- VIX: {vix} | Inflation: {cpi}%

USER PROFILE: {preamble}

Based on ALL the data above, provide:
1. Verdict: BUY / HOLD / SELL
2. Conviction: 0-100 (how confident, based on data alignment)
3. Key reason (1 sentence)
4. Risk warning (1 sentence)
5. Rationale (2-3 sentences in Hebrew)
"""
```

**One Gemini call per ticker** instead of 9. Cheaper, faster, and grounded in data.

### 3.4 Optional: Keep Personas as "Commentary Layer"
If you still want the persona flavor:
- Run the scoring engine first (algorithmic)
- Then ask 2-3 personas to **comment on the scores** (not analyze from scratch)
- "Given that NVDA has a Valuation Score of 35/100 (expensive) but a Technical
  Score of 85/100 (strong momentum), what would Warren Buffett say?"
- This keeps the personality while eliminating hallucination

---

## Phase 4 — Dashboard Upgrades (1 week)

### 4.1 Score Dashboard (new view: `views/scores.py`)
Visual display of all 6 scores per holding:
- Radar/spider chart: Valuation, Technical, Risk, Sentiment, Macro, Quality
- Color-coded: green (>70), yellow (40-70), red (<40)
- Compare any two holdings side by side

### 4.2 Accuracy Tracker (new view or section)
- Track every verdict historically
- Compare "what we said" vs "what happened" after 1w, 1m, 3m
- Display hit rate per scoring method
- Build trust through transparency

### 4.3 Rebalancing Suggestions
- Define target allocation per sector
- Show drift from target
- Suggest specific trades: "Sell 2 shares of SPY, buy 5 shares of XLV"
- Include tax impact estimate (if cost basis available)

### 4.4 Earnings Calendar
- Show upcoming earnings dates for all holdings
- Historical earnings surprise (beat/miss)
- Pre-earnings volatility warning

### 4.5 Correlation Heatmap Improvements
- Add Israeli tickers (when data available)
- Show rolling 30-day correlation (not just static)
- Highlight dangerous correlations (>0.85)

---

## Phase 5 — Advanced Features (2-4 weeks)

### 5.1 OpenBB Integration
- Replace Yahoo Finance + Alpha Vantage + FRED with OpenBB's unified API
- Benefits: 350+ datasets, normalized data, MCP server support
- Reduces code complexity: one library instead of three
- Ref: https://openbb.co/ | https://github.com/OpenBB-finance/OpenBB

### 5.2 Backtesting Engine
- "If I had followed these recommendations for the last 6 months, how would I have done?"
- Compare: AI recommendations vs buy-and-hold SPY vs random
- Monte Carlo simulation: 1000 paths for next 12 months

### 5.3 Multi-Model Comparison
- Run same analysis through Gemini + Claude + GPT-4
- Compare verdicts — agreement = high confidence, disagreement = flag
- Track which model is most accurate over time

### 5.4 Interactive Telegram Bot
- `/check NVDA` — get instant analysis for any ticker
- `/rebalance` — see current drift and suggestions
- `/learn` — get today's lesson on demand
- `/performance` — portfolio vs benchmark chart

### 5.5 PDF Weekly Report
- Auto-generated every Friday
- Cover page, key metrics, per-holding analysis, charts
- Attach to Telegram or email

---

## Alternative Approaches: What Others Do

### Option A: ai-hedge-fund (virattt) Architecture
- **Repo:** https://github.com/virattt/ai-hedge-fund (51K+ stars)
- **How it differs:** Uses LangGraph for agent orchestration, Financial Datasets API
  for real data, React frontend with drag-and-drop agent editor
- **Key advantage:** Agents get REAL financial data (prices, metrics, insider trades,
  news) injected automatically via a data caching layer
- **18 agents:** 12 investor personas + 6 technical agents
- **What to steal:** Their data pipeline pattern — fetch → cache → inject into prompt
- **Limitation:** English only, no Hebrew, no Israeli market support

### Option B: FinRobot (AI4Finance Foundation)
- **Repo:** https://github.com/AI4Finance-Foundation/FinRobot
- **How it differs:** Uses "Chain of Thought" agents — Data-CoT, Concept-CoT, Thesis-CoT
- **Key advantage:** Multi-source data integration, generates full equity research reports
- **What to steal:** The 3-agent CoT architecture (gather data → reason → synthesize)
- **Limitation:** Academic, complex setup, overkill for personal portfolio

### Option C: OpenBB + Custom Agents
- **Site:** https://openbb.co/
- **How it differs:** Not an AI system — it's a data platform with MCP server
- **Key advantage:** 350+ data sources through one unified Python/REST API
- **What to steal:** Use OpenBB as your data layer, keep your custom analysis on top
- **Best fit:** Replace your Yahoo Finance + Alpha Vantage calls with OpenBB

### Option D: Scoring Engine (no personas at all)
- **Concept:** Pure algorithmic scoring — no LLM for analysis, only for language
- **How it works:** 6 quantitative scores → weighted average → verdict
- **Key advantage:** 100% reproducible, no hallucination, instant (no API calls)
- **Limitation:** Loses the "personality" and narrative quality
- **Best fit:** Use as the backbone, add LLM only for Hebrew rationale generation

### Recommended: Hybrid (Phase 3 above)
Combine Option D (scoring engine) + Option A's data pipeline + your existing
persona flavor. This gives you:
- Real data (from ai-hedge-fund's approach)
- Transparent scoring (from Option D)
- Hebrew personality (your unique value-add)
- Educational daily messages (your mentor vision)

---

## Priority Matrix

| Phase | Effort | Impact | Priority |
|-------|--------|--------|----------|
| 0 — Foundation fixes | 1-2 days | High | ★★★★★ |
| 1 — Real data pipeline | 1 week | Very High | ★★★★★ |
| 2 — Telegram mentor | 3-5 days | High | ★★★★☆ |
| 3 — Scoring engine | 1-2 weeks | Very High | ★★★★★ |
| 4 — Dashboard upgrades | 1 week | Medium | ★★★☆☆ |
| 5 — Advanced features | 2-4 weeks | Medium | ★★☆☆☆ |

**Recommended order:** Phase 0 → Phase 1 → Phase 3 → Phase 2 → Phase 4 → Phase 5

Phase 3 before Phase 2 because the Telegram messages should use the new
scoring engine, not the old persona system.

---

## Success Metrics

After implementation, track these weekly:

1. **Accuracy:** % of BUY verdicts that were profitable after 30 days
2. **Calibration:** Does 80% conviction mean ~80% accuracy?
3. **Coverage:** % of holdings with complete fundamental data
4. **Engagement:** Do you read the Telegram messages? Do you act on them?
5. **Learning:** Can you explain Sharpe Ratio, Beta, RSI after 3 months of lessons?

---

## Non-Goals (what we're NOT building)

- Automated trading — this is a decision-support tool, not a trading bot
- Real-time alerts — daily cadence is enough for a conservative investor
- Social features — this is personal, not social
- Mobile app — Telegram + web dashboard is sufficient
