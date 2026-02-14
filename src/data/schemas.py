"""
Canonical parquet schema validators for picker-first pipeline.
"""

from __future__ import annotations

from typing import List

import pandas as pd


DAILY_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]
FUNDAMENTAL_COLUMNS = [
    "ticker",
    "report_date",
    "disclosure_date",
    "eps",
    "revenue",
    "roe",
    "gross_margin",
]
FACTOR_COLUMNS = [
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


def _ensure_columns(df: pd.DataFrame, required: List[str], dataset_name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{dataset_name} missing columns: {missing}")


def validate_daily_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # Support API-style naming.
    out = out.rename(
        columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Adj Close": "adj_close",
            "Volume": "volume",
        }
    )
    if "adj_close" not in out.columns and "close" in out.columns:
        out["adj_close"] = out["close"]
    _ensure_columns(out, DAILY_COLUMNS, "daily_ohlcv")
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out = out[DAILY_COLUMNS].dropna(subset=["date", "open", "high", "low", "close"])
    return out.sort_values("date")


def validate_quarterly_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    _ensure_columns(out, FUNDAMENTAL_COLUMNS, "quarterly_fundamentals")
    out["report_date"] = pd.to_datetime(out["report_date"]).dt.date
    out["disclosure_date"] = pd.to_datetime(out["disclosure_date"]).dt.date
    out = out.dropna(subset=["ticker", "report_date", "disclosure_date"]).sort_values("report_date")
    return out[FUNDAMENTAL_COLUMNS]


def validate_factor_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    _ensure_columns(out, FACTOR_COLUMNS, "factor_table")
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["above_200ma"] = out["above_200ma"].astype(bool)
    out["near_52w"] = out["near_52w"].astype(bool)
    out = out.dropna(subset=["date", "ticker", "adj_close"]).sort_values(["date", "ticker"])
    return out[FACTOR_COLUMNS]


def data_quality_report(df: pd.DataFrame, key_columns: List[str]) -> dict:
    """
    Basic DQ report used by pipeline quality checks.
    """
    if df is None or df.empty:
        return {"rows": 0, "null_ratio": 1.0, "duplicates": 0}

    keys = [c for c in key_columns if c in df.columns]
    null_ratio = float(df[keys].isnull().mean().mean()) if keys else 0.0
    duplicates = int(df.duplicated(subset=keys).sum()) if keys else int(df.duplicated().sum())
    return {"rows": int(len(df)), "null_ratio": null_ratio, "duplicates": duplicates}
