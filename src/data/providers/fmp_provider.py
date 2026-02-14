"""
Financial Modeling Prep provider implementation.
"""

from __future__ import annotations

import os
import json
from typing import Dict, Optional
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from .base import FundamentalDataProvider


class FMPProvider(FundamentalDataProvider):
    """Fundamental provider backed by Financial Modeling Prep API."""

    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: Optional[str] = None, timeout_seconds: int = 15):
        self.api_key = api_key or os.getenv("FMP_API_KEY", "")
        self.timeout_seconds = timeout_seconds

    def _enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        if not self._enabled():
            return None

        q = dict(params or {})
        q["apikey"] = self.api_key
        url = f"{self.BASE_URL}/{path.lstrip('/')}?{urlencode(q)}"
        with urlopen(url, timeout=self.timeout_seconds) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload)

    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        payload = self._get(f"profile/{symbol.upper()}")
        if not payload:
            return None
        rec = payload[0] if isinstance(payload, list) and payload else None
        if not rec:
            return None

        return {
            "symbol": rec.get("symbol", symbol.upper()),
            "name": rec.get("companyName", symbol.upper()),
            "sector": rec.get("sector", "Unknown"),
            "industry": rec.get("industry", "Unknown"),
            "market_cap": rec.get("mktCap", 0),
            "beta": rec.get("beta", 0),
            "pe_ratio": rec.get("pe", 0),
            "forward_pe": rec.get("pe", 0),
            "dividend_yield": rec.get("lastDiv", 0),
            "profit_margin": rec.get("netProfitMargin", 0),
            "revenue_growth": rec.get("revenueGrowth", 0),
            "earnings_growth": rec.get("epsGrowth", 0),
            "fifty_two_week_high": rec.get("range", "").split("-")[-1].strip() if rec.get("range") else 0,
            "fifty_two_week_low": rec.get("range", "").split("-")[0].strip() if rec.get("range") else 0,
            "avg_volume": rec.get("volAvg", 0),
            "shares_outstanding": rec.get("sharesOutstanding", 0),
            "source": "fmp",
        }

    def get_quarterly_fundamentals(self, symbol: str, limit: int = 20) -> Optional[pd.DataFrame]:
        # Income statement for EPS/revenue/gross margin.
        income = self._get(
            f"income-statement/{symbol.upper()}",
            {"period": "quarter", "limit": limit},
        )
        if not income or not isinstance(income, list):
            return None

        rows = []
        for rec in income:
            report_date = pd.to_datetime(rec.get("date")).date() if rec.get("date") else None
            # Use acceptedDate when present, fallback to filingDate/date.
            disclosure_raw = rec.get("acceptedDate") or rec.get("fillingDate") or rec.get("date")
            disclosure_date = pd.to_datetime(disclosure_raw).date() if disclosure_raw else report_date
            revenue = rec.get("revenue")
            gross_profit = rec.get("grossProfit")
            gross_margin = None
            if revenue and gross_profit:
                gross_margin = float(gross_profit) / float(revenue) if float(revenue) != 0 else None

            rows.append(
                {
                    "ticker": symbol.upper(),
                    "report_date": report_date,
                    "disclosure_date": disclosure_date,
                    "eps": rec.get("eps"),
                    "revenue": revenue,
                    "roe": None,
                    "gross_margin": gross_margin,
                    "source": "fmp",
                }
            )
        df = pd.DataFrame(rows).dropna(subset=["report_date", "disclosure_date"])
        return df.sort_values("report_date")

    def get_analyst_estimates(self, symbol: str, limit: int = 8) -> Optional[pd.DataFrame]:
        payload = self._get(f"analyst-estimates/{symbol.upper()}", {"limit": limit})
        if not payload or not isinstance(payload, list):
            return None

        rows = []
        for rec in payload:
            report_date = rec.get("date")
            rows.append(
                {
                    "ticker": symbol.upper(),
                    "report_date": pd.to_datetime(report_date).date() if report_date else None,
                    "estimated_eps": rec.get("estimatedEpsAvg"),
                    "estimated_revenue": rec.get("estimatedRevenueAvg"),
                    "source": "fmp",
                }
            )
        return pd.DataFrame(rows).dropna(subset=["report_date"]).sort_values("report_date")

    def get_earnings_calendar(self, symbol: str, limit: int = 12) -> Optional[pd.DataFrame]:
        payload = self._get(
            "historical/earning_calendar/" + symbol.upper(),
            {"limit": limit},
        )
        if not payload or not isinstance(payload, list):
            return None

        rows = []
        for rec in payload:
            report_date = rec.get("date")
            rows.append(
                {
                    "ticker": symbol.upper(),
                    "report_date": pd.to_datetime(report_date).date() if report_date else None,
                    "disclosure_date": pd.to_datetime(report_date).date() if report_date else None,
                    "source": "fmp",
                }
            )
        return pd.DataFrame(rows).dropna(subset=["report_date"]).sort_values("report_date")
