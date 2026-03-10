#!/usr/bin/env python3
"""
CACHED DATA MANAGER (CANONICAL FOR SCANNING & BACKTESTING)
==========================================================

This is the CANONICAL data manager for scanning and backtesting.
Use this for all daily OHLCV data access. It provides:

1. SQLite database for local storage (data/stock_cache.db)
2. Automatic data freshness checking
3. Incremental updates (only fetch missing days)
4. Fundamental data caching (refreshed weekly)
5. Thread-safe operations

For IBKR multi-timeframe live data, use IBKRDataManager from src/data/.

Usage:
    # Recommended: Use the factory function
    from src.data import get_data_manager
    dm = get_data_manager("cached")
    
    # Or import directly
    from src.data_manager import DataManager
    dm = DataManager()
    
    # Get daily data (uses cache, fetches only if needed)
    data = dm.get_daily_data("NVDA", period="1y")
    
    # Get fundamentals (cached, refreshed weekly)
    info = dm.get_fundamentals("NVDA")
    
    # Force refresh
    data = dm.get_daily_data("NVDA", force_refresh=True)
    
    # Bulk preload (for scanner)
    dm.preload_symbols(["NVDA", "AMD", "GOOGL"])

Tables:
- stock_daily: OHLCV data (Date, Open, High, Low, Close, Volume)
- stock_fundamentals: Sector, Industry, MarketCap, Beta, etc.
- data_metadata: Last update timestamps
"""

import sqlite3
import os
import sys
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import warnings
warnings.filterwarnings('ignore')

from src.data.factor_alignment import align_fundamentals_to_daily
from src.data.parquet_store import ParquetDataStore
from src.data.mysql_store import MySQLFundamentalsStore
from src.data.providers import (
    FMPProvider,
    IBKRGcloudFundamentalProvider,
    IBKRGcloudMarketProvider,
    IBKRLocalMarketProvider,
    ResilientFundamentalProvider,
    ResilientMarketDataProvider,
    YFinanceProvider,
)
from src.data.routing import DataRoutingConfig
from src.data.schemas import validate_daily_ohlcv, validate_quarterly_fundamentals


# ============================================================================
# DATABASE SETUP
# ============================================================================

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'stock_cache.db')


