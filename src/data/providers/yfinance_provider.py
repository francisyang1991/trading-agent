"""
Yahoo Finance provider implementation.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
import yfinance as yf

from .base import FundamentalDataProvider, MarketDataProvider


class YFinanceProvider(MarketDataProvider, FundamentalDataProvider):
    """Provider backed by yfinance."""

    @staticmethod
    def _classify_earnings_session(ts: pd.Timestamp) -> tuple[str, str]:
        """
        Map an earnings timestamp to ET clock time and session bucket.
        """
        if ts is None or pd.isna(ts):
            return "", "unknown"

        t = pd.Timestamp(ts)
        try:
            if t.tzinfo is None:
                t = t.tz_localize("America/New_York")
            else:
                t = t.tz_convert("America/New_York")
        except Exception:
            return "", "unknown"

        hhmm = f"{int(t.hour):02d}:{int(t.minute):02d}"
        minute_of_day = int(t.hour) * 60 + int(t.minute)

        # 00:00 from some feeds often means date-only (unknown session).
        if int(t.hour) == 0 and int(t.minute) == 0:
            return hhmm, "unknown"
        if minute_of_day < (9 * 60 + 30):
            return hhmm, "pre-market"
        if minute_of_day >= (16 * 60):
            return hhmm, "post-market"
        return hhmm, "in-market"

    def _normalize_ohlcv(self, raw: pd.DataFrame) -> Optional[pd.DataFrame]:
        if raw is None or raw.empty:
            return None

        df = raw.copy()

        if isinstance(df.columns, pd.MultiIndex):
            # yfinance can return [('Close','NVDA'), ...] even for single ticker.
            df.columns = [c[0] for c in df.columns]

        # Normalize expected columns
        col_map = {
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Adj Close": "Adj Close",
            "Volume": "Volume",
        }
        missing = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c not in df.columns]
        if missing:
            return None

        if "Adj Close" not in df.columns:
            df["Adj Close"] = df["Close"]

        df = df.reset_index()
        if "Date" not in df.columns and "Datetime" in df.columns:
            df.rename(columns={"Datetime": "Date"}, inplace=True)
        if "Date" not in df.columns:
            return None

        out = df[["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]].copy()
        out["Date"] = pd.to_datetime(out["Date"]).dt.date
        return out

    @staticmethod
    def to_yahoo_symbol(symbol: str) -> str:
        return symbol.replace(".", "-").upper()

    def get_daily_ohlcv(self, symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        t = yf.Ticker(self.to_yahoo_symbol(symbol))
        raw = t.history(period=period, auto_adjust=False)
        return self._normalize_ohlcv(raw)

    def get_index_ohlcv(self, index_symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
        t = yf.Ticker(index_symbol)
        raw = t.history(period=period, auto_adjust=False)
        return self._normalize_ohlcv(raw)

    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        info = yf.Ticker(self.to_yahoo_symbol(symbol)).info
        if not info:
            return None
        return {
            "symbol": symbol.upper(),
            "name": info.get("shortName", symbol.upper()),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            # Numeric fields: use None when missing, NEVER default to 0.
            # Defaulting to 0 turns "no data" into "zero value" — a data integrity lie.
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "dividend_yield": info.get("dividendYield"),
            "profit_margin": info.get("profitMargins"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "source": "yfinance",
        }

    def get_quarterly_fundamentals(self, symbol: str, limit: int = 20) -> Optional[pd.DataFrame]:
        t = yf.Ticker(self.to_yahoo_symbol(symbol))
        income = t.quarterly_income_stmt
        if income is None or income.empty:
            return None

        # Also fetch balance sheet for ROE computation (net_income / shareholders_equity).
        balance = t.quarterly_balance_sheet
        equity_map: Dict = {}
        if balance is not None and not balance.empty:
            for col in balance.columns:
                d_str = str(pd.to_datetime(col).date())
                equity_val = None
                for eq_key in (
                    "Stockholders Equity",
                    "Total Stockholders Equity",
                    "Stockholders' Equity",
                    "Common Stock Equity",
                    "Total Equity Gross Minority Interest",
                ):
                    if eq_key in balance.index:
                        v = balance.loc[eq_key, col]
                        if v is not None and pd.notna(v) and v != 0:
                            equity_val = float(v)
                            break
                if equity_val is not None:
                    equity_map[d_str] = equity_val

        # yfinance columns are period end dates.
        columns = list(income.columns)[:limit]
        rows = []
        for d in columns:
            report_date = pd.to_datetime(d).date()
            # yfinance lacks explicit disclosure date, fallback to report_date.
            disclosure_date = report_date

            eps = None
            for eps_key in ("Diluted EPS", "Basic EPS", "Normalized Diluted EPS"):
                if eps_key in income.index:
                    eps = income.loc[eps_key, d]
                    break

            revenue = None
            for rev_key in ("Total Revenue", "Operating Revenue"):
                if rev_key in income.index:
                    revenue = income.loc[rev_key, d]
                    break

            gross_margin = None
            gross_profit = income.loc["Gross Profit", d] if "Gross Profit" in income.index else None
            if revenue and pd.notna(revenue) and revenue != 0 and gross_profit is not None and pd.notna(gross_profit):
                gross_margin = float(gross_profit) / float(revenue)

            # Compute ROE = net_income / shareholders_equity for this quarter.
            roe = None
            net_income = None
            for ni_key in ("Net Income", "Net Income Common Stockholders"):
                if ni_key in income.index:
                    ni_val = income.loc[ni_key, d]
                    if ni_val is not None and pd.notna(ni_val):
                        net_income = float(ni_val)
                        break
            equity = equity_map.get(str(report_date))
            if net_income is not None and equity is not None and equity != 0:
                roe = net_income / equity

            rows.append(
                {
                    "ticker": symbol.upper(),
                    "report_date": report_date,
                    "disclosure_date": disclosure_date,
                    "eps": float(eps) if eps is not None and pd.notna(eps) else None,
                    "revenue": float(revenue) if revenue is not None and pd.notna(revenue) else None,
                    "roe": roe,
                    "gross_margin": gross_margin,
                    "source": "yfinance",
                }
            )

        return pd.DataFrame(rows).sort_values("report_date")

    def get_analyst_estimates(self, symbol: str, limit: int = 8) -> Optional[pd.DataFrame]:
        # yfinance does not provide robust analyst estimate history.
        return pd.DataFrame(
            columns=["ticker", "report_date", "estimated_eps", "estimated_revenue", "source"]
        )

    def get_earnings_calendar(self, symbol: str, limit: int = 12) -> Optional[pd.DataFrame]:
        t = yf.Ticker(self.to_yahoo_symbol(symbol))
        try:
            df = t.get_earnings_dates(limit=limit)
        except Exception:
            return None
        if df is None or df.empty:
            return None

        rows = []
        for idx in df.index:
            ts = pd.to_datetime(idx)
            d = ts.date()
            hhmm_et, session = self._classify_earnings_session(ts)
            rows.append(
                {
                    "ticker": symbol.upper(),
                    "report_date": d,
                    "disclosure_date": d,
                    "release_time_et": hhmm_et,
                    "release_session": session,
                    "source": "yfinance",
                }
            )
        return pd.DataFrame(rows).sort_values("report_date")

    def get_factor_fundamentals(self, symbol: str) -> Dict:
        """
        Compact fundamentals snapshot for factor filtering.
        """
        tk = yf.Ticker(self.to_yahoo_symbol(symbol))
        info = tk.info or {}
        eps_yoy = info.get("earningsGrowth")
        revenue_growth = info.get("revenueGrowth")
        roe = info.get("returnOnEquity")
        gross_margin = info.get("grossMargins")
        debt_to_equity = info.get("debtToEquity")

        revenue_acceleration = None
        try:
            q = tk.quarterly_income_stmt
            if q is not None and not q.empty and ("Total Revenue" in q.index or "Operating Revenue" in q.index):
                key = "Total Revenue" if "Total Revenue" in q.index else "Operating Revenue"
                s = q.loc[key].dropna().astype(float)
                if len(s) >= 3:
                    r0, r1, r2 = float(s.iloc[0]), float(s.iloc[1]), float(s.iloc[2])
                    if r1 != 0 and r2 != 0:
                        g_now = r0 / r1 - 1.0
                        g_prev = r1 / r2 - 1.0
                        revenue_acceleration = g_now - g_prev
                        if revenue_growth is None:
                            revenue_growth = g_now
        except Exception:
            pass

        return {
            "ticker": symbol.upper(),
            "eps_yoy": eps_yoy,
            "revenue_growth": revenue_growth,
            "revenue_acceleration": revenue_acceleration,
            "earnings_surprise": None,
            "roe": roe,
            "gross_margin": gross_margin,
            "debt_to_equity": debt_to_equity,
        }


def batch_download_daily_ohlcv(
    symbols: List[str],
    period: str,
    batch_size: int = 200,
    threads: bool = False,
    progress_hook: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, pd.DataFrame]:
    """
    Batch download daily OHLCV from Yahoo with symbol normalization.
    """
    out: Dict[str, pd.DataFrame] = {}
    total = len(symbols)
    processed = 0
    for i in range(0, total, batch_size):
        batch = symbols[i : i + batch_size]
        ymap = {YFinanceProvider.to_yahoo_symbol(s): s for s in batch}
        ybatch = list(ymap.keys())
        try:
            raw = yf.download(
                tickers=" ".join(ybatch),
                period=period,
                group_by="ticker",
                auto_adjust=False,
                threads=threads,
                progress=False,
            )
        except Exception:
            raw = None

        if raw is not None and not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                top = set(raw.columns.get_level_values(0))
                for ysym, orig in ymap.items():
                    if ysym not in top:
                        continue
                    sub = raw[ysym].copy()
                    if sub.empty or "Close" not in sub.columns:
                        continue
                    sub = sub.reset_index()
                    if "Date" not in sub.columns and "Datetime" in sub.columns:
                        sub.rename(columns={"Datetime": "Date"}, inplace=True)
                    if "Date" in sub.columns:
                        sub["Date"] = pd.to_datetime(sub["Date"]).dt.date
                    out[orig] = sub
            else:
                orig = ymap[ybatch[0]]
                if "Close" in raw.columns:
                    sub = raw.reset_index()
                    if "Date" in sub.columns:
                        sub["Date"] = pd.to_datetime(sub["Date"]).dt.date
                    out[orig] = sub

        processed += len(batch)
        if progress_hook:
            progress_hook(processed, total)

    return out
