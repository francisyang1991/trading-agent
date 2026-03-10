"""
IBKR GCloud Fundamental Data Provider.

Fetches fundamental data by calling the IBKR-connected Flask GUI
running on GCP (http://<host>:8080/api/fundamentals/...).

The Flask GUI has a live ib_async connection to the IB Gateway and
serves parsed Reuters fundamental data over HTTP/JSON.

Config (from picker_config.yaml → ibkr section):
  gcloud_trade_api_url: "http://34.75.9.166:8080"
  gcloud_trade_api_key: "saiyan-trade-2026"
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .base import FundamentalDataProvider


class IBKRGcloudFundamentalProvider(FundamentalDataProvider):
    """Fetch fundamentals from the GCP Trading GUI's IBKR-backed endpoints."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 15,
    ):
        self.base_url = (
            base_url
            or os.environ.get("IBKR_GCLOUD_URL")
            or "http://34.75.9.166:8080"
        )
        self.api_key = (
            api_key
            or os.environ.get("IBKR_GCLOUD_API_KEY")
            or "saiyan-trade-2026"
        )
        self.timeout = timeout

    def _enabled(self) -> bool:
        """Provider is enabled if we have a URL and key."""
        return bool(self.base_url and self.api_key)

    def _get_json(self, path: str) -> Optional[any]:
        """HTTP GET → parsed JSON."""
        url = f"{self.base_url.rstrip('/')}{path}"
        req = Request(url, headers={
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "trading-agent/1.0",
        })
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload)
        except HTTPError as e:
            if e.code not in (404, 503):
                # Don't spam logs for expected failures
                print(f"  [IBKRGcloud] HTTP {e.code} for {url}")
            return None
        except (URLError, json.JSONDecodeError, OSError):
            return None

    # ── FundamentalDataProvider interface ──────────────────────────────────

    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        """
        Fetch key ratios (ROE, margin, D/E, market cap, EPS, revenue)
        from IBKR via GCP gateway.
        """
        data = self._get_json(f"/api/fundamentals/profile/{symbol.upper()}")
        if not data or "error" in data:
            return None

        # Normalize to the canonical profile format used by the picker.
        return {
            "ticker": symbol.upper(),
            "market_cap": data.get("market_cap"),
            "pe_ratio": data.get("pe_ratio"),
            "eps": data.get("eps"),
            "revenue": data.get("revenue"),
            "roe": data.get("roe"),
            "gross_margin": data.get("gross_margin"),
            "debt_to_equity": data.get("debt_to_equity"),
            "source": "ibkr_reuters",
        }

    def get_quarterly_fundamentals(
        self, symbol: str, limit: int = 20
    ) -> Optional[pd.DataFrame]:
        """
        Fetch quarterly EPS, revenue, ROE, gross margin from IBKR
        via GCP gateway.
        """
        data = self._get_json(f"/api/fundamentals/quarterly/{symbol.upper()}")
        if not data or isinstance(data, dict):
            # Error response is a dict with "error" key
            return None

        if not isinstance(data, list) or len(data) == 0:
            return None

        df = pd.DataFrame(data)

        # Ensure canonical columns
        for col in ("ticker", "report_date", "disclosure_date", "eps", "revenue", "roe", "gross_margin"):
            if col not in df.columns:
                df[col] = None

        df["ticker"] = symbol.upper()

        # Convert date strings
        for dcol in ("report_date", "disclosure_date"):
            if dcol in df.columns:
                df[dcol] = pd.to_datetime(df[dcol], errors="coerce").dt.date

        # Sort descending by report_date, limit
        df = df.sort_values("report_date", ascending=False).head(limit)
        return df

    def get_analyst_estimates(self, symbol: str, limit: int = 8) -> Optional[pd.DataFrame]:
        """
        IBKR RESC (analyst estimates) endpoint — not yet implemented
        on the Flask GUI. Returns None so the chain falls through.
        """
        return None

    def get_earnings_calendar(self, symbol: str, limit: int = 12) -> Optional[pd.DataFrame]:
        """
        IBKR CalendarReport endpoint — not yet implemented
        on the Flask GUI. Returns None so the chain falls through.
        """
        return None
