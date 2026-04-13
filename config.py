"""
Portfolio Dashboard Configuration — English
"""

# ─── Ticker Mapping (Extrade Pro name → Yahoo Finance ticker) ────────────────
TICKER_MAP = {
    "ALPHABET INC-CL A": "GOOGL",
    "AMAZON COM INC": "AMZN",
    "BROOKFIELD ASSET MGT": "BAM",
    "BROOKFIELD CORP": "BN",
    "Coupang, Inc.": "CPNG",
    "ETHA": "ETHA",
    "HEALTH CARE SELECT SECTOR": "XLV",
    "IBIT": "IBIT",
    "INVESCO NASDAQ 100 ETF": "QQQM",
    "ISHARES DJ US AEROSPACE & DF": "ITA",
    "NVIDIA CORP": "NVDA",
    "SPDR S&P 500 ETF TRUST": "SPY",
    "SPROTT URANIUM MINERS": "URNM",
    "VANECK URANIUM + NUCLEAR ENER": "NLR",
    "VANGUARD S&P 500 ETF": "VOO",
    "KESEM ETF 4A TA-INSURANCE": "5108.TA",
    "KESEM KSM-F34": "KSM-F34.TA",
    "KESEM KSM-F77": "KSM-F77.TA",
}

# ─── Sector Classification (English) ────────────────────────────────────────
SECTOR_MAP = {
    "GOOGL": "Technology",
    "AMZN": "Consumer Discretionary",
    "BAM": "Financials",
    "BN": "Financials",
    "CPNG": "Consumer Discretionary",
    "ETHA": "Crypto",
    "XLV": "Healthcare",
    "IBIT": "Crypto",
    "QQQM": "Broad Market",
    "ITA": "Aerospace & Defense",
    "NVDA": "Technology",
    "SPY": "Broad Market",
    "URNM": "Energy / Uranium",
    "NLR": "Energy / Nuclear",
    "VOO": "Broad Market",
    "5108.TA": "Insurance (Israel)",
    "KSM-F34.TA": "Broad Market (Israel)",
    "KSM-F77.TA": "Broad Market (Israel)",
}

# ─── Asset Type Classification (English) ────────────────────────────────────
ASSET_TYPE_MAP = {
    "GOOGL": "US Stock",
    "AMZN": "US Stock",
    "BAM": "US Stock",
    "BN": "US Stock",
    "CPNG": "US Stock",
    "ETHA": "Crypto ETF",
    "XLV": "Sector ETF",
    "IBIT": "Crypto ETF",
    "QQQM": "Broad Market ETF",
    "ITA": "Sector ETF",
    "NVDA": "US Stock",
    "SPY": "Broad Market ETF",
    "URNM": "Sector ETF",
    "NLR": "Sector ETF",
    "VOO": "Broad Market ETF",
    "5108.TA": "Israeli ETF",
    "KSM-F34.TA": "Israeli ETF",
    "KSM-F77.TA": "Israeli ETF",
}

# ─── Display Names (English) ─────────────────────────────────────────────────
DISPLAY_NAMES = {
    "GOOGL": "Alphabet (Google)",
    "AMZN": "Amazon",
    "BAM": "Brookfield Asset Mgmt",
    "BN": "Brookfield Corp",
    "CPNG": "Coupang",
    "ETHA": "Ethereum ETF",
    "XLV": "Healthcare ETF",
    "IBIT": "Bitcoin ETF",
    "QQQM": "Nasdaq 100 ETF",
    "ITA": "Aerospace & Defense ETF",
    "NVDA": "Nvidia",
    "SPY": "S&P 500 ETF (SPY)",
    "URNM": "Uranium Miners ETF",
    "NLR": "Nuclear Energy ETF",
    "VOO": "S&P 500 ETF (VOO)",
    "5108.TA": "Israel Insurance Index",
    "KSM-F34.TA": "Kesem F34 (Israel)",
    "KSM-F77.TA": "Kesem F77 (Israel)",
}

# ─── Sector Colors ───────────────────────────────────────────────────────────
SECTOR_COLORS = {
    "Technology": "#2563eb",
    "Consumer Discretionary": "#7c3aed",
    "Financials": "#0891b2",
    "Crypto": "#ea580c",
    "Healthcare": "#059669",
    "Broad Market": "#64748b",
    "Aerospace & Defense": "#ca8a04",
    "Energy / Uranium": "#dc2626",
    "Energy / Nuclear": "#e67700",
    "Insurance (Israel)": "#16a34a",
    "Broad Market (Israel)": "#0f766e",
}

# ─── Theme Colors (Portfolio Pro Light) ───────────────────────────────────────
COLORS = {
    "bg": "#F7F7F9",
    "card_bg": "#ffffff",
    "card_border": "rgba(226,232,240,0.6)",
    "text": "#0f172a",
    "text_secondary": "#94a3b8",
    "positive": "#059669",
    "negative": "#e11d48",
    "accent": "#4F46E5",
    "warning": "#f59e0b",
    "chart_grid": "#e2e8f0",
}

# ─── Chart Colors ────────────────────────────────────────────────────────────
CHART_COLORS = [
    "#2563eb", "#059669", "#7c3aed", "#ea580c", "#dc2626",
    "#0891b2", "#16a34a", "#ca8a04", "#e67700", "#64748b",
    "#be185d", "#1d4ed8", "#6366f1", "#10b981", "#db2777", "#0284c7",
]

# ─── API Configuration ───────────────────────────────────────────────────────
YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YF_HEADERS = {"User-Agent": "Mozilla/5.0"}
USDILS_TICKER = "USDILS=X"

# ─── Financial Constants ─────────────────────────────────────────────────────
RISK_FREE_RATE = 0.045
TRADING_DAYS_YEAR = 252

# ─── Israeli ETF ─────────────────────────────────────────────────────────────
ISRAELI_TICKERS = {"KSM-F34.TA", "KSM-F77.TA"}

# Yahoo Finance returns prices in agorot (1/100 ILS) for these tickers — divide by 100
AGOROT_TICKERS = {"KSM-F34.TA", "KSM-F77.TA"}
