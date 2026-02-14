"""
No-leakage alignment helpers for fundamentals and factor generation.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd


def align_fundamentals_to_daily(
    daily_df: pd.DataFrame,
    fundamental_df: pd.DataFrame,
    ticker: str,
) -> pd.DataFrame:
    """
    Forward-fill quarterly fundamentals only after disclosure_date.
    """
    if daily_df is None or daily_df.empty:
        return pd.DataFrame()
    if fundamental_df is None or fundamental_df.empty:
        out = daily_df.copy()
        out["ticker"] = ticker
        return out

    price = daily_df.copy()
    if "date" not in price.columns:
        if isinstance(price.index, pd.DatetimeIndex):
            price = price.reset_index().rename(columns={price.index.name or "index": "date"})
        elif "Date" in price.columns:
            price = price.rename(columns={"Date": "date"})
    price["date"] = pd.to_datetime(price["date"])
    price = price.sort_values("date")

    f = fundamental_df.copy()
    f["disclosure_date"] = pd.to_datetime(f["disclosure_date"])
    f = f.sort_values("disclosure_date")

    keep_cols = [
        c for c in ["disclosure_date", "eps", "revenue", "roe", "gross_margin"] if c in f.columns
    ]
    f = f[keep_cols]

    aligned = pd.merge_asof(
        price,
        f,
        left_on="date",
        right_on="disclosure_date",
        direction="backward",
    )
    aligned["ticker"] = ticker
    return aligned


def compute_rs_rank(
    prices: pd.DataFrame,
    benchmark: pd.Series,
    lookback_days: Iterable[int] = (63, 126, 252),
) -> pd.Series:
    """
    Compute RS rank 1-99 from average excess return vs benchmark.
    """
    if prices.empty:
        return pd.Series(dtype=float)

    score = pd.Series(0.0, index=prices.index)
    close = prices["adj_close"] if "adj_close" in prices.columns else prices["close"]
    bench = benchmark.reindex(prices.index).ffill()

    valid_windows = 0
    for n in lookback_days:
        stock_ret = close / close.shift(n) - 1.0
        bench_ret = bench / bench.shift(n) - 1.0
        excess = (stock_ret - bench_ret).fillna(0.0)
        score = score + excess
        valid_windows += 1

    if valid_windows == 0:
        return pd.Series(50.0, index=prices.index)

    rs = score / float(valid_windows)
    pct_rank = rs.rank(pct=True).clip(0.0, 1.0)
    return (pct_rank * 98 + 1).round(2)
