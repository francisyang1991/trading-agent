"""
FinancialDatasets provider implementation.
https://docs.financialdatasets.ai/introduction
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import pandas as pd

from .base import FundamentalDataProvider


class FinancialDatasetsProvider(FundamentalDataProvider):
    """Fundamental provider backed by FinancialDatasets API."""

    BASE_URL = "https://api.financialdatasets.ai"

    def __init__(self, api_key: Optional[str] = None, timeout_seconds: int = 20):
        self.api_key = api_key or os.getenv("FINANCIALDATASETS_API_KEY", "")
        self.timeout_seconds = timeout_seconds

    def _enabled(self) -> bool:
        return bool(self.api_key)

    def _get_json(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        if not self._enabled():
            return None

        # Ensure trailing slash — the API returns 301 without it,
        # and urllib strips custom headers (X-API-KEY) on redirect,
        # causing silent 401/403 failures.
        path_clean = path.strip("/") + "/"
        q = urlencode(params or {})
        url = f"{self.BASE_URL}/{path_clean}"
        if q:
            url = f"{url}?{q}"
        req = Request(url, headers={
            "X-API-KEY": self.api_key,
            "User-Agent": "trading-agent/1.0",
            "Accept": "application/json",
        })
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload)
        except HTTPError as e:
            print(f"  [FinancialDatasets] HTTP {e.code} for {url}")
            return None
        except (URLError, json.JSONDecodeError) as e:
            print(f"  [FinancialDatasets] Error for {url}: {e}")
            return None

    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        symbol = symbol.upper()
        facts = self._get_json("company/facts", {"ticker": symbol}) or {}
        facts = facts.get("company_facts", facts or {})

        metrics = self._get_json("financial-metrics/snapshot", {"ticker": symbol}) or {}
        metrics = metrics.get("snapshot", metrics or {})

        if not facts and not metrics:
            return None

        return {
            "symbol": symbol,
            "name": facts.get("name"),
            "sector": facts.get("sector") or facts.get("sic_sector"),
            "industry": facts.get("industry") or facts.get("sic_industry"),
            "market_cap": facts.get("market_cap"),
            "beta": None,
            "pe_ratio": metrics.get("price_to_earnings_ratio"),
            "forward_pe": None,
            "dividend_yield": None,
            "profit_margin": metrics.get("net_margin"),
            "revenue_growth": metrics.get("revenue_growth"),
            "earnings_growth": metrics.get("earnings_growth"),
            "roe": metrics.get("return_on_equity"),
            "gross_margin": metrics.get("gross_margin"),
            "debt_to_equity": metrics.get("debt_to_equity"),
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
            "avg_volume": None,
            "shares_outstanding": facts.get("weighted_average_shares"),
            "source": "financialdatasets",
        }

    def get_quarterly_fundamentals(self, symbol: str, limit: int = 20) -> Optional[pd.DataFrame]:
        symbol = symbol.upper()
        income = self._get_json(
            "financials/income-statements",
            {"ticker": symbol, "period": "quarterly", "limit": limit},
        )
        balance = self._get_json(
            "financials/balance-sheets",
            {"ticker": symbol, "period": "quarterly", "limit": limit},
        )
        if not income and not balance:
            return None

        income_rows = (income or {}).get("income_statements", []) if isinstance(income, dict) else []
        balance_rows = (balance or {}).get("balance_sheets", []) if isinstance(balance, dict) else []

        balance_map = {}
        for row in balance_rows:
            report_period = row.get("report_period")
            if report_period:
                balance_map[str(report_period)] = row

        out_rows = []
        for row in income_rows:
            report_period = row.get("report_period")
            if not report_period:
                continue

            revenue = row.get("revenue")
            gross_profit = row.get("gross_profit")
            gross_margin = None
            if revenue and gross_profit is not None and revenue != 0:
                gross_margin = float(gross_profit) / float(revenue)

            eps = row.get("earnings_per_share_diluted")
            if eps is None:
                eps = row.get("earnings_per_share")

            b = balance_map.get(str(report_period), {})
            roe = None
            net_income = row.get("net_income")
            equity = b.get("shareholders_equity")
            if net_income is not None and equity not in (None, 0):
                roe = float(net_income) / float(equity)

            out_rows.append(
                {
                    "ticker": symbol,
                    "report_date": pd.to_datetime(report_period).date(),
                    "disclosure_date": pd.to_datetime(report_period).date(),
                    "eps": float(eps) if eps is not None else None,
                    "revenue": float(revenue) if revenue is not None else None,
                    "roe": roe,
                    "gross_margin": gross_margin,
                    "source": "financialdatasets",
                }
            )

        if not out_rows:
            return None

        return pd.DataFrame(out_rows).sort_values("report_date")

    def get_analyst_estimates(self, symbol: str, limit: int = 8) -> Optional[pd.DataFrame]:
        symbol = symbol.upper()
        data = self._get_json("analyst-estimates", {"ticker": symbol, "period": "annual"})
        rows = (data or {}).get("analyst_estimates", []) if isinstance(data, dict) else []
        if not rows:
            return None

        out = []
        for row in rows[:limit]:
            fiscal_period = row.get("fiscal_period")
            if not fiscal_period:
                continue
            out.append(
                {
                    "ticker": symbol,
                    "report_date": pd.to_datetime(fiscal_period).date(),
                    "estimated_eps": row.get("earnings_per_share"),
                    "estimated_revenue": None,
                    "source": "financialdatasets",
                }
            )

        if not out:
            return None
        return pd.DataFrame(out)

    def get_earnings_calendar(self, symbol: str, limit: int = 12) -> Optional[pd.DataFrame]:
        # Not exposed in FinancialDatasets docs at the moment.
        return pd.DataFrame(columns=["ticker", "report_date", "disclosure_date", "source"])
