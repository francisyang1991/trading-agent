#!/usr/bin/env python3
"""
Universe Data Snapshot Tool

Downloads historical price data and fundamental information for all symbols
in the configured stock universe and stores them locally for backtesting.

Usage:
    # Download all data (daily bars, 2 years)
    python -m tools.snapshot_universe

    # Download with custom settings
    python -m tools.snapshot_universe --universe config/stock_universe_large.yaml --days 365

    # Download specific theme only
    python -m tools.snapshot_universe --theme sp500

    # Resume from failed download
    python -m tools.snapshot_universe --resume
"""

import argparse
import asyncio
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pandas as pd
import yaml
from loguru import logger

# Configure logging
logger.remove()
logger.add(
    "logs/snapshot_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="7 days",
    level="DEBUG"
)
logger.add(lambda msg: print(msg, end=""), level="INFO", colorize=True)


@dataclass
class SnapshotConfig:
    """Configuration for data snapshot."""
    universe_file: str = "config/stock_universe_large.yaml"
    output_dir: str = "data/snapshots"
    db_path: str = "data/stock_cache.db"
    
    # Data settings
    bar_size: str = "1 day"
    duration_days: int = 730  # 2 years
    
    # Rate limiting
    requests_per_second: float = 3.0
    batch_size: int = 50
    batch_delay: float = 5.0
    
    # Retry settings
    max_retries: int = 3
    retry_delay: float = 2.0


