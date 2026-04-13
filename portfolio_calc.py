"""
Financial calculations: risk metrics, returns, correlations.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from config import RISK_FREE_RATE, TRADING_DAYS_YEAR, ISRAELI_TICKERS


def compute_daily_returns(historical: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build a DataFrame of daily returns for all tickers."""
    returns = {}
    for ticker, df in historical.items():
        if ticker in ISRAELI_TICKERS:
            continue
        closes = df["close"].dropna()
        if len(closes) > 1:
            returns[ticker] = closes.pct_change().dropna()
    if not returns:
        return pd.DataFrame()
    return pd.DataFrame(returns).dropna(how="all")


def compute_portfolio_cumulative(daily_returns: pd.DataFrame,
                                 weights: dict[str, float]) -> pd.Series:
    """Compute weighted portfolio cumulative returns."""
    if daily_returns.empty:
        return pd.Series(dtype=float)

    # Normalize weights to only include tickers with return data
    available = [t for t in weights if t in daily_returns.columns]
    if not available:
        return pd.Series(dtype=float)

    total_w = sum(weights[t] for t in available)
    if total_w == 0:
        return pd.Series(dtype=float)

    norm_weights = {t: weights[t] / total_w for t in available}

    # Weighted daily return
    port_daily = sum(daily_returns[t] * norm_weights[t] for t in available)
    cumulative = (1 + port_daily).cumprod() - 1
    return cumulative


def compute_benchmark_cumulative(historical: dict[str, pd.DataFrame],
                                 benchmark: str = "SPY") -> pd.Series:
    """Compute benchmark cumulative returns."""
    if benchmark not in historical:
        return pd.Series(dtype=float)
    closes = historical[benchmark]["close"].dropna()
    if len(closes) < 2:
        return pd.Series(dtype=float)
    daily = closes.pct_change().dropna()
    return (1 + daily).cumprod() - 1


def compute_risk_metrics(portfolio_returns: pd.Series,
                         benchmark_returns: pd.Series = None) -> dict:
    """Compute portfolio-level risk metrics."""
    if portfolio_returns.empty or len(portfolio_returns) < 10:
        return _empty_metrics()

    daily_mean = portfolio_returns.mean()
    daily_std = portfolio_returns.std()

    ann_return = daily_mean * TRADING_DAYS_YEAR
    ann_volatility = daily_std * np.sqrt(TRADING_DAYS_YEAR)

    # Sharpe
    sharpe = (ann_return - RISK_FREE_RATE) / ann_volatility if ann_volatility > 0 else 0

    # Sortino (downside deviation)
    downside = portfolio_returns[portfolio_returns < 0]
    downside_std = downside.std() * np.sqrt(TRADING_DAYS_YEAR) if len(downside) > 0 else ann_volatility
    sortino = (ann_return - RISK_FREE_RATE) / downside_std if downside_std > 0 else 0

    # Max Drawdown
    cumulative = (1 + portfolio_returns).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()

    # Calmar
    calmar = ann_return / abs(max_drawdown) if max_drawdown != 0 else 0

    # Beta & Alpha (vs benchmark)
    beta = 0
    alpha = 0
    if benchmark_returns is not None and len(benchmark_returns) > 10:
        aligned = pd.DataFrame({"port": portfolio_returns, "bench": benchmark_returns}).dropna()
        if len(aligned) > 10:
            cov = aligned["port"].cov(aligned["bench"])
            var_bench = aligned["bench"].var()
            beta = cov / var_bench if var_bench > 0 else 0
            bench_ann = aligned["bench"].mean() * TRADING_DAYS_YEAR
            alpha = ann_return - (RISK_FREE_RATE + beta * (bench_ann - RISK_FREE_RATE))

    return {
        "ann_return": ann_return,
        "ann_volatility": ann_volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "beta": beta,
        "alpha": alpha,
        "daily_mean": daily_mean,
        "daily_std": daily_std,
    }


def _empty_metrics() -> dict:
    return {
        "ann_return": 0, "ann_volatility": 0, "sharpe": 0, "sortino": 0,
        "max_drawdown": 0, "calmar": 0, "beta": 0, "alpha": 0,
        "daily_mean": 0, "daily_std": 0,
    }


def compute_individual_metrics(historical: dict[str, pd.DataFrame],
                               benchmark_hist: dict[str, pd.DataFrame] = None) -> pd.DataFrame:
    """Compute per-holding risk/return metrics."""
    rows = []
    bench_returns = None
    if benchmark_hist and "SPY" in benchmark_hist:
        bench_closes = benchmark_hist["SPY"]["close"].dropna()
        if len(bench_closes) > 1:
            bench_returns = bench_closes.pct_change().dropna()

    for ticker, df in historical.items():
        if ticker in ISRAELI_TICKERS:
            continue
        closes = df["close"].dropna()
        if len(closes) < 10:
            continue

        daily = closes.pct_change().dropna()
        ann_ret = daily.mean() * TRADING_DAYS_YEAR
        ann_vol = daily.std() * np.sqrt(TRADING_DAYS_YEAR)
        sharpe = (ann_ret - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else 0

        # Max drawdown
        cum = (1 + daily).cumprod()
        max_dd = ((cum - cum.cummax()) / cum.cummax()).min()

        # Beta vs SPY
        beta = 0
        if bench_returns is not None:
            aligned = pd.DataFrame({"stock": daily, "bench": bench_returns}).dropna()
            if len(aligned) > 10:
                cov = aligned["stock"].cov(aligned["bench"])
                var_b = aligned["bench"].var()
                beta = cov / var_b if var_b > 0 else 0

        rows.append({
            "ticker": ticker,
            "ann_return": ann_ret,
            "ann_volatility": ann_vol,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "beta": beta,
        })

    return pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()


def compute_correlation_matrix(historical: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute pairwise correlation matrix of daily returns."""
    daily_returns = compute_daily_returns(historical)
    if daily_returns.empty:
        return pd.DataFrame()
    return daily_returns.corr()
