"""
Resilient provider wrappers with retry/backoff and fallback.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional, Tuple
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
try:
    from tenacity import retry, stop_after_attempt, wait_exponential
except Exception:  # pragma: no cover - fallback when tenacity missing
    def retry(*args, **kwargs):  # type: ignore
        def _wrap(fn):
            return fn
        return _wrap

    def stop_after_attempt(*args, **kwargs):  # type: ignore
        return None

    def wait_exponential(*args, **kwargs):  # type: ignore
        return None

from .base import FundamentalDataProvider, MarketDataProvider
from ..ibkr_client import IBKRClient, IB_ASYNC_AVAILABLE


class ResilientMarketDataProvider(MarketDataProvider):
    """Try providers in order with retry/backoff."""

    def __init__(self, providers: Iterable[MarketDataProvider]):
        self.providers = list(providers)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=1, max=8))
    def _safe_daily(self, provider: MarketDataProvider, symbol: str, period: str) -> Optional[pd.DataFrame]:
        return provider.get_daily_ohlcv(symbol, period)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=1, max=8))
    def _safe_index(self, provider: MarketDataProvider, index_symbol: str, period: str) -> Optional[pd.DataFrame]:
        return provider.get_index_ohlcv(index_symbol, period)

    def get_daily_ohlcv(self, symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        for p in self.providers:
            try:
                out = self._safe_daily(p, symbol, period)
            except Exception:
                out = None
            if out is not None and not out.empty:
                return out
        return None

    def get_index_ohlcv(self, index_symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        for p in self.providers:
            try:
                out = self._safe_index(p, index_symbol, period)
            except Exception:
                out = None
            if out is not None and not out.empty:
                return out
        return None


class ResilientFundamentalProvider(FundamentalDataProvider):
    """Try fundamental providers in order with retry/backoff."""

    def __init__(self, providers: Iterable[FundamentalDataProvider]):
        self.providers = list(providers)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=1, max=8))
    def _safe_profile(self, provider: FundamentalDataProvider, symbol: str):
        return provider.get_company_profile(symbol)

    def get_company_profile(self, symbol: str):
        for p in self.providers:
            try:
                out = self._safe_profile(p, symbol)
            except Exception:
                out = None
            if out:
                return out
        return None

    def get_quarterly_fundamentals(self, symbol: str, limit: int = 20):
        for p in self.providers:
            try:
                out = p.get_quarterly_fundamentals(symbol, limit=limit)
            except Exception:
                out = None
            if out is not None and not out.empty:
                return out
        return None

    def get_analyst_estimates(self, symbol: str, limit: int = 8):
        for p in self.providers:
            try:
                out = p.get_analyst_estimates(symbol, limit=limit)
            except Exception:
                out = None
            if out is not None and not out.empty:
                return out
        return None

    def get_earnings_calendar(self, symbol: str, limit: int = 12):
        for p in self.providers:
            try:
                out = p.get_earnings_calendar(symbol, limit=limit)
            except Exception:
                out = None
            if out is not None and not out.empty:
                return out
        return None


def fetch_stooq_daily_ohlcv(symbol: str) -> Optional[pd.DataFrame]:
    url = "https://stooq.com/q/d/l/?" + urlencode({"s": f"{symbol.lower()}.us", "i": "d"})
    try:
        with urlopen(url, timeout=15) as resp:
            payload = resp.read().decode("utf-8", errors="ignore")
    except URLError:
        return None
    if not payload.strip() or payload.startswith("No data"):
        return None
    df = pd.read_csv(pd.io.common.StringIO(payload))
    if df.empty:
        return None
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
    return df


def fetch_ibkr_gcloud_daily_ohlcv(
    symbol: str,
    period: str,
    base_url: str,
    api_key: str,
) -> Optional[pd.DataFrame]:
    return fetch_ibkr_gcloud_ohlcv(symbol, period, "1d", base_url, api_key)


def fetch_ibkr_gcloud_ohlcv(
    symbol: str,
    period: str,
    interval: str,
    base_url: str,
    api_key: str,
) -> Optional[pd.DataFrame]:
    endpoints = [
        f"{base_url}/api/history/{symbol}",
        f"{base_url}/api/ohlcv/{symbol}",
        f"{base_url}/api/bars/{symbol}",
    ]
    for url in endpoints:
        try:
            req = Request(
                url + "?" + urlencode({"period": period, "interval": interval}),
                headers={"X-API-Key": api_key},
            )
            with urlopen(req, timeout=20) as resp:
                payload = resp.read().decode("utf-8", errors="ignore")
            data = pd.read_json(pd.io.common.StringIO(payload))
            if data.empty:
                continue
            cols = {c.lower(): c for c in data.columns}
            ren = {}
            if "open" in cols:
                ren[cols["open"]] = "Open"
            if "high" in cols:
                ren[cols["high"]] = "High"
            if "low" in cols:
                ren[cols["low"]] = "Low"
            if "close" in cols:
                ren[cols["close"]] = "Close"
            if "volume" in cols:
                ren[cols["volume"]] = "Volume"
            if "date" in cols:
                ren[cols["date"]] = "Date"
            data = data.rename(columns=ren)
            if "Date" in data.columns and "Close" in data.columns:
                if str(interval).lower() in {"1d", "1day", "1 day"}:
                    data["Date"] = pd.to_datetime(data["Date"], errors="coerce").dt.date
                else:
                    data["Date"] = pd.to_datetime(data["Date"], errors="coerce", utc=True)
                return data
        except Exception:
            continue
    return None


def _period_to_ib_duration(period: str) -> str:
    p = period.lower()
    if p.endswith("y"):
        return f"{int(p[:-1])} Y"
    if p.endswith("mo"):
        months = int(p[:-2])
        return f"{months * 30} D"
    if p.endswith("d"):
        return f"{int(p[:-1])} D"
    return "365 D"


_IB_CLIENT: Optional[IBKRClient] = None
_IB_CLIENT_AVAILABLE: Optional[bool] = None


def fetch_ibkr_local_daily_ohlcv(
    symbol: str,
    period: str,
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 99,
) -> Optional[pd.DataFrame]:
    global _IB_CLIENT, _IB_CLIENT_AVAILABLE
    if not IB_ASYNC_AVAILABLE:
        _IB_CLIENT_AVAILABLE = False
        return None
    if _IB_CLIENT_AVAILABLE is False:
        return None

    try:
        if _IB_CLIENT is None:
            _IB_CLIENT = IBKRClient(host=host, port=port, client_id=client_id, readonly=True, timeout=10)
            if not _IB_CLIENT.connect():
                _IB_CLIENT_AVAILABLE = False
                _IB_CLIENT = None
                return None
            _IB_CLIENT_AVAILABLE = True

        duration = _period_to_ib_duration(period)
        h = _IB_CLIENT.get_historical_data(symbol=symbol, bar_size="1 day", duration=duration, use_rth=True)
        if h is None or h.empty:
            return None
        df = h.copy().reset_index()
        ren = {}
        if "datetime" in df.columns:
            ren["datetime"] = "Date"
        if "open" in df.columns:
            ren["open"] = "Open"
        if "high" in df.columns:
            ren["high"] = "High"
        if "low" in df.columns:
            ren["low"] = "Low"
        if "close" in df.columns:
            ren["close"] = "Close"
        if "volume" in df.columns:
            ren["volume"] = "Volume"
        df = df.rename(columns=ren)
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"]).dt.date
        return df
    except Exception:
        return None


def fallback_price_fetch(
    symbol: str,
    period: str,
    gcloud_base_url: str,
    gcloud_api_key: str,
    local_ibkr_host: str,
    local_ibkr_port: int,
    local_ibkr_client_id: int,
    progress_hook: Optional[Callable[[str, str], None]] = None,
) -> Tuple[Optional[pd.DataFrame], str]:
    """
    Fetch daily OHLCV via non-Yahoo fallback chain.
    Returns tuple (dataframe_or_none, source_name).
    """
    d = fetch_ibkr_gcloud_daily_ohlcv(symbol, period, gcloud_base_url, gcloud_api_key)
    if d is not None and not d.empty:
        if progress_hook:
            progress_hook(symbol, "ibkr_gcloud")
        return d, "ibkr_gcloud"

    d = fetch_ibkr_local_daily_ohlcv(
        symbol,
        period,
        host=local_ibkr_host,
        port=local_ibkr_port,
        client_id=local_ibkr_client_id,
    )
    if d is not None and not d.empty:
        if progress_hook:
            progress_hook(symbol, "ibkr_local")
        return d, "ibkr_local"

    d = fetch_stooq_daily_ohlcv(symbol)
    if d is not None and not d.empty:
        if progress_hook:
            progress_hook(symbol, "stooq")
        return d, "stooq"

    if progress_hook:
        progress_hook(symbol, "none")
    return None, "none"