class UniverseDataSnapshot:
    """
    Downloads and stores historical data for stock universe.
    
    Stores data in:
    1. SQLite database for efficient querying
    2. Parquet files for fast DataFrame loading
    3. JSON metadata for tracking
    """
    
    def __init__(self, config: Optional[SnapshotConfig] = None):
        self.config = config or SnapshotConfig()
        self.ib_client = None
        self._use_yfinance = True  # Fallback to yfinance if IB unavailable
        
        # Create output directory
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        
        # Progress tracking
        self.completed_symbols: Set[str] = set()
        self.failed_symbols: Dict[str, str] = {}
        self.progress_file = Path(self.config.output_dir) / "progress.json"
        
        # Load existing progress
        self._load_progress()
    
    def _load_progress(self):
        """Load progress from previous run."""
        if self.progress_file.exists():
            try:
                with open(self.progress_file) as f:
                    data = json.load(f)
                    self.completed_symbols = set(data.get("completed", []))
                    self.failed_symbols = data.get("failed", {})
                    logger.info(f"Loaded progress: {len(self.completed_symbols)} completed, {len(self.failed_symbols)} failed")
            except Exception as e:
                logger.warning(f"Could not load progress: {e}")
    
    def _save_progress(self):
        """Save progress for resume capability."""
        try:
            with open(self.progress_file, "w") as f:
                json.dump({
                    "completed": list(self.completed_symbols),
                    "failed": self.failed_symbols,
                    "last_update": datetime.now().isoformat()
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save progress: {e}")
    
    def load_universe(self, theme: Optional[str] = None) -> List[str]:
        """
        Load symbols from universe file.
        
        Args:
            theme: Optional theme filter (sp500, qqq, russell2000, china_adr)
            
        Returns:
            List of unique symbols
        """
        try:
            with open(self.config.universe_file) as f:
                data = yaml.safe_load(f)
            
            if theme and theme in data.get("themes", {}):
                symbols = data["themes"][theme].get("symbols", [])
                logger.info(f"Loaded {len(symbols)} symbols from theme '{theme}'")
            else:
                # All unique symbols
                symbols = data.get("all_symbols", [])
                if not symbols:
                    # Combine from themes
                    all_symbols = set()
                    for theme_data in data.get("themes", {}).values():
                        all_symbols.update(theme_data.get("symbols", []))
                    symbols = sorted(all_symbols)
                logger.info(f"Loaded {len(symbols)} unique symbols from all themes")
            
            return symbols
            
        except Exception as e:
            logger.error(f"Failed to load universe: {e}")
            return []
    
    async def _init_ib_client(self) -> bool:
        """Initialize IB client if available."""
        # For bulk downloads, yfinance is more reliable (no rate limits)
        # IB Gateway is better for real-time data, not bulk historical
        logger.info("Using yfinance for bulk historical data download (more reliable)")
        self._use_yfinance = True
        return False
    
    async def download_historical_data(
        self,
        symbol: str,
        end_date: Optional[datetime] = None
    ) -> Optional[pd.DataFrame]:
        """
        Download historical OHLCV data for a symbol.
        
        Args:
            symbol: Stock symbol
            end_date: End date for data (defaults to today)
            
        Returns:
            DataFrame with OHLCV data or None if failed
        """
        if end_date is None:
            end_date = datetime.now()
        
        start_date = end_date - timedelta(days=self.config.duration_days)
        
        # Try IB first, then yfinance
        if self.ib_client and not self._use_yfinance:
            try:
                df = await self.ib_client.get_historical_data_range(
                    symbol=symbol,
                    bar_size=self.config.bar_size,
                    start_date=start_date,
                    end_date=end_date,
                    use_rth=True
                )
                
                if not df.empty:
                    return df
                    
            except Exception as e:
                logger.debug(f"IB data fetch failed for {symbol}: {e}")
        
        # Fallback to yfinance
        return await self._download_yfinance(symbol, start_date, end_date)
    
    async def _download_yfinance(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> Optional[pd.DataFrame]:
        """Download data using yfinance."""
        try:
            import yfinance as yf
            
            # Run in executor to not block
            loop = asyncio.get_event_loop()
            
            def fetch():
                ticker = yf.Ticker(symbol)
                df = ticker.history(
                    start=start_date.strftime("%Y-%m-%d"),
                    end=end_date.strftime("%Y-%m-%d"),
                    interval="1d"
                )
                return df
            
            df = await loop.run_in_executor(None, fetch)
            
            if df.empty:
                return None
            
            # Standardize columns
            df = df.reset_index()
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]
            
            # Rename to standard format
            column_map = {
                "date": "datetime",
                "adj_close": "adj_close"
            }
            df = df.rename(columns=column_map)
            
            if "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.set_index("datetime")
            
            # Keep only needed columns
            keep_cols = ["open", "high", "low", "close", "volume"]
            df = df[[c for c in keep_cols if c in df.columns]]
            
            return df
            
        except Exception as e:
            logger.debug(f"yfinance fetch failed for {symbol}: {e}")
            return None
    
    async def download_fundamentals(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Download fundamental data for a symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dict with fundamental data or None
        """
        try:
            import yfinance as yf
            
            loop = asyncio.get_event_loop()
            
            def fetch():
                ticker = yf.Ticker(symbol)
                info = ticker.info
                
                # Extract key fundamentals
                return {
                    "symbol": symbol,
                    "name": info.get("longName", info.get("shortName", "")),
                    "sector": info.get("sector", ""),
                    "industry": info.get("industry", ""),
                    "market_cap": info.get("marketCap", 0),
                    "pe_ratio": info.get("trailingPE"),
                    "forward_pe": info.get("forwardPE"),
                    "peg_ratio": info.get("pegRatio"),
                    "price_to_book": info.get("priceToBook"),
                    "dividend_yield": info.get("dividendYield"),
                    "beta": info.get("beta"),
                    "52_week_high": info.get("fiftyTwoWeekHigh"),
                    "52_week_low": info.get("fiftyTwoWeekLow"),
                    "avg_volume": info.get("averageVolume"),
                    "float_shares": info.get("floatShares"),
                    "short_ratio": info.get("shortRatio"),
                    "earnings_date": str(info.get("earningsDate", "")) if info.get("earningsDate") else None,
                    "updated_at": datetime.now().isoformat()
                }
            
            return await loop.run_in_executor(None, fetch)
            
        except Exception as e:
            logger.debug(f"Fundamentals fetch failed for {symbol}: {e}")
            return None
    
    def save_to_db(self, symbol: str, df: pd.DataFrame, fundamentals: Optional[Dict] = None):
        """
        Save data to SQLite database.
        
        Args:
            symbol: Stock symbol
            df: OHLCV DataFrame
            fundamentals: Optional fundamental data
        """
        try:
            conn = sqlite3.connect(self.config.db_path)
            
            # Save price data
            df_to_save = df.reset_index()
            df_to_save["symbol"] = symbol
            
            # Convert datetime to string for SQLite compatibility
            if "datetime" in df_to_save.columns:
                df_to_save["datetime"] = df_to_save["datetime"].astype(str)
            elif df_to_save.index.name == "datetime":
                df_to_save = df_to_save.reset_index()
                df_to_save["datetime"] = df_to_save["datetime"].astype(str)
            
            df_to_save.to_sql(
                "prices_snapshot",
                conn,
                if_exists="append",
                index=False
            )
            
            # Save fundamentals
            if fundamentals:
                fund_df = pd.DataFrame([fundamentals])
                fund_df.to_sql(
                    "fundamentals_snapshot",
                    conn,
                    if_exists="append",
                    index=False
                )
            
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to save {symbol} to database: {e}")
    
    def save_to_parquet(self, symbol: str, df: pd.DataFrame):
        """
        Save data to Parquet file for fast loading.
        
        Args:
            symbol: Stock symbol
            df: OHLCV DataFrame
        """
        try:
            output_path = Path(self.config.output_dir) / "prices" / f"{symbol}.parquet"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(output_path)
        except Exception as e:
            logger.error(f"Failed to save {symbol} to parquet: {e}")
    
    async def process_symbol(self, symbol: str) -> bool:
        """
        Process a single symbol - download and store data.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            True if successful
        """
        if symbol in self.completed_symbols:
            logger.debug(f"Skipping {symbol} (already completed)")
            return True
        
        for attempt in range(self.config.max_retries):
            try:
                # Download price data
                df = await self.download_historical_data(symbol)
                
                if df is None or df.empty:
                    raise ValueError(f"No data returned for {symbol}")
                
                # Download fundamentals
                fundamentals = await self.download_fundamentals(symbol)
                
                # Save to database and parquet
                self.save_to_db(symbol, df, fundamentals)
                self.save_to_parquet(symbol, df)
                
                self.completed_symbols.add(symbol)
                
                if symbol in self.failed_symbols:
                    del self.failed_symbols[symbol]
                
                logger.info(f"✓ {symbol}: {len(df)} bars")
                return True
                
            except Exception as e:
                if attempt < self.config.max_retries - 1:
                    logger.warning(f"Retry {attempt + 1}/{self.config.max_retries} for {symbol}: {e}")
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    self.failed_symbols[symbol] = str(e)
                    logger.error(f"✗ {symbol}: {e}")
                    return False
        
        return False
    
    async def run(
        self,
        symbols: Optional[List[str]] = None,
        theme: Optional[str] = None,
        resume: bool = True
    ):
        """
        Run the snapshot download process.
        
        Args:
            symbols: Optional list of symbols (loads from universe if None)
            theme: Optional theme filter
            resume: Skip already completed symbols
        """
        # Load symbols
        if symbols is None:
            symbols = self.load_universe(theme)
        
        if not symbols:
            logger.error("No symbols to process")
            return
        
        # Filter out completed if resuming
        if resume:
            pending_symbols = [s for s in symbols if s not in self.completed_symbols]
            logger.info(f"Resuming: {len(pending_symbols)} pending, {len(self.completed_symbols)} completed")
        else:
            pending_symbols = symbols
            self.completed_symbols.clear()
            self.failed_symbols.clear()
        
        if not pending_symbols:
            logger.info("All symbols already completed!")
            return
        
        # Initialize database tables
        self._init_database()
        
        # Try to initialize IB client
        await self._init_ib_client()
        
        # Process in batches
        total = len(pending_symbols)
        processed = 0
        start_time = datetime.now()
        
        logger.info(f"Starting download of {total} symbols...")
        
        for i in range(0, len(pending_symbols), self.config.batch_size):
            batch = pending_symbols[i:i + self.config.batch_size]
            
            for symbol in batch:
                success = await self.process_symbol(symbol)
                processed += 1
                
                # Rate limiting
                await asyncio.sleep(1.0 / self.config.requests_per_second)
                
                # Progress update every 10 symbols
                if processed % 10 == 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    rate = processed / elapsed if elapsed > 0 else 0
                    eta = (total - processed) / rate if rate > 0 else 0
                    logger.info(
                        f"Progress: {processed}/{total} ({100*processed/total:.1f}%) - "
                        f"Rate: {rate:.1f}/s - ETA: {eta/60:.1f} min"
                    )
            
            # Save progress after each batch
            self._save_progress()
            
            # Batch delay
            if i + self.config.batch_size < len(pending_symbols):
                logger.info(f"Batch complete, waiting {self.config.batch_delay}s...")
                await asyncio.sleep(self.config.batch_delay)
        
        # Final summary
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"\nSnapshot complete!\n"
            f"  Completed: {len(self.completed_symbols)}\n"
            f"  Failed: {len(self.failed_symbols)}\n"
            f"  Duration: {elapsed/60:.1f} minutes\n"
            f"  Output: {self.config.output_dir}"
        )
        
        # Save final progress
        self._save_progress()
        
        # Cleanup
        if self.ib_client:
            await self.ib_client.disconnect()
    
    def _init_database(self):
        """Initialize database tables."""
        conn = sqlite3.connect(self.config.db_path)
        cursor = conn.cursor()
        
        # Price snapshot table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prices_snapshot (
                symbol TEXT,
                datetime TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                PRIMARY KEY (symbol, datetime)
            )
        """)
        
        # Fundamentals snapshot table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
                symbol TEXT PRIMARY KEY,
                name TEXT,
                sector TEXT,
                industry TEXT,
                market_cap REAL,
                pe_ratio REAL,
                forward_pe REAL,
                peg_ratio REAL,
                price_to_book REAL,
                dividend_yield REAL,
                beta REAL,
                "52_week_high" REAL,
                "52_week_low" REAL,
                avg_volume REAL,
                float_shares REAL,
                short_ratio REAL,
                earnings_date TEXT,
                updated_at TEXT
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_symbol ON prices_snapshot(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_datetime ON prices_snapshot(datetime)")
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def load_symbol_data(
        symbol: str,
        db_path: str = "data/stock_cache.db",
        snapshot_dir: str = "data/snapshots"
    ) -> Optional[pd.DataFrame]:
        """
        Load cached data for a symbol.
        
        Args:
            symbol: Stock symbol
            db_path: Path to SQLite database
            snapshot_dir: Path to parquet snapshots
            
        Returns:
            DataFrame with OHLCV data or None
        """
        # Try parquet first (faster)
        parquet_path = Path(snapshot_dir) / "prices" / f"{symbol}.parquet"
        if parquet_path.exists():
            try:
                return pd.read_parquet(parquet_path)
            except Exception:
                pass
        
        # Fall back to SQLite
        try:
            conn = sqlite3.connect(db_path)
            df = pd.read_sql_query(
                "SELECT * FROM prices_snapshot WHERE symbol = ?",
                conn,
                params=(symbol,)
            )
            conn.close()
            
            if not df.empty:
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.set_index("datetime")
                return df.drop(columns=["symbol"], errors="ignore")
                
        except Exception:
            pass
        
        return None


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Download stock universe data snapshot")
    parser.add_argument(
        "--universe",
        default="config/stock_universe_large.yaml",
        help="Path to universe YAML file"
    )
    parser.add_argument(
        "--output",
        default="data/snapshots",
        help="Output directory for snapshots"
    )
    parser.add_argument(
        "--theme",
        choices=["sp500", "qqq", "russell2000", "china_adr"],
        help="Download only specific theme"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=730,
        help="Number of days of history (default: 730 = 2 years)"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Resume from previous run (default: True)"
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Start fresh, ignore previous progress"
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        help="Specific symbols to download"
    )
    
    args = parser.parse_args()
    
    config = SnapshotConfig(
        universe_file=args.universe,
        output_dir=args.output,
        duration_days=args.days
    )
    
    snapshot = UniverseDataSnapshot(config)
    
    asyncio.run(snapshot.run(
        symbols=args.symbols,
        theme=args.theme,
        resume=not args.fresh
    ))


if __name__ == "__main__":
    main()
