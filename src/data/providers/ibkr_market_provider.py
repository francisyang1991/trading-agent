"""
Market data providers backed by IBKR-connected services.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from .base import MarketDataProvider
from .resilient import fetch_ibkr_gcloud_daily_ohlcv, fetch_ibkr_local_daily_ohlcv


class IBKRGcloudMarketProvider(MarketDataProvider):
    """Daily OHLCV provider backed by the GCP IBKR HTTP API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.base_url = (
            base_url
            or os.environ.get("IBKR_GCLOUD_URL")
            or os.environ.get("TRADE_API_URL")
            or ""
        ).strip().rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("IBKR_GCLOUD_API_KEY")
            or os.environ.get("TRADE_API_KEY")
            or ""
        ).strip()

    def _enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def get_daily_ohlcv(self, symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        if not self._enabled():
            return None
        return fetch_ibkr_gcloud_daily_ohlcv(symbol, period, self.base_url, self.api_key)

    def get_index_ohlcv(self, index_symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        return self.get_daily_ohlcv(index_symbol, period=period)


class IBKRLocalMarketProvider(MarketDataProvider):
    """Daily OHLCV provider backed by a locally reachable IBKR gateway."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
    ):
        self.host = (host or os.environ.get("IB_HOST") or "127.0.0.1").strip()
        self.port = int(port or os.environ.get("IB_PORT") or 4002)
        self.client_id = int(client_id or os.environ.get("IB_CLIENT_ID") or 99)

    def _enabled(self) -> bool:
        return bool(self.host and self.port)

    def get_daily_ohlcv(self, symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        if not self._enabled():
            return None
        return fetch_ibkr_local_daily_ohlcv(
            symbol,
            period,
            host=self.host,
            port=self.port,
            client_id=self.client_id,
        )

    def get_index_ohlcv(self, index_symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        return self.get_daily_ohlcv(index_symbol, period=period)
