#!/usr/bin/env python3
"""
DEPRECATED: use SQLite cache via `src/data_manager.py`
=====================================================

This project now standardizes on `src/data_manager.py` (SQLite + WAL) for all caching:
- scanner
- backtests
- historical scans

This legacy CSV/parquet cache duplicating logic is intentionally removed to avoid “new wheels”.

Use instead:

```bash
python3 -m src.data_manager --stats
python3 -m src.data_manager --preload AAPL MSFT NVDA
```
"""

raise SystemExit(
    "tools/data_cache.py is deprecated. Use SQLite cache via `python3 -m src.data_manager` instead."
)
    
    def _load_metadata(self) -> Dict:
        """Load cache metadata."""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r') as f:
                return json.load(f)
        return {"symbols": {}}
    
    def _save_metadata(self):
        """Save cache metadata."""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2, default=str)
    
    def _get_cache_path(self, symbol: str) -> Path:
        """Get cache file path for a symbol."""
        ext = "parquet" if self.format == "parquet" else "csv"
        return self.cache_dir / f"{symbol.upper()}.{ext}"
    
    def _read_cache(self, symbol: str) -> Optional[pd.DataFrame]:
        """Read cached data for a symbol."""
        cache_path = self._get_cache_path(symbol)
        
        if not cache_path.exists():
            return None
        
        try:
            if self.format == "parquet":
                df = pd.read_parquet(cache_path)
            else:
                df = pd.read_csv(cache_path, index_col=0)
                # Parse dates manually to handle timezone issues
                df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
            
            # Ensure index is datetime (timezone-naive)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
            elif df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            
            return df
        except Exception as e:
            logger.error(f"Error reading cache for {symbol}: {e}")
            return None
    
    def _write_cache(self, symbol: str, data: pd.DataFrame):
        """Write data to cache."""
        cache_path = self._get_cache_path(symbol)
        
        try:
            if self.format == "parquet":
                data.to_parquet(cache_path)
            else:
                data.to_csv(cache_path)
            
            # Update metadata
            self.metadata["symbols"][symbol.upper()] = {
                "last_update": datetime.now().isoformat(),
                "start_date": str(data.index.min()),
                "end_date": str(data.index.max()),
                "rows": len(data)
            }
            self._save_metadata()
            
            logger.info(f"Cached {len(data)} rows for {symbol}")
        except Exception as e:
            logger.error(f"Error writing cache for {symbol}: {e}")
    
    def get_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        force_update: bool = False
    ) -> Optional[pd.DataFrame]:
        """
        Get stock data with automatic caching.
        
        Args:
            symbol: Stock symbol
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            force_update: Force download even if cached
            
        Returns:
            DataFrame with OHLCV data
        """
        symbol = symbol.upper()
        
        # Check if we need to download
        cached_data = self._read_cache(symbol)
        
        if cached_data is not None and not force_update:
            # Check if cached data covers requested range
            needs_update = False
            
            if start_date:
                start_dt = pd.to_datetime(start_date)
                if cached_data.index.min() > start_dt:
                    needs_update = True
            
            if end_date:
                end_dt = pd.to_datetime(end_date)
                if cached_data.index.max() < end_dt:
                    needs_update = True
            else:
                # Check if we need recent data
                today = datetime.now().date()
                last_cached = cached_data.index.max().date()
                # Update if cache is more than 1 trading day old
                if (today - last_cached).days > 3:  # Account for weekends
                    needs_update = True
            
            if not needs_update:
                logger.info(f"Using cached data for {symbol} ({len(cached_data)} rows)")
                
                # Filter by date range if specified
                if start_date:
                    cached_data = cached_data[cached_data.index >= start_date]
                if end_date:
                    cached_data = cached_data[cached_data.index <= end_date]
                
                return cached_data
        
        # Download new data
        return self.update_data(symbol, start_date, end_date)
    
    def update_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """
        Download and update cached data for a symbol.
        
        Args:
            symbol: Stock symbol
            start_date: Start date
            end_date: End date
            
        Returns:
            Updated DataFrame
        """
        symbol = symbol.upper()
        logger.info(f"Downloading data for {symbol}...")
        
        try:
            ticker = yf.Ticker(symbol)
            
            # Get existing cached data
            cached_data = self._read_cache(symbol)
            
            if cached_data is not None:
                # Incremental update: only download from last cached date
                last_date = cached_data.index.max()
                
                # Add buffer for potential data corrections
                download_start = (last_date - timedelta(days=5)).strftime('%Y-%m-%d')
                
                logger.info(f"Incremental update from {download_start}")
                new_data = ticker.history(start=download_start)
            else:
                # Full download
                if start_date:
                    new_data = ticker.history(start=start_date, end=end_date)
                else:
                    new_data = ticker.history(period=self.default_period)
            
            if new_data.empty:
                logger.warning(f"No data downloaded for {symbol}")
                return cached_data
            
            # Merge with existing data
            if cached_data is not None:
                # Combine and remove duplicates (keep latest)
                combined = pd.concat([cached_data, new_data])
                combined = combined[~combined.index.duplicated(keep='last')]
                combined = combined.sort_index()
                data = combined
            else:
                data = new_data
            
            # Save to cache
            self._write_cache(symbol, data)
            
            # Filter by requested range
            if start_date:
                data = data[data.index >= start_date]
            if end_date:
                data = data[data.index <= end_date]
            
            return data
            
        except Exception as e:
            logger.error(f"Error downloading {symbol}: {e}")
            return cached_data  # Return cached data if download fails
    
    def update_all(self, symbols: Optional[List[str]] = None):
        """
        Update all cached symbols or specified list.
        
        Args:
            symbols: List of symbols to update (default: all cached)
        """
        if symbols is None:
            symbols = list(self.metadata.get("symbols", {}).keys())
        
        if not symbols:
            logger.warning("No symbols to update")
            return
        
        logger.info(f"Updating {len(symbols)} symbols...")
        
        for symbol in symbols:
            self.update_data(symbol)
        
        logger.info("Update complete!")
    
    def get_cached_symbols(self) -> List[str]:
        """Get list of all cached symbols."""
        return list(self.metadata.get("symbols", {}).keys())
    
    def get_cache_info(self, symbol: Optional[str] = None) -> Dict:
        """
        Get cache information.
        
        Args:
            symbol: Specific symbol or None for all
            
        Returns:
            Cache metadata
        """
        if symbol:
            return self.metadata.get("symbols", {}).get(symbol.upper(), {})
        return self.metadata
    
    def clear_cache(self, symbol: Optional[str] = None):
        """
        Clear cache for a symbol or all symbols.
        
        Args:
            symbol: Specific symbol or None for all
        """
        if symbol:
            cache_path = self._get_cache_path(symbol)
            if cache_path.exists():
                cache_path.unlink()
                self.metadata["symbols"].pop(symbol.upper(), None)
                self._save_metadata()
                logger.info(f"Cleared cache for {symbol}")
        else:
            # Clear all
            for f in self.cache_dir.glob("*.parquet"):
                f.unlink()
            for f in self.cache_dir.glob("*.csv"):
                f.unlink()
            self.metadata = {"symbols": {}}
            self._save_metadata()
            logger.info("Cleared all cache")
    
    def preload_symbols(self, symbols: List[str]):
        """
        Preload multiple symbols into cache.
        
        Args:
            symbols: List of symbols to preload
        """
        logger.info(f"Preloading {len(symbols)} symbols...")
        
        for i, symbol in enumerate(symbols, 1):
            print(f"[{i}/{len(symbols)}] Loading {symbol}...", end=" ")
            data = self.get_data(symbol)
            if data is not None:
                print(f"✅ {len(data)} rows")
            else:
                print("❌ Failed")
        
        logger.info("Preload complete!")


def main():
    """Demo the cache system."""
    print("\n📦 Stock Data Cache Demo")
    print("=" * 50)
    
    cache = StockDataCache()
    
    # Demo: Load some stocks
    test_symbols = ["AAPL", "MSFT", "NVDA"]
    
    for symbol in test_symbols:
        print(f"\n📊 Loading {symbol}...")
        
        # First load (will download)
        data = cache.get_data(symbol)
        if data is not None:
            print(f"   Rows: {len(data)}")
            print(f"   Date range: {data.index.min().date()} to {data.index.max().date()}")
            print(f"   Latest close: ${data['Close'].iloc[-1]:.2f}")
        
        # Second load (should use cache)
        print(f"   Loading again (should use cache)...")
        data2 = cache.get_data(symbol)
        print(f"   ✅ Loaded from cache")
    
    # Show cache info
    print("\n📋 Cache Summary:")
    info = cache.get_cache_info()
    for sym, meta in info.get("symbols", {}).items():
        print(f"   {sym}: {meta['rows']} rows, last update: {meta['last_update'][:10]}")
    
    print("\n✅ Cache demo complete!")


if __name__ == "__main__":
    main()
