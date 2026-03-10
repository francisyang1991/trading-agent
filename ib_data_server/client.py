#!/usr/bin/env python3
"""
MacBook client for the IB Data Server API.

Drop this file anywhere on your MacBook. It queries the Mac mini's
API over local network or Tailscale to get IBKR historical data
without needing a direct IB Gateway connection.

Usage:
    from ib_data_server.client import IBDataClient

    client = IBDataClient("http://mac-mini.local:8100")  # or Tailscale IP
    df = client.get_bars("AAPL", bar_size="1 day", start="2025-01-01")
    print(df.tail())

    # Convenience: get pandas DataFrame directly
    df = client.daily("NVDA", days=90)
"""

from datetime import datetime, timedelta
from typing import Optional

import requests

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


class IBDataClient:
    """Thin HTTP client for the IB Data Server running on Mac mini."""

    def __init__(self, base_url: str = "http://127.0.0.1:8100", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict:
        return self._get("/health")

    def get_bars_raw(
        self,
        symbol: str,
        bar_size: str = "1 day",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 5000,
    ) -> dict:
        params = {"bar_size": bar_size, "limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._get(f"/bars/{symbol}", params=params)

    def get_bars(
        self,
        symbol: str,
        bar_size: str = "1 day",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 5000,
    ):
        """Get bars as a pandas DataFrame (if pandas available, else list of dicts)."""
        data = self.get_bars_raw(symbol, bar_size, start, end, limit)
        bars = data.get("bars", [])
        if HAS_PANDAS and bars:
            df = pd.DataFrame(bars)
            if "ts" in df.columns:
                df["ts"] = pd.to_datetime(df["ts"])
                df = df.set_index("ts")
            return df
        return bars

    def daily(self, symbol: str, days: int = 365, start: Optional[str] = None):
        """Convenience: fetch daily bars for the last N days."""
        if not start:
            start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        return self.get_bars(symbol, "1 day", start=start)

    def intraday(self, symbol: str, bar_size: str = "5 mins", days: int = 5):
        """Convenience: fetch intraday bars."""
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        return self.get_bars(symbol, bar_size, start=start)

    def symbols(self) -> list[dict]:
        return self._get("/symbols").get("symbols", [])

    def stats(self) -> dict:
        return self._get("/stats")

    def add_symbol(self, symbol: str, sec_type: str = "STK", exchange: str = "SMART", currency: str = "USD") -> dict:
        return self._post("/symbols", params={"symbol": symbol, "sec_type": sec_type, "exchange": exchange, "currency": currency})

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, params: Optional[dict] = None, json: Optional[dict] = None) -> dict:
        resp = requests.post(f"{self.base_url}{path}", params=params, json=json, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8100"
    client = IBDataClient(url)
    print(f"Health: {client.health()}")
    print(f"Stats:  {client.stats()}")
    print(f"Symbols: {[s['symbol'] for s in client.symbols()]}")
