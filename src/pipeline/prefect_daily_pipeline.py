"""
Prefect daily pipeline for picker-first data preparation.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd
from prefect import flow, get_run_logger, task

from src.data.factor_alignment import align_fundamentals_to_daily, compute_rs_rank
from src.data.schemas import (
    DAILY_COLUMNS,
    FACTOR_COLUMNS,
    FUNDAMENTAL_COLUMNS,
    data_quality_report,
    validate_factor_table,
)
from src.data_manager import DataManager
from src.pipeline.daily_alerts import build_daily_alert_payload


@task(retries=3, retry_delay_seconds=5)
def fetch_price_data(symbols: List[str], period: str = "2y") -> Dict[str, pd.DataFrame]:
    dm = DataManager()
    out: Dict[str, pd.DataFrame] = {}
    for s in symbols:
        df = dm.get_daily_data(s, period=period, force_refresh=False)
        if df is None or df.empty:
            continue
        local = (
            df.reset_index()
            .rename(
                columns={
                    "Date": "date",
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )
            .assign(adj_close=lambda x: x["close"])
        )
        out[s.upper()] = local[DAILY_COLUMNS].copy()
    return out


@task(retries=3, retry_delay_seconds=10)
def fetch_fundamental_data(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    dm = DataManager()
    out: Dict[str, pd.DataFrame] = {}
    for s in symbols:
        df = dm.get_quarterly_fundamentals(s, force_refresh=False)
        if df is None or df.empty:
            continue
        out[s.upper()] = df[FUNDAMENTAL_COLUMNS].copy()
    return out


@task(retries=2, retry_delay_seconds=10)
def fetch_estimates_and_surprise(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    dm = DataManager()
    out: Dict[str, pd.DataFrame] = {}
    for s in symbols:
        df = dm.get_analyst_estimates(s)
        if df is None:
            continue
        out[s.upper()] = df
    return out


@task(retries=2, retry_delay_seconds=5)
def align_calendars_and_effective_dates(
    prices: Dict[str, pd.DataFrame],
    fundamentals: Dict[str, pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for ticker, pdf in prices.items():
        fdf = fundamentals.get(ticker, pd.DataFrame(columns=FUNDAMENTAL_COLUMNS))
        out[ticker] = align_fundamentals_to_daily(pdf, fdf, ticker=ticker)
    return out


@task
def compute_factors(
    aligned: Dict[str, pd.DataFrame],
    benchmark_close: pd.Series,
) -> pd.DataFrame:
    rows = []
    for ticker, df in aligned.items():
        if df is None or df.empty:
            continue
        local = df.copy().sort_values("date")
        local["adj_close"] = local["adj_close"].astype(float)
        local["ma200"] = local["adj_close"].rolling(200).mean()
        local["above_200ma"] = local["adj_close"] > local["ma200"]
        local["high_52w"] = local["adj_close"].rolling(252).max()
        local["near_52w"] = local["adj_close"] >= (local["high_52w"] * 0.85)
        local["revenue_growth"] = local["revenue"].pct_change()
        local["gm_rank"] = local["gross_margin"].rank(pct=True) * 100
        local["rs_rank"] = compute_rs_rank(local[["adj_close"]], benchmark_close)
        rows.append(
            local[
                [
                    "date",
                    "ticker",
                    "adj_close",
                    "rs_rank",
                    "above_200ma",
                    "near_52w",
                    "eps",
                    "revenue_growth",
                    "roe",
                    "gm_rank",
                ]
            ]
        )

    if not rows:
        return pd.DataFrame(columns=FACTOR_COLUMNS)
    return pd.concat(rows, ignore_index=True)


@task
def materialize_processed_dataset(factor_table: pd.DataFrame) -> str:
    dm = DataManager()
    validated = validate_factor_table(factor_table)
    path = dm.parquet_store.save_factor_table(validated)
    return str(path)


@task
def quality_checks_and_alert(
    prices: Dict[str, pd.DataFrame],
    fundamentals: Dict[str, pd.DataFrame],
    factor_table: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
) -> Dict:
    """
    Basic DQ checks + alert payload.
    """
    logger = get_run_logger()
    alert = {"status": "ok", "issues": []}

    # Missing trading days check (per symbol gap > 7 days in date index)
    for ticker, df in prices.items():
        d = pd.to_datetime(df["date"]).sort_values()
        max_gap = d.diff().dt.days.dropna().max() if len(d) > 1 else 0
        if max_gap and max_gap > 7:
            alert["issues"].append(f"{ticker}: max trading-day gap {int(max_gap)} days")

    # Null ratio + duplicates
    ft_dq = data_quality_report(factor_table, ["date", "ticker", "adj_close", "rs_rank"])
    if ft_dq["null_ratio"] > 0.1:
        alert["issues"].append(f"factor_table high null ratio: {ft_dq['null_ratio']:.2%}")
    if ft_dq["duplicates"] > 0:
        alert["issues"].append(f"factor_table duplicate rows: {ft_dq['duplicates']}")

    # Benchmark alignment
    if benchmark_prices is None or benchmark_prices.empty:
        alert["issues"].append("missing benchmark data for RS computation")

    # Fundamentals presence
    coverage = sum(1 for s in prices if s in fundamentals and not fundamentals[s].empty)
    if prices and (coverage / len(prices)) < 0.6:
        alert["issues"].append(
            f"fundamentals coverage too low: {coverage}/{len(prices)} symbols"
        )

    if alert["issues"]:
        alert["status"] = "warn"
        for issue in alert["issues"]:
            logger.warning(issue)
    else:
        logger.info("DQ checks passed.")
    return alert


@task
def persist_run_metadata(
    run_id: str,
    symbols: List[str],
    period: str,
    output_path: str,
    dq_alert: Dict,
) -> str:
    metadata_dir = Path("data/processed/runs")
    metadata_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id,
        "created_at": datetime.utcnow().isoformat(),
        "symbols_count": len(symbols),
        "period": period,
        "output_path": output_path,
        "dq_alert": dq_alert,
        "params": {"symbols": symbols},
    }
    path = metadata_dir / f"{run_id}.json"
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return str(path)


@flow(name="picker-first-daily-pipeline")
def run_daily_pipeline(
    symbols: List[str],
    period: str = "2y",
    benchmark_symbol: str = "^GSPC",
    current_positions: List[Dict] | None = None,
) -> Dict:
    dm = DataManager()
    run_id = f"daily_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    logger = get_run_logger()

    prices = fetch_price_data(symbols, period=period)
    fundamentals = fetch_fundamental_data(symbols)
    _ = fetch_estimates_and_surprise(symbols)

    benchmark = dm.market_provider.get_index_ohlcv(benchmark_symbol, period=period)
    benchmark_close = pd.Series(dtype=float)
    if benchmark is not None and not benchmark.empty:
        benchmark["Date"] = pd.to_datetime(benchmark["Date"])
        benchmark_close = benchmark.set_index("Date")["Adj Close"]

    aligned = align_calendars_and_effective_dates(prices, fundamentals)
    factor_table = compute_factors(aligned, benchmark_close=benchmark_close)
    output_path = materialize_processed_dataset(factor_table)
    dq_alert = quality_checks_and_alert(prices, fundamentals, factor_table, benchmark)
    latest_picks = (
        factor_table.sort_values("date").groupby("ticker").tail(1).sort_values("rs_rank", ascending=False).head(20)
    )
    new_picks = latest_picks[
        ["date", "ticker", "adj_close", "rs_rank", "above_200ma", "near_52w", "eps", "roe"]
    ].to_dict(orient="records")
    daily_alert_payload = build_daily_alert_payload(
        new_picks=new_picks,
        current_positions=current_positions or [],
        benchmark_df=benchmark if benchmark is not None else pd.DataFrame(),
    )
    metadata_path = persist_run_metadata(run_id, symbols, period, output_path, dq_alert)

    logger.info("Pipeline completed.")
    return {
        "run_id": run_id,
        "factor_table_path": output_path,
        "metadata_path": metadata_path,
        "dq_alert": dq_alert,
        "daily_alert_payload": daily_alert_payload,
    }


if __name__ == "__main__":
    # Small default smoke run.
    print(
        run_daily_pipeline(
            symbols=["AAPL", "MSFT", "NVDA", "AMZN", "META"],
            period="2y",
            benchmark_symbol="^GSPC",
        )
    )
