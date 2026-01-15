#!/usr/bin/env python3
"""
DATA MANAGER WITH LOCAL SQL CACHING
====================================
Provides a caching layer for stock data to avoid repeated API calls.

Features:
1. SQLite database for local storage
2. Automatic data freshness checking
3. Incremental updates (only fetch missing days)
4. Fundamental data caching (refreshed weekly)
5. Thread-safe operations

Tables:
- stock_daily: OHLCV data (Date, Open, High, Low, Close, Volume)
- stock_fundamentals: Sector, Industry, MarketCap, Beta, etc.
- data_metadata: Last update timestamps

Usage:
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
"""

import sqlite3
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import warnings
warnings.filterwarnings('ignore')


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
            revenue_growth REAL,
            earnings_growth REAL,
            fifty_two_week_high REAL,
            fifty_two_week_low REAL,
            avg_volume REAL,
            shares_outstanding REAL,
            last_updated TIMESTAMP
        )
    """)
    
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
    
    def __init__(self, max_daily_age_hours: int = 12, max_fundamental_age_days: int = 7):
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
        needs_refresh = force_refresh or self._needs_daily_refresh(symbol)
        
        if needs_refresh:
            # Fetch from API
            data = self._fetch_daily_from_api(symbol, period)
            if data is not None and not data.empty:
                # Save to database
                self._save_daily_to_db(symbol, data)
                # Update in-memory cache
                self._cache[cache_key] = (data.copy(), datetime.now())
                return data
        
        # Load from database
        data = self._load_daily_from_db(symbol, period)
        if data is not None and not data.empty:
            self._cache[cache_key] = (data.copy(), datetime.now())
            return data
        
        # Fallback: fetch from API
        data = self._fetch_daily_from_api(symbol, period)
        if data is not None and not data.empty:
            self._save_daily_to_db(symbol, data)
            self._cache[cache_key] = (data.copy(), datetime.now())
        
        return data
    
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
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=period)
            
            if data.empty:
                return None
            
            # Reset index to get Date as column
            data = data.reset_index()
            data.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'Dividends', 'Stock Splits']
            data['Date'] = pd.to_datetime(data['Date']).dt.date
            
            return data[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
            
        except Exception as e:
            print(f"   ⚠️ API error for {symbol}: {e}")
            return None
    
    def _save_daily_to_db(self, symbol: str, data: pd.DataFrame):
        """Save daily data to database."""
        with self._lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Insert or replace data
            for _, row in data.iterrows():
                cursor.execute("""
                    INSERT OR REPLACE INTO stock_daily 
                    (symbol, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    row['Date'],
                    row['Open'],
                    row['High'],
                    row['Low'],
                    row['Close'],
                    int(row['Volume']) if pd.notna(row['Volume']) else 0
                ))
            
            # Update metadata
            cursor.execute("""
                INSERT OR REPLACE INTO data_metadata 
                (symbol, daily_last_date, daily_last_updated)
                VALUES (?, ?, ?)
            """, (symbol, data['Date'].max(), datetime.now().isoformat()))
            
            conn.commit()
            conn.close()
    
    def _load_daily_from_db(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        """Load daily data from database."""
        conn = self.get_connection()
        
        # Calculate date range based on period
        days_map = {'1mo': 30, '3mo': 90, '6mo': 180, '1y': 365, '2y': 730, '5y': 1825}
        days = days_map.get(period, 365)
        start_date = (datetime.now() - timedelta(days=days)).date()
        
        query = """
            SELECT date, open, high, low, close, volume
            FROM stock_daily
            WHERE symbol = ? AND date >= ?
            ORDER BY date ASC
        """
        
        data = pd.read_sql_query(query, conn, params=(symbol, start_date))
        conn.close()
        
        if data.empty:
            return None
        
        # Convert to proper format
        data.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        data['Date'] = pd.to_datetime(data['Date'])
        data.set_index('Date', inplace=True)
        
        return data
    
    # -------------------------------------------------------------------------
    # FUNDAMENTALS
    # -------------------------------------------------------------------------
    
    def get_fundamentals(self, symbol: str, force_refresh: bool = False) -> Dict:
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
                return data
        
        # Fetch from API
        data = self._fetch_fundamentals_from_api(symbol)
        if data:
            self._save_fundamentals_to_db(symbol, data)
        
        return data or {}
    
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
        """Fetch fundamentals from Yahoo Finance API."""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            return {
                'symbol': symbol,
                'name': info.get('shortName', symbol),
                'sector': info.get('sector', 'Unknown'),
                'industry': info.get('industry', 'Unknown'),
                'market_cap': info.get('marketCap', 0),
                'beta': info.get('beta', 1.0),
                'pe_ratio': info.get('trailingPE', 0),
                'forward_pe': info.get('forwardPE', 0),
                'dividend_yield': info.get('dividendYield', 0),
                'profit_margin': info.get('profitMargins', 0),
                'revenue_growth': info.get('revenueGrowth', 0),
                'earnings_growth': info.get('earningsGrowth', 0),
                'fifty_two_week_high': info.get('fiftyTwoWeekHigh', 0),
                'fifty_two_week_low': info.get('fiftyTwoWeekLow', 0),
                'avg_volume': info.get('averageVolume', 0),
                'shares_outstanding': info.get('sharesOutstanding', 0)
            }
            
        except Exception as e:
            print(f"   ⚠️ Fundamentals API error for {symbol}: {e}")
            return None
    
    def _save_fundamentals_to_db(self, symbol: str, data: Dict):
        """Save fundamentals to database."""
        with self._lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO stock_fundamentals 
                (symbol, name, sector, industry, market_cap, beta, pe_ratio,
                 forward_pe, dividend_yield, profit_margin, revenue_growth,
                 earnings_growth, fifty_two_week_high, fifty_two_week_low,
                 avg_volume, shares_outstanding, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['symbol'],
                data['name'],
                data['sector'],
                data['industry'],
                data['market_cap'],
                data['beta'],
                data['pe_ratio'],
                data['forward_pe'],
                data['dividend_yield'],
                data['profit_margin'],
                data['revenue_growth'],
                data['earnings_growth'],
                data['fifty_two_week_high'],
                data['fifty_two_week_low'],
                data['avg_volume'],
                data['shares_outstanding'],
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
                   'revenue_growth', 'earnings_growth', 'fifty_two_week_high',
                   'fifty_two_week_low', 'avg_volume', 'shares_outstanding', 'last_updated']
        
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
        
        # Check which symbols need refresh
        for symbol in symbols:
            symbol = symbol.upper()
            if self._needs_daily_refresh(symbol):
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
    
    args = parser.parse_args()
    
    dm = DataManager()
    
    if args.stats:
        stats = dm.get_cache_stats()
        print(f"\n📊 Cache Statistics:")
        print(f"   Symbols with daily data: {stats['daily_symbols']}")
        print(f"   Total daily records: {stats['daily_records']:,}")
        print(f"   Fundamental records: {stats['fundamental_records']}")
        print(f"   Database size: {stats['database_size_mb']:.2f} MB")
    
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
