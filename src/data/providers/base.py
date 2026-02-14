"""
Provider contracts for market and fundamental data sources.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional

import pandas as pd


class MarketDataProvider(ABC):
    """Interface for OHLCV market data providers."""

    @abstractmethod
    def get_daily_ohlcv(self, symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        """
        Return OHLCV DataFrame with columns:
        Date, Open, High, Low, Close, Adj Close, Volume
        """
        raise NotImplementedError

    @abstractmethod
    def get_index_ohlcv(self, index_symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        """Return index OHLCV with same schema as get_daily_ohlcv."""
        raise NotImplementedError


class FundamentalDataProvider(ABC):
    """Interface for fundamentals and estimates providers."""

    @abstractmethod
    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        """Return normalized profile/fundamental snapshot."""
        raise NotImplementedError

    @abstractmethod
    def get_quarterly_fundamentals(self, symbol: str, limit: int = 20) -> Optional[pd.DataFrame]:
        """
        Return DataFrame with canonical quarterly schema:
        ticker, report_date, disclosure_date, eps, revenue, roe, gross_margin
        """
        raise NotImplementedError

    @abstractmethod
    def get_analyst_estimates(self, symbol: str, limit: int = 8) -> Optional[pd.DataFrame]:
        """
        Return estimates DataFrame with columns:
        ticker, report_date, estimated_eps, estimated_revenue, source
        """
        raise NotImplementedError

    @abstractmethod
    def get_earnings_calendar(self, symbol: str, limit: int = 12) -> Optional[pd.DataFrame]:
        """
        Return earnings calendar DataFrame with columns:
        ticker, report_date, disclosure_date
        """
        raise NotImplementedError
