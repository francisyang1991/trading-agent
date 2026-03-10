"""
Historical lookback: run the picker at past dates using only data available then.

Usage:
    from src.picker.lookback import run_picker_at_date, compare_across_dates

    picks_6m_ago = run_picker_at_date(prices, spy, fund_service, cfg, months_ago=6)
    comparison = compare_across_dates([picks_now, picks_3m, picks_6m, picks_9m])
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from src.picker.fundamentals_service import FundamentalSnapshotService
from src.universe.filters import (
    build_technical_base_from_prices,
    moat_quality_filter,
    technical_filter,
)


def run_picker_at_date(
    prices: Dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    fundamentals_service: FundamentalSnapshotService,
    cfg: Dict[str, Any],
    as_of_date: date,
    progress_every: int = 50,
    refresh_historical_cache: bool = False,
) -> pd.DataFrame:
    """
    Run the full three-layer picker using only data available at as_of_date.
    No lookahead: prices are truncated and fundamentals are reconstructed from
    quarterly data disclosed on/before as_of_date.

    Args:
        prices: full price dict (will be truncated internally).
        spy_df: SPY DataFrame with Close column.
        fundamentals_service: for enriching survivors.
        cfg: dict with keys matching picker_config.yaml structure.
        as_of_date: pretend we are running on this date.
        progress_every: log every N tickers in fundamentals.

    Returns:
        DataFrame of picks with composite score, or empty DataFrame.
    """
    tech_cfg = cfg.get("technical", {})
    fund_cfg = cfg.get("fundamental", {})
    qual_cfg = cfg.get("quality", {})
    score_cfg = cfg.get("scoring", {})
    lb_cfg = cfg.get("lookback", {})
    skip_missing_yoy = bool(lb_cfg.get("skip_missing_yoy_checks", False))

    # 1. Compute SPY benchmark return at as_of_date
    spy_trunc = _truncate(spy_df, as_of_date)
    if spy_trunc is None or len(spy_trunc) < 252:
        print(f"  [lookback {as_of_date}] SPY has insufficient history, skipping")
        return pd.DataFrame()

    spy_close = spy_trunc["Close"]
    spy_ret_252 = float(spy_close.iloc[-1] / spy_close.iloc[-252] - 1)

    # 2. Build technical base at as_of_date
    base = build_technical_base_from_prices(
        prices,
        benchmark_return_252=spy_ret_252,
        near_52w_ratio=tech_cfg.get("near_52w_ratio", 0.85),
        min_bars=tech_cfg.get("min_bars", 260),
        as_of_date=as_of_date,
    )
    if base.empty:
        return pd.DataFrame()

    tech = technical_filter(base, rs_threshold=tech_cfg.get("rs_threshold", 80.0))
    if tech.empty:
        return pd.DataFrame()

    print(f"  [lookback {as_of_date}] base={len(base)} tech={len(tech)}")

    # 3. Fundamental enrichment using point-in-time quarterly snapshots.
    fund_df = fundamentals_service.load_for_tickers(
        tech["ticker"].tolist(),
        existing_snapshot_path=None,  # no cache for lookback runs
        progress_every=progress_every,
        as_of_date=as_of_date,
        use_persistent_cache=True,
        refresh_persistent_cache=refresh_historical_cache,
    )

    merged = tech.merge(fund_df, on="ticker", how="left")
    merged["gm_rank"] = (
        pd.to_numeric(merged["gross_margin"], errors="coerce").rank(pct=True) * 100
    ).round(2)

    # 4. Fundamental + quality filters
    fund = _filter_lookback_fundamentals(
        merged,
        fund_cfg=fund_cfg,
        skip_missing_yoy_checks=skip_missing_yoy,
    )
    quality = moat_quality_filter(
        fund,
        min_roe=qual_cfg.get("min_roe", 0.10),
        min_gm_rank=qual_cfg.get("min_gm_rank", 60.0),
        max_debt_to_equity=qual_cfg.get("max_debt_to_equity", 1.2),
    )

    # 5. Score
    if not quality.empty:
        quality = quality.copy()
        eps_for_score = pd.to_numeric(quality["eps_yoy"], errors="coerce").fillna(0.0)
        rev_for_score = pd.to_numeric(quality["revenue_growth"], errors="coerce").fillna(0.0)
        quality["composite"] = (
            quality["rs_rank"] * score_cfg.get("rs_rank_weight", 0.35)
            + (eps_for_score * 100).clip(-100, 200) * score_cfg.get("eps_yoy_weight", 0.20)
            + (rev_for_score * 100).clip(-100, 200) * score_cfg.get("revenue_growth_weight", 0.20)
            + quality["gm_rank"] * score_cfg.get("gm_rank_weight", 0.15)
            + (quality["roe"] * 100).clip(-100, 100) * score_cfg.get("roe_weight", 0.10)
        )
        quality = quality.sort_values("composite", ascending=False)
        quality["as_of_date"] = str(as_of_date)

    print(f"  [lookback {as_of_date}] fundamental={len(fund)} quality={len(quality)}")
    return quality


def _filter_lookback_fundamentals(
    merged: pd.DataFrame,
    fund_cfg: Dict[str, Any],
    skip_missing_yoy_checks: bool = False,
) -> pd.DataFrame:
    """
    Apply lookback fundamental filtering with optional missing-YoY bypass.

    When skip_missing_yoy_checks is True, rows with missing EPS/Revenue growth
    are not rejected solely for missing values; threshold checks apply only when
    values are present.
    """
    if merged is None or merged.empty:
        return pd.DataFrame()

    df = merged.copy()
    # Quality stage requires ROE + GM rank regardless of YoY policy.
    df = df.dropna(subset=["roe", "gm_rank"])
    if df.empty:
        return df

    min_eps = fund_cfg.get("min_eps_yoy", 0.25)
    min_rev = fund_cfg.get("min_revenue_growth", 0.10)
    min_acc = fund_cfg.get("min_revenue_acceleration")
    require_surprise_non_negative = fund_cfg.get("require_surprise_non_negative", False)

    eps = pd.to_numeric(df.get("eps_yoy"), errors="coerce")
    rev = pd.to_numeric(df.get("revenue_growth"), errors="coerce")
    mask = pd.Series(True, index=df.index)

    if skip_missing_yoy_checks:
        mask &= eps.isna() | (eps >= min_eps)
        mask &= rev.isna() | (rev >= min_rev)
    else:
        mask &= eps.notna() & (eps >= min_eps)
        mask &= rev.notna() & (rev >= min_rev)

    if min_acc is not None:
        acc = pd.to_numeric(df.get("revenue_acceleration"), errors="coerce")
        if skip_missing_yoy_checks:
            mask &= acc.isna() | (acc >= float(min_acc))
        else:
            mask &= acc.notna() & (acc >= float(min_acc))

    if require_surprise_non_negative and "earnings_surprise" in df.columns:
        surprise = pd.to_numeric(df["earnings_surprise"], errors="coerce")
        mask &= surprise >= 0

    return df[mask].copy()


def compare_across_dates(
    results: Dict[str, pd.DataFrame],
    current_prices: Optional[Dict[str, pd.DataFrame]] = None,
) -> pd.DataFrame:
    """
    Compare picks across multiple lookback dates.
    Finds stocks that appear in multiple time windows (persistent strength).

    Args:
        results: {"now": df, "3m_ago": df, "6m_ago": df, "9m_ago": df}
        current_prices: if provided, compute gain since each lookback date.

    Returns:
        DataFrame with ticker, appearance count, which dates, and gain.
    """
    all_tickers: Dict[str, Dict] = {}

    for label, df in results.items():
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            ticker = row["ticker"]
            if ticker not in all_tickers:
                all_tickers[ticker] = {
                    "ticker": ticker,
                    "appearances": 0,
                    "dates": [],
                    "best_score": 0,
                    "price_at_earliest": None,
                    "price_now": None,
                }
            all_tickers[ticker]["appearances"] += 1
            all_tickers[ticker]["dates"].append(label)
            score = row.get("composite", 0)
            if score > all_tickers[ticker]["best_score"]:
                all_tickers[ticker]["best_score"] = score

            # Track price at each lookback date
            price = row.get("adj_close")
            existing = all_tickers[ticker].get("price_at_earliest")
            if price and (existing is None or label != "now"):
                all_tickers[ticker]["price_at_earliest"] = price

    # Add current price for gain calculation
    if current_prices:
        for ticker, info in all_tickers.items():
            df = current_prices.get(ticker)
            if df is not None and not df.empty:
                info["price_now"] = float(df["Close"].iloc[-1])

    rows = list(all_tickers.values())
    for r in rows:
        r["dates"] = ", ".join(r["dates"])
        if r["price_at_earliest"] and r["price_now"]:
            r["gain_pct"] = round((r["price_now"] / r["price_at_earliest"] - 1) * 100, 1)
        else:
            r["gain_pct"] = None

    result = pd.DataFrame(rows).sort_values(
        ["appearances", "best_score"], ascending=[False, False]
    )
    return result


def _truncate(df: pd.DataFrame, as_of: date) -> Optional[pd.DataFrame]:
    """Truncate a price DataFrame to only include data <= as_of."""
    d = df.copy()
    if "Date" in d.columns:
        d = d[pd.to_datetime(d["Date"]).dt.date <= as_of]
    elif isinstance(d.index, pd.DatetimeIndex):
        d = d[d.index.date <= as_of]
    if d.empty:
        return None
    return d
