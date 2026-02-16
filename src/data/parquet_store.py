"""
Parquet storage contracts for daily/fundamental/processed datasets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .schemas import (
    validate_daily_ohlcv,
    validate_factor_table,
    validate_quarterly_fundamentals,
)


class ParquetDataStore:
    """Canonical parquet writer/reader with schema validation."""

    def __init__(self, root_dir: str = "data"):
        self.root = Path(root_dir)
        self.daily_dir = self.root / "daily"
        self.fund_dir = self.root / "fundamental"
        self.proc_dir = self.root / "processed"
        for d in (self.daily_dir, self.fund_dir, self.proc_dir):
            d.mkdir(parents=True, exist_ok=True)

    def daily_path(self, ticker: str) -> Path:
        return self.daily_dir / f"{ticker.upper()}.parquet"

    def fundamental_path(self, ticker: str) -> Path:
        return self.fund_dir / f"{ticker.upper()}.parquet"

    def factor_path(self) -> Path:
        return self.proc_dir / "factor_table.parquet"

    def _csv_fallback_path(self, parquet_path: Path) -> Path:
        return parquet_path.with_suffix(".csv")

    def save_daily(self, ticker: str, df: pd.DataFrame) -> Path:
        out = validate_daily_ohlcv(df)
        path = self.daily_path(ticker)
        try:
            out.to_parquet(path, index=False)
            return path
        except Exception:
            csv_path = self._csv_fallback_path(path)
            out.to_csv(csv_path, index=False)
            return csv_path

    def load_daily(self, ticker: str) -> Optional[pd.DataFrame]:
        path = self.daily_path(ticker)
        if path.exists():
            try:
                return pd.read_parquet(path)
            except Exception:
                pass
        csv_path = self._csv_fallback_path(path)
        if csv_path.exists():
            return pd.read_csv(csv_path)
        return None

    def save_quarterly_fundamentals(self, ticker: str, df: pd.DataFrame) -> Path:
        out = validate_quarterly_fundamentals(df)
        path = self.fundamental_path(ticker)
        try:
            out.to_parquet(path, index=False)
            return path
        except Exception:
            csv_path = self._csv_fallback_path(path)
            out.to_csv(csv_path, index=False)
            return csv_path

    def load_quarterly_fundamentals(self, ticker: str) -> Optional[pd.DataFrame]:
        path = self.fundamental_path(ticker)
        if path.exists():
            try:
                return pd.read_parquet(path)
            except Exception:
                pass
        csv_path = self._csv_fallback_path(path)
        if csv_path.exists():
            return pd.read_csv(csv_path)
        return None

    def save_factor_table(self, df: pd.DataFrame) -> Path:
        out = validate_factor_table(df)
        path = self.factor_path()
        try:
            out.to_parquet(path, index=False)
            return path
        except Exception:
            csv_path = self._csv_fallback_path(path)
            out.to_csv(csv_path, index=False)
            return csv_path

    def load_factor_table(self) -> Optional[pd.DataFrame]:
        path = self.factor_path()
        if path.exists():
            try:
                return pd.read_parquet(path)
            except Exception:
                pass
        csv_path = self._csv_fallback_path(path)
        if csv_path.exists():
            return pd.read_csv(csv_path)
        return None