def get_connection() -> sqlite3.Connection:
    """Get database connection with proper settings."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_database():
    """Initialize database tables."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Stock daily OHLCV data
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily (
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            adj_close REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    
    # Create index for faster queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_stock_daily_symbol 
        ON stock_daily (symbol)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_stock_daily_date 
        ON stock_daily (date DESC)
    """)
    
    # Stock fundamentals
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_fundamentals (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            sector TEXT,
            industry TEXT,
            market_cap REAL,
            beta REAL,
            pe_ratio REAL,
            forward_pe REAL,
            dividend_yield REAL,
            profit_margin REAL,
            gross_margin REAL,
            revenue_growth REAL,
            earnings_growth REAL,
            roe REAL,
            debt_to_equity REAL,
            fifty_two_week_high REAL,
            fifty_two_week_low REAL,
            avg_volume REAL,
            shares_outstanding REAL,
            last_updated TIMESTAMP
        )
    """)

    # Ensure newly added columns exist for existing DBs.
    cursor.execute("PRAGMA table_info(stock_fundamentals)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    for col_name, col_type in (
        ("gross_margin", "REAL"),
        ("roe", "REAL"),
        ("debt_to_equity", "REAL"),
    ):
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE stock_fundamentals ADD COLUMN {col_name} {col_type}")
    
    # Metadata for tracking updates
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS data_metadata (
            symbol TEXT PRIMARY KEY,
            daily_last_date DATE,
            daily_last_updated TIMESTAMP,
            fundamentals_last_updated TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    print(f"✅ Database initialized at: {DB_PATH}")


# Initialize on import
init_database()


# ============================================================================
# DATA MANAGER CLASS
# ============================================================================

class DataManager:
    """
    Manages stock data with local SQLite caching.
    """
    
    def __init__(
        self,
        max_daily_age_hours: int = 12,
        max_fundamental_age_days: int = 7,
        parquet_root_dir: str = "data",
        data_mode: Optional[str] = None,
        routing: Optional[DataRoutingConfig] = None,
    ):
        """
        Initialize DataManager.
        
        Args:
            max_daily_age_hours: Max age for daily data before refresh (default 12h)
            max_fundamental_age_days: Max age for fundamentals before refresh (default 7d)
        """
        self.max_daily_age = timedelta(hours=max_daily_age_hours)
        self.max_fundamental_age = timedelta(days=max_fundamental_age_days)
        self._lock = threading.Lock()
        self._cache = {}  # In-memory cache for current session
        self.parquet_store = ParquetDataStore(parquet_root_dir)
        self.mysql_store = MySQLFundamentalsStore()
        self.routing = (routing or DataRoutingConfig.from_env()).with_mode(data_mode)

        yf_provider = YFinanceProvider()
        gcloud_market = IBKRGcloudMarketProvider(
            base_url=self.routing.gcloud_base_url or None,
            api_key=self.routing.gcloud_api_key or None,
        )
        local_market = IBKRLocalMarketProvider(
            host=self.routing.local_gateway_host,
            port=self.routing.local_gateway_port,
            client_id=self.routing.local_gateway_client_id,
        )
        market_providers = {
            "yfinance": yf_provider,
            "gcloud": gcloud_market,
            "ibkr_local": local_market,
        }
        self.market_provider_order = self.routing.daily_market_order()
        active_market_providers = [
            market_providers[name]
            for name in self.market_provider_order
            if getattr(market_providers[name], "_enabled", lambda: True)()
        ]
        self.market_provider = ResilientMarketDataProvider(active_market_providers)

        ibkr_fund = IBKRGcloudFundamentalProvider(
            base_url=self.routing.gcloud_base_url or None,
            api_key=self.routing.gcloud_api_key or None,
        )
        fundamental_providers = {
            "yfinance": yf_provider,
            "gcloud": ibkr_fund,
            "fmp": FMPProvider(),
        }
        self.fundamental_provider_order = self.routing.fundamental_order()
        active_fundamentals = [
            fundamental_providers[name]
            for name in self.fundamental_provider_order
            if getattr(fundamental_providers[name], "_enabled", lambda: True)()
        ]
        self.fundamental_provider = ResilientFundamentalProvider(active_fundamentals)
    
    def get_connection(self) -> sqlite3.Connection:
        """Get thread-safe database connection."""
        return get_connection()
    
    # -------------------------------------------------------------------------
    # DAILY DATA
    # -------------------------------------------------------------------------
    
    def get_daily_data(
        self,
        symbol: str,
        period: str = "1y",
        force_refresh: bool = False
    ) -> Optional[pd.DataFrame]:
        """
        Get daily OHLCV data for a symbol.
        
        Uses cache if data is fresh, otherwise fetches from API.
        
        Args:
            symbol: Stock symbol
            period: Data period (1y, 2y, etc.)
            force_refresh: Force API refresh even if cache is fresh
            
        Returns:
            DataFrame with OHLCV data or None if error
        """
        symbol = symbol.upper()
        
        # Check in-memory cache first (fastest)
        cache_key = f"{symbol}_{period}"
        if cache_key in self._cache and not force_refresh:
            cached_data, cached_time = self._cache[cache_key]
            if datetime.now() - cached_time < timedelta(minutes=5):
                return cached_data.copy()
        
        # Check if we need to refresh from API
        # NOTE: freshness alone is not enough — we also need sufficient historical coverage
        # for the requested `period` (e.g., cached 1y shouldn't satisfy a 2y request).
        needs_refresh = (
            force_refresh
            or self._needs_daily_refresh(symbol)
            or self._needs_daily_coverage(symbol, period)
        )
        
        if needs_refresh:
            # Fetch from API
            api_data = self._fetch_daily_from_api(symbol, period)
            if api_data is not None and not api_data.empty:
                # Save to database (expects Date column)
                self._save_daily_to_db(symbol, api_data)
                out = self._standardize_daily_output(api_data)
                # Update in-memory cache (store standardized)
                self._cache[cache_key] = (out.copy(), datetime.now())
                return out
        
        # Load from database
        data = self._load_daily_from_db(symbol, period)
        if data is not None and not data.empty:
            out = self._standardize_daily_output(data)
            self._cache[cache_key] = (out.copy(), datetime.now())
            return out
        
        # Fallback: fetch from API
        api_data = self._fetch_daily_from_api(symbol, period)
        if api_data is not None and not api_data.empty:
            self._save_daily_to_db(symbol, api_data)
            out = self._standardize_daily_output(api_data)
            self._cache[cache_key] = (out.copy(), datetime.now())
            return out

        return None

    def _standardize_daily_output(self, data: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        """
        Standardize daily OHLCV data format returned to callers.

        Guarantees:
        - DatetimeIndex named 'Date'
        - Columns: Open, High, Low, Close, Volume

        Internally we may fetch API data in a 'Date' column format for DB writes; callers
        should always receive an indexed DataFrame for consistent downstream logic.
        """
        if data is None or getattr(data, "empty", True):
            return data

        # If API-style with a Date column
        if "Date" in data.columns:
            out = data.copy()
            out["Date"] = pd.to_datetime(out["Date"])
            out = out.set_index("Date")
            # Ensure the column order exists
            cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in out.columns]
            return out[cols].copy()

        # If DB-style already indexed
        if isinstance(data.index, pd.DatetimeIndex):
            return data.copy()

        return data.copy()

    def _period_to_days(self, period: str) -> int:
        """Map period string to approximate day count."""
        days_map = {
            '1mo': 30,
            '3mo': 90,
            '6mo': 180,
            '1y': 365,
            '2y': 730,
            '3y': 1095,
            '5y': 1825,
            '10y': 3650,
            'max': 36500,
        }
        return days_map.get(period, 365)

    def _sanitize_daily_frame(self, data: Optional[pd.DataFrame]) -> pd.DataFrame:
        """
        Enforce daily OHLCV quality constraints before cache reads/writes.
        """
        if data is None or getattr(data, "empty", True):
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

        out = data.copy()
        if "Date" not in out.columns:
            if isinstance(out.index, pd.DatetimeIndex):
                out = out.reset_index().rename(columns={out.index.name or "index": "Date"})
            else:
                return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

        required = ["Date", "Open", "High", "Low", "Close", "Volume"]
        for col in required:
            if col not in out.columns:
                out[col] = pd.NA

        out = out[required].copy()
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")
            out[col] = out[col].replace([np.inf, -np.inf], np.nan)

        out = out.dropna(subset=["Date", "Open", "High", "Low", "Close"])
        out = out[
            (out["Open"] > 0)
            & (out["High"] > 0)
            & (out["Low"] > 0)
            & (out["Close"] > 0)
            & (out["High"] >= out["Low"])
        ]
        out["Volume"] = out["Volume"].fillna(0).clip(lower=0).astype(int)
        out["Date"] = out["Date"].dt.date
        out = out.drop_duplicates(subset=["Date"], keep="last").sort_values("Date")
        return out

    def _needs_daily_coverage(self, symbol: str, period: str) -> bool:
        """
        Return True if the DB does not have enough historical coverage for `period`.
        This is different from staleness: data can be fresh but incomplete for longer periods.
        """
        try:
            days = self._period_to_days(period)
            desired_start_date = (datetime.now() - timedelta(days=days)).date()

            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT MIN(date) FROM stock_daily
                WHERE symbol = ?
                  AND open IS NOT NULL
                  AND high IS NOT NULL
                  AND low IS NOT NULL
                  AND close IS NOT NULL
                  AND open > 0
                  AND high > 0
                  AND low > 0
                  AND close > 0
                  AND high >= low
                """,
                (symbol,),
            )
            row = cursor.fetchone()
            conn.close()

            if row is None or row[0] is None:
                return True

            # SQLite returns date as 'YYYY-MM-DD' string
            min_date = datetime.fromisoformat(row[0]).date() if isinstance(row[0], str) else row[0]
            return min_date > desired_start_date
        except Exception:
            # Fail safe: if we can't confirm coverage, fetch
            return True
    
    def _needs_daily_refresh(self, symbol: str) -> bool:
        """Check if daily data needs refresh."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT daily_last_updated FROM data_metadata WHERE symbol = ?
        """, (symbol,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result is None:
            return True
        
        last_updated = datetime.fromisoformat(result[0]) if result[0] else None
        if last_updated is None:
            return True
        
        # Check if data is stale
        if datetime.now() - last_updated > self.max_daily_age:
            return True
        
        # Check if market has closed since last update (for intraday freshness)
        now = datetime.now()
        market_close = now.replace(hour=16, minute=0, second=0)
        if now.weekday() < 5 and now > market_close and last_updated < market_close:
            return True
        
        return False
    
    def _fetch_daily_from_api(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """Fetch daily data from Yahoo Finance API."""
        try:
            data = self.market_provider.get_daily_ohlcv(symbol, period=period)
            if data is None or data.empty:
                return None
            validated = validate_daily_ohlcv(data)
            # Keep legacy DB format while preserving adj_close in parquet lane.
            self.parquet_store.save_daily(symbol, validated)
            out = validated.rename(
                columns={
                    "date": "Date",
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )
            return out[["Date", "Open", "High", "Low", "Close", "Volume"]]
            
        except Exception as e:
            print(f"   ⚠️ API error for {symbol}: {e}")
            return None
    
    def _save_daily_to_db(self, symbol: str, data: pd.DataFrame):
        """Save daily data to database (bulk insert)."""
        clean = self._sanitize_daily_frame(data)
        if clean.empty:
            return

        with self._lock:
            conn = self.get_connection()
            cursor = conn.cursor()

            rows = [
                (
                    symbol,
                    row["Date"],
                    row["Open"],
                    row["High"],
                    row["Low"],
                    row["Close"],
                    int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
                )
                for _, row in clean.iterrows()
            ]
            cursor.executemany(
                """
                INSERT OR REPLACE INTO stock_daily 
                (symbol, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

            # Update metadata
            cursor.execute(
                """
                INSERT OR REPLACE INTO data_metadata 
                (symbol, daily_last_date, daily_last_updated)
                VALUES (?, ?, ?)
                """,
                (symbol, clean["Date"].max(), datetime.now().isoformat()),
            )

            conn.commit()
            conn.close()
    
    def _load_daily_from_db(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """Load daily data from database."""
        conn = self.get_connection()
        
        # Calculate date range based on period (align with _period_to_days)
        days = self._period_to_days(period)
        start_date = (datetime.now() - timedelta(days=days)).date()
        
        query = """
            SELECT date, open, high, low, close, volume
            FROM stock_daily
            WHERE symbol = ? AND date >= ?
              AND open IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
              AND open > 0
              AND high > 0
              AND low > 0
              AND close > 0
              AND high >= low
            ORDER BY date ASC
        """
        
        data = pd.read_sql_query(query, conn, params=(symbol, start_date))
        conn.close()
        
        if data.empty:
            return None

        # Convert to proper format and sanitize any legacy bad rows.
        data.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        data = self._sanitize_daily_frame(data)
        if data.empty:
            return None
        data["Date"] = pd.to_datetime(data["Date"])
        data.set_index("Date", inplace=True)
        return data
    
    # -------------------------------------------------------------------------
    # FUNDAMENTALS
    # -------------------------------------------------------------------------
    
    def get_fundamentals(
        self,
        symbol: str,
        force_refresh: bool = False,
        validate: bool = True,
        strict: bool = False,
        required_fields: Optional[List[str]] = None,
    ) -> Dict:
        """
        Get fundamental data for a symbol.
        
        Args:
            symbol: Stock symbol
            force_refresh: Force API refresh
            
        Returns:
            Dict with fundamental data
        """
        symbol = symbol.upper()
        
        # Check if we need refresh
        needs_refresh = force_refresh or self._needs_fundamentals_refresh(symbol)
        
        if not needs_refresh:
            # Load from database
            data = self._load_fundamentals_from_db(symbol)
            if data:
                return self._with_fundamentals_validation(
                    data,
                    validate=validate,
                    strict=strict,
                    required_fields=required_fields,
                )
        
        # Fetch from API
        data = self._fetch_fundamentals_from_api(symbol)
        if data:
            self._save_fundamentals_to_db(symbol, data)
        return self._with_fundamentals_validation(
            data or {},
            validate=validate,
            strict=strict,
            required_fields=required_fields,
        )

    def _with_fundamentals_validation(
        self,
        data: Dict,
        validate: bool = True,
        strict: bool = False,
        required_fields: Optional[List[str]] = None,
    ) -> Dict:
        out = dict(data or {})
        if not validate:
            return out
        validation = self.validate_fundamentals_payload(out, required_fields=required_fields)
        out["_validation"] = validation
        if strict and not validation["is_valid"]:
            return {}
        return out

    def validate_fundamentals_payload(
        self,
        data: Dict,
        required_fields: Optional[List[str]] = None,
    ) -> Dict:
        """
        Validate a fundamentals payload before serving it to downstream callers.
        """
        fields = required_fields or ["revenue_growth", "earnings_growth", "profit_margin"]

        def _missing(v) -> bool:
            return v is None or (isinstance(v, float) and np.isnan(v))

        missing_fields = [f for f in fields if _missing(data.get(f))]
        zero_fields = [f for f in fields if data.get(f) in (0, 0.0)]
        # If every required field is either missing or zero, treat payload as empty/unusable.
        is_empty_payload = len(set(missing_fields).union(zero_fields)) == len(fields)
        return {
            "is_valid": not is_empty_payload,
            "is_empty_payload": is_empty_payload,
            "missing_fields": missing_fields,
            "zero_fields": zero_fields,
            "required_fields": fields,
        }
    
    def _needs_fundamentals_refresh(self, symbol: str) -> bool:
        """Check if fundamentals need refresh."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT last_updated FROM stock_fundamentals WHERE symbol = ?
        """, (symbol,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result is None:
            return True
        
        last_updated = datetime.fromisoformat(result[0]) if result[0] else None
        if last_updated is None:
            return True
        
        return datetime.now() - last_updated > self.max_fundamental_age
    
    def _fetch_fundamentals_from_api(self, symbol: str) -> Optional[Dict]:
        """Fetch fundamentals from provider stack (FinancialDatasets/FMP/Yahoo)."""
        try:
            return self.fundamental_provider.get_company_profile(symbol)
            
        except Exception as e:
            print(f"   ⚠️ Fundamentals API error for {symbol}: {e}")
            return None

    def get_quarterly_fundamentals(self, symbol: str, force_refresh: bool = False) -> pd.DataFrame:
        """
        Return canonical quarterly fundamentals and persist to parquet.
        """
        symbol = symbol.upper()
        if not force_refresh:
            cached = self.parquet_store.load_quarterly_fundamentals(symbol)
            if cached is not None and not cached.empty:
                return validate_quarterly_fundamentals(cached)

        data = self.fundamental_provider.get_quarterly_fundamentals(symbol)
        if data is None or data.empty:
            return pd.DataFrame(columns=["ticker", "report_date", "disclosure_date", "eps", "revenue", "roe", "gross_margin"])

        validated = validate_quarterly_fundamentals(data)
        self.parquet_store.save_quarterly_fundamentals(symbol, validated)
        # Optional MySQL sink for historical fundamentals
        if self.mysql_store.enabled():
            self.mysql_store.write_quarterly_fundamentals(validated)
        return validated

    def get_analyst_estimates(self, symbol: str) -> pd.DataFrame:
        """Fetch analyst estimates from provider stack."""
        data = self.fundamental_provider.get_analyst_estimates(symbol.upper())
        if data is None:
            return pd.DataFrame(columns=["ticker", "report_date", "estimated_eps", "estimated_revenue", "source"])
        return data

    def get_earnings_calendar(self, symbol: str) -> pd.DataFrame:
        """Fetch earnings dates used for disclosure alignment checks."""
        data = self.fundamental_provider.get_earnings_calendar(symbol.upper())
        if data is None:
            return pd.DataFrame(columns=["ticker", "report_date", "disclosure_date", "source"])
        return data

    def build_aligned_daily_with_fundamentals(
        self,
        symbol: str,
        period: str = "2y",
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Create no-leakage daily dataset where quarterly fundamentals become visible
        only after disclosure_date.
        """
        daily = self.get_daily_data(symbol, period=period, force_refresh=force_refresh)
        if daily is None or daily.empty:
            return pd.DataFrame()
        price = daily.reset_index().rename(
            columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        price["adj_close"] = price["close"]
        fund = self.get_quarterly_fundamentals(symbol, force_refresh=force_refresh)
        return align_fundamentals_to_daily(price, fund, ticker=symbol.upper())
    
    def _save_fundamentals_to_db(self, symbol: str, data: Dict):
        """Save fundamentals to database."""
        with self._lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO stock_fundamentals 
                (symbol, name, sector, industry, market_cap, beta, pe_ratio,
                 forward_pe, dividend_yield, profit_margin, gross_margin,
                 revenue_growth, earnings_growth, roe, debt_to_equity,
                 fifty_two_week_high, fifty_two_week_low, avg_volume,
                 shares_outstanding, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('symbol'),
                data.get('name'),
                data.get('sector'),
                data.get('industry'),
                data.get('market_cap'),
                data.get('beta'),
                data.get('pe_ratio'),
                data.get('forward_pe'),
                data.get('dividend_yield'),
                data.get('profit_margin'),
                data.get('gross_margin'),
                data.get('revenue_growth'),
                data.get('earnings_growth'),
                data.get('roe'),
                data.get('debt_to_equity'),
                data.get('fifty_two_week_high'),
                data.get('fifty_two_week_low'),
                data.get('avg_volume'),
                data.get('shares_outstanding'),
                datetime.now().isoformat()
            ))
            
            # Update metadata
            cursor.execute("""
                INSERT OR REPLACE INTO data_metadata 
                (symbol, fundamentals_last_updated)
                VALUES (?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                fundamentals_last_updated = excluded.fundamentals_last_updated
            """, (symbol, datetime.now().isoformat()))
            
            conn.commit()
            conn.close()
    
    def _load_fundamentals_from_db(self, symbol: str) -> Optional[Dict]:
        """Load fundamentals from database."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM stock_fundamentals WHERE symbol = ?
        """, (symbol,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result is None:
            return None
        
        columns = ['symbol', 'name', 'sector', 'industry', 'market_cap', 'beta',
                   'pe_ratio', 'forward_pe', 'dividend_yield', 'profit_margin',
                   'gross_margin', 'revenue_growth', 'earnings_growth', 'roe',
                   'debt_to_equity', 'fifty_two_week_high', 'fifty_two_week_low',
                   'avg_volume', 'shares_outstanding', 'last_updated']
        
        return dict(zip(columns, result))
    
    # -------------------------------------------------------------------------
    # BULK OPERATIONS
    # -------------------------------------------------------------------------
    
    def preload_symbols(self, symbols: List[str], period: str = "1y") -> Dict[str, pd.DataFrame]:
        """
        Preload data for multiple symbols in parallel.
        
        Args:
            symbols: List of symbols
            period: Data period
            
        Returns:
            Dict mapping symbol to DataFrame
        """
        print(f"📥 Preloading {len(symbols)} symbols...")
        
        results = {}
        symbols_to_fetch = []
        
        # Check which symbols need refresh / coverage
        for symbol in symbols:
            symbol = symbol.upper()
            if self._needs_daily_refresh(symbol) or self._needs_daily_coverage(symbol, period):
                symbols_to_fetch.append(symbol)
            else:
                # Load from cache/db
                data = self.get_daily_data(symbol, period)
                if data is not None:
                    results[symbol] = data
        
        # Fetch remaining symbols in parallel
        if symbols_to_fetch:
            print(f"   Fetching {len(symbols_to_fetch)} from API...")
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {
                    executor.submit(self.get_daily_data, s, period, True): s 
                    for s in symbols_to_fetch
                }
                for future in as_completed(futures):
                    symbol = futures[future]
                    try:
                        data = future.result()
                        if data is not None:
                            results[symbol] = data
                    except Exception as e:
                        print(f"   ⚠️ Error preloading {symbol}: {e}")
        
        print(f"   ✅ Loaded {len(results)} symbols")
        return results

    def _batch_metadata_ok(self, symbols: List[str]) -> set:
        """Batch check which symbols have fresh metadata (one query)."""
        if not symbols:
            return set()
        td = self.max_daily_age
        if td.days >= 1:
            modifier = f"-{td.days} days"
        else:
            hours = td.seconds // 3600 or 1
            modifier = f"-{hours} hours"
        conn = self.get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(symbols))
        cursor.execute(
            f"""
            SELECT symbol FROM data_metadata
            WHERE symbol IN ({placeholders})
              AND daily_last_updated IS NOT NULL
              AND datetime(daily_last_updated) > datetime('now', ?)
            """,
            [str(s).upper() for s in symbols] + [modifier],
        )
        ok = {str(row[0]).upper() for row in cursor.fetchall()}
        conn.close()
        return ok

    def load_cached_prices(
        self,
        symbols: List[str],
        period: str = "1y",
        min_bars: int = 60,
        progress_hook: Optional[Callable[[int, int, int], None]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Load cached daily bars from the DB only (no API calls).

        We skip symbols that are stale or lack sufficient history so the caller can
        re-fetch them in a bulk API stage.
        """
        out: Dict[str, pd.DataFrame] = {}
        total = len(symbols)
        symbols_u = [str(s).upper() for s in symbols]
        metadata_ok = self._batch_metadata_ok(symbols_u)
        for idx, symbol_u in enumerate(symbols_u, 1):
            try:
                if symbol_u not in metadata_ok:
                    if progress_hook:
                        progress_hook(idx, total, len(out))
                    continue
                if self._needs_daily_coverage(symbol_u, period):
                    if progress_hook:
                        progress_hook(idx, total, len(out))
                    continue

                data = self._load_daily_from_db(symbol_u, period)
                if data is None or data.empty or len(data) < min_bars:
                    if progress_hook:
                        progress_hook(idx, total, len(out))
                    continue

                if isinstance(data.index, pd.DatetimeIndex):
                    frame = data.reset_index().rename(
                        columns={
                            data.index.name or "index": "Date",
                            "Open": "Open",
                            "High": "High",
                            "Low": "Low",
                            "Close": "Close",
                            "Volume": "Volume",
                        }
                    )
                else:
                    frame = data.copy()

                if "Date" in frame.columns:
                    frame["Date"] = pd.to_datetime(frame["Date"]).dt.date
                out[symbol_u] = frame
            except Exception:
                continue
            finally:
                if progress_hook:
                    progress_hook(idx, total, len(out))
        return out

    def persist_prices(
        self,
        prices: Dict[str, pd.DataFrame],
        progress_hook: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """
        Persist a symbol->daily bars mapping into the canonical SQLite cache.
        """
        total = len(prices)
        for idx, (symbol, data) in enumerate(prices.items(), 1):
            if data is None or data.empty:
                if progress_hook:
                    progress_hook(idx, total)
                continue
            cols = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in data.columns]
            if len(cols) < 6:
                if progress_hook:
                    progress_hook(idx, total)
                continue
            try:
                self._save_daily_to_db(str(symbol).upper(), data[cols])
            except Exception:
                pass
            if progress_hook:
                progress_hook(idx, total)
    
    def get_cache_stats(self) -> Dict:
        """Get database statistics."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Count records
        cursor.execute("SELECT COUNT(DISTINCT symbol) FROM stock_daily")
        daily_symbols = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM stock_daily")
        daily_records = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM stock_fundamentals")
        fundamental_records = cursor.fetchone()[0]
        
        # Database size
        cursor.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
        db_size = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'daily_symbols': daily_symbols,
            'daily_records': daily_records,
            'fundamental_records': fundamental_records,
            'database_size_mb': db_size / (1024 * 1024) if db_size else 0
        }

    def validate_quarterly_fundamentals_coverage(
        self,
        symbols: List[str],
        progress_hook: Optional[Callable[[int, int], None]] = None,
    ) -> Dict:
        """
        Validate cached quarterly fundamentals coverage from parquet store.

        Returns aggregate counters and per-field non-null counts for the latest row per symbol.
        """
        total = len(symbols)
        symbols_with_data = 0
        latest_rows = []

        for idx, symbol in enumerate(symbols, 1):
            sym = str(symbol).upper()
            try:
                q = self.parquet_store.load_quarterly_fundamentals(sym)
                if q is None or q.empty:
                    if progress_hook:
                        progress_hook(idx, total)
                    continue
                symbols_with_data += 1
                q_sorted = q.sort_values("report_date")
                latest_rows.append(q_sorted.iloc[-1].to_dict())
            except Exception:
                pass
            finally:
                if progress_hook:
                    progress_hook(idx, total)

        latest_df = pd.DataFrame(latest_rows)
        fields = ["eps", "revenue", "roe", "gross_margin"]
        non_null = {}
        for col in fields:
            if col in latest_df.columns:
                non_null[col] = int(pd.to_numeric(latest_df[col], errors="coerce").notna().sum())
            else:
                non_null[col] = 0

        return {
            "symbols_total": total,
            "symbols_with_quarterly_data": symbols_with_data,
            "latest_rows_count": len(latest_rows),
            "non_null_latest": non_null,
        }

    def purge_invalid_daily_rows(self, symbol: Optional[str] = None) -> int:
        """
        Delete invalid OHLCV rows from cache.

        Invalid means:
        - any OHLC field is NULL
        - any OHLC field is <= 0
        - high < low
        """
        cond = (
            "open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL "
            "OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR high < low"
        )
        params: Tuple = ()
        where = f"({cond})"
        if symbol:
            where = f"symbol = ? AND ({cond})"
            params = (symbol.upper(),)

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM stock_daily WHERE {where}", params)
        to_delete = int(cursor.fetchone()[0] or 0)
        if to_delete <= 0:
            conn.close()
            return 0

        cursor.execute(f"DELETE FROM stock_daily WHERE {where}", params)

        # Refresh metadata daily_last_date pointers.
        if symbol:
            sym = symbol.upper()
            cursor.execute(
                """
                UPDATE data_metadata
                SET daily_last_date = (
                    SELECT MAX(date) FROM stock_daily WHERE symbol = ?
                )
                WHERE symbol = ?
                """,
                (sym, sym),
            )
        else:
            cursor.execute(
                """
                UPDATE data_metadata
                SET daily_last_date = (
                    SELECT MAX(sd.date)
                    FROM stock_daily sd
                    WHERE sd.symbol = data_metadata.symbol
                )
                """
            )

        conn.commit()
        conn.close()

        if symbol:
            sym = symbol.upper()
            self._cache = {k: v for k, v in self._cache.items() if not k.startswith(f"{sym}_")}
        else:
            self._cache = {}

        return to_delete
    
    def clear_cache(self, symbol: Optional[str] = None):
        """Clear cache for a symbol or all symbols."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if symbol:
            symbol = symbol.upper()
            cursor.execute("DELETE FROM stock_daily WHERE symbol = ?", (symbol,))
            cursor.execute("DELETE FROM stock_fundamentals WHERE symbol = ?", (symbol,))
            cursor.execute("DELETE FROM data_metadata WHERE symbol = ?", (symbol,))
            print(f"🗑️ Cleared cache for {symbol}")
        else:
            cursor.execute("DELETE FROM stock_daily")
            cursor.execute("DELETE FROM stock_fundamentals")
            cursor.execute("DELETE FROM data_metadata")
            print("🗑️ Cleared all cache")
        
        conn.commit()
        conn.close()
        
        # Clear in-memory cache
        if symbol:
            self._cache = {k: v for k, v in self._cache.items() if not k.startswith(symbol)}
        else:
            self._cache = {}


# ============================================================================
# CLI
# ============================================================================

def main():
    """CLI for database management."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Stock Data Manager")
    parser.add_argument("--stats", action="store_true", help="Show cache statistics")
    parser.add_argument("--preload", nargs="+", help="Preload symbols")
    parser.add_argument("--clear", nargs="?", const="ALL", help="Clear cache (symbol or ALL)")
    parser.add_argument("--test", type=str, help="Test loading a symbol")
    parser.add_argument("--backfill-quarterly", action="store_true", help="Backfill quarterly fundamentals to parquet")
    parser.add_argument("--validate-quarterly", action="store_true", help="Validate quarterly fundamentals coverage")
    parser.add_argument(
        "--purge-invalid-daily",
        nargs="?",
        const="ALL",
        help="Delete invalid OHLCV rows (optionally pass a symbol, default ALL)",
    )
    parser.add_argument("--symbols-file", type=str, help="File with one ticker per line (optional)")
    parser.add_argument("--max-symbols", type=int, default=0, help="Max symbols to backfill (0 = all)")
    
    args = parser.parse_args()
    
    dm = DataManager()
    
    if args.stats:
        stats = dm.get_cache_stats()
        print(f"\n📊 Cache Statistics:")
        print(f"   Symbols with daily data: {stats['daily_symbols']}")
        print(f"   Total daily records: {stats['daily_records']:,}")
        print(f"   Fundamental records: {stats['fundamental_records']}")
        print(f"   Database size: {stats['database_size_mb']:.2f} MB")
        return

    if args.purge_invalid_daily:
        target = None if args.purge_invalid_daily == "ALL" else str(args.purge_invalid_daily).upper()
        removed = dm.purge_invalid_daily_rows(target)
        scope = "all symbols" if target is None else target
        print(f"🧹 Purged invalid daily rows for {scope}: {removed}")
        return

    if args.backfill_quarterly:
        if args.symbols_file:
            with open(args.symbols_file, "r", encoding="utf-8") as fh:
                symbols = [line.strip().split(",")[0].upper() for line in fh if line.strip()]
        else:
            from src.universe.listed_symbols import load_all_listed_us_symbols
            symbols = load_all_listed_us_symbols()
        if args.max_symbols > 0:
            symbols = symbols[: args.max_symbols]

        print(f"📦 Backfilling quarterly fundamentals for {len(symbols)} symbols")
        if dm.mysql_store.enabled():
            print("🧰 MySQL sink enabled via MYSQL_URL")
        else:
            print("🧰 MySQL sink disabled (set MYSQL_URL to enable)")
        for idx, sym in enumerate(symbols, 1):
            dm.get_quarterly_fundamentals(sym, force_refresh=True)
            if idx % 50 == 0 or idx == len(symbols):
                print(f"  {idx}/{len(symbols)}")
        return

    if args.validate_quarterly:
        if args.symbols_file:
            with open(args.symbols_file, "r", encoding="utf-8") as fh:
                symbols = [line.strip().split(",")[0].upper() for line in fh if line.strip()]
        else:
            from src.universe.listed_symbols import load_all_listed_us_symbols
            symbols = load_all_listed_us_symbols()
        if args.max_symbols > 0:
            symbols = symbols[: args.max_symbols]

        print(f"🔎 Validating quarterly fundamentals for {len(symbols)} symbols")
        stats = dm.validate_quarterly_fundamentals_coverage(
            symbols,
            progress_hook=lambda i, total: (
                print(f"  {i}/{total}") if (i % 250 == 0 or i == total) else None
            ),
        )
        print("\n=== QUARTERLY FUNDAMENTALS COVERAGE ===")
        print(f"Symbols total: {stats['symbols_total']}")
        print(f"Symbols with quarterly data: {stats['symbols_with_quarterly_data']}")
        print(f"Latest rows counted: {stats['latest_rows_count']}")
        nn = stats["non_null_latest"]
        print(f"latest eps non-null: {nn['eps']}/{stats['latest_rows_count']}")
        print(f"latest revenue non-null: {nn['revenue']}/{stats['latest_rows_count']}")
        print(f"latest roe non-null: {nn['roe']}/{stats['latest_rows_count']}")
        print(f"latest gross_margin non-null: {nn['gross_margin']}/{stats['latest_rows_count']}")
        return
    
    elif args.preload:
        dm.preload_symbols(args.preload)
    
    elif args.clear:
        if args.clear == "ALL":
            dm.clear_cache()
        else:
            dm.clear_cache(args.clear)
    
    elif args.test:
        print(f"\n🔍 Testing {args.test}...")
        
        # Test daily data
        import time
        start = time.time()
        data = dm.get_daily_data(args.test, "1y")
        elapsed = time.time() - start
        
        if data is not None:
            print(f"   ✅ Daily data: {len(data)} records ({elapsed:.2f}s)")
            print(f"   Latest: {data.index[-1]} @ ${data['Close'].iloc[-1]:.2f}")
        
        # Test fundamentals
        start = time.time()
        info = dm.get_fundamentals(args.test)
        elapsed = time.time() - start
        
        if info:
            print(f"   ✅ Fundamentals ({elapsed:.2f}s):")
            print(f"      Sector: {info.get('sector', 'N/A')}")
            print(f"      Market Cap: ${info.get('market_cap', 0)/1e9:.1f}B")
            print(f"      Beta: {info.get('beta', 'N/A')}")
        
        # Second load (should be from cache)
        start = time.time()
        data = dm.get_daily_data(args.test, "1y")
        elapsed = time.time() - start
        print(f"\n   ⚡ Cached load: {elapsed:.4f}s")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
