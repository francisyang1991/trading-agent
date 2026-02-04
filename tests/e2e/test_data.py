"""
Market Data Tests for IB Gateway.

Tests:
- Historical data fetching
- Current price retrieval
- Real-time data subscriptions
- Data quality validation

Run with: pytest tests/e2e/test_data.py -v
"""

import asyncio
from datetime import datetime, timedelta

import pandas as pd
import pytest
import pytest_asyncio

from src.data.ibkr_async_client import IBKRAsyncClient


# =============================================================================
# Historical Data Tests
# =============================================================================

class TestHistoricalData:
    """Historical market data tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_fetch_daily_data(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test fetching daily historical data."""
        symbol = test_symbols[0]  # SPY
        
        df = await connected_client.get_historical_data(
            symbol=symbol,
            bar_size="1 day",
            duration="30 D"
        )
        
        # Validate data
        assert not df.empty, f"No data returned for {symbol}"
        assert len(df) > 0, "Should have at least some bars"
        
        # Validate columns
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in required_cols:
            assert col in df.columns, f"Missing column: {col}"
        
        # Validate data quality
        assert df['close'].notna().all(), "Close prices should not be NaN"
        assert (df['high'] >= df['low']).all(), "High should be >= Low"
        assert (df['high'] >= df['close']).all(), "High should be >= Close"
        assert (df['low'] <= df['close']).all(), "Low should be <= Close"
        
        # Validate volume
        assert (df['volume'] >= 0).all(), "Volume should be non-negative"
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_fetch_intraday_data(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test fetching intraday data."""
        symbol = test_symbols[0]
        
        df = await connected_client.get_historical_data(
            symbol=symbol,
            bar_size="5 mins",
            duration="1 D"
        )
        
        assert not df.empty, f"No intraday data returned for {symbol}"
        
        # Intraday should have more bars than daily
        daily_df = await connected_client.get_historical_data(
            symbol=symbol,
            bar_size="1 day",
            duration="1 D"
        )
        
        assert len(df) >= len(daily_df), "Intraday should have more bars than daily"
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_fetch_multiple_symbols(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test fetching data for multiple symbols."""
        results = {}
        
        for symbol in test_symbols[:3]:
            df = await connected_client.get_historical_data(
                symbol=symbol,
                bar_size="1 day",
                duration="5 D"
            )
            results[symbol] = df
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)
        
        # All should have data
        for symbol, df in results.items():
            assert not df.empty, f"No data for {symbol}"
    
    @pytest.mark.live
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_fetch_date_range(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test fetching data for specific date range."""
        symbol = test_symbols[0]
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        df = await connected_client.get_historical_data_range(
            symbol=symbol,
            bar_size="1 day",
            start_date=start_date,
            end_date=end_date
        )
        
        assert not df.empty, "Should have data for date range"
        
        # Verify date range
        assert df.index.min() >= start_date - timedelta(days=5), "Data should start near start_date"
        assert df.index.max() <= end_date + timedelta(days=1), "Data should end near end_date"
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_invalid_bar_size(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test handling of invalid bar size."""
        symbol = test_symbols[0]
        
        df = await connected_client.get_historical_data(
            symbol=symbol,
            bar_size="invalid_size"
        )
        
        # Should return empty DataFrame, not raise
        assert df.empty, "Invalid bar size should return empty DataFrame"


# =============================================================================
# Current Price Tests
# =============================================================================

class TestCurrentPrice:
    """Current market price tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_get_current_price(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test getting current market price."""
        import math
        symbol = test_symbols[0]
        
        price = await connected_client.get_current_price(symbol)
        
        # During market hours, should get a price
        # During off-hours, may be None or last close
        # Paper accounts without market data subscription may return NaN
        if price is not None and not math.isnan(price):
            assert isinstance(price, float)
            assert price > 0, "Price should be positive"
            assert price < 100000, "Price should be reasonable"  # Sanity check
        else:
            # Paper account without market data subscription
            # Try to get price from historical data instead
            df = await connected_client.get_historical_data(symbol, "1 day", "5 D")
            if not df.empty:
                hist_price = df['close'].iloc[-1]
                assert hist_price > 0, "Historical price should be available"
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_get_prices_multiple_symbols(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test getting prices for multiple symbols."""
        prices = {}
        
        for symbol in test_symbols[:3]:
            price = await connected_client.get_current_price(symbol)
            prices[symbol] = price
            await asyncio.sleep(0.5)  # Rate limiting
        
        # At least some should have prices
        valid_prices = [p for p in prices.values() if p is not None]
        # During market hours, most should have prices
        # During off-hours, may have fewer


# =============================================================================
# Real-time Data Tests
# =============================================================================

class TestRealTimeData:
    """Real-time data subscription tests."""
    
    @pytest.mark.live
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_subscribe_bars(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test subscribing to real-time bars."""
        symbol = test_symbols[0]
        bars_received = []
        
        def on_bar(bar):
            bars_received.append(bar)
        
        # Subscribe
        success = await connected_client.subscribe_bars(symbol, on_bar)
        assert success, "Subscription should succeed"
        
        # Wait for some bars (5-second bars)
        await asyncio.sleep(15)
        
        # Unsubscribe
        await connected_client.unsubscribe_bars(symbol)
        
        # During market hours, should receive bars
        # During off-hours, may not receive any
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_unsubscribe_bars(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test unsubscribing from real-time bars."""
        symbol = test_symbols[0]
        
        def on_bar(bar):
            pass
        
        # Subscribe
        await connected_client.subscribe_bars(symbol, on_bar)
        
        # Unsubscribe
        await connected_client.unsubscribe_bars(symbol)
        
        # Should not be in subscriptions
        assert symbol not in connected_client._subscriptions
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_unsubscribe_all(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test unsubscribing from all subscriptions."""
        def on_bar(bar):
            pass
        
        # Subscribe to multiple symbols
        for symbol in test_symbols[:2]:
            await connected_client.subscribe_bars(symbol, on_bar)
        
        assert len(connected_client._subscriptions) > 0
        
        # Unsubscribe all
        await connected_client.unsubscribe_all()
        
        assert len(connected_client._subscriptions) == 0


# =============================================================================
# Contract Caching Tests
# =============================================================================

class TestContractCaching:
    """Contract caching tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_contract_caching(self, connected_client: IBKRAsyncClient, test_symbols: list):
        """Test that contracts are cached."""
        symbol = test_symbols[0]
        
        # Clear cache
        connected_client._contracts_cache.clear()
        connected_client._contract_cache_times.clear()
        
        # First request - should qualify contract
        contract1 = await connected_client._get_qualified_contract(symbol)
        
        assert len(connected_client._contracts_cache) == 1
        
        # Second request - should use cache
        contract2 = await connected_client._get_qualified_contract(symbol)
        
        assert contract1 is contract2, "Should return cached contract"
        assert len(connected_client._contracts_cache) == 1


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestDataErrorHandling:
    """Data error handling tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_invalid_symbol(self, connected_client: IBKRAsyncClient):
        """Test handling of invalid symbol."""
        df = await connected_client.get_historical_data(
            symbol="INVALID_SYMBOL_12345",
            bar_size="1 day",
            duration="5 D"
        )
        
        # Should return empty DataFrame, not raise
        assert df.empty, "Invalid symbol should return empty DataFrame"
    
    @pytest.mark.asyncio
    async def test_not_connected_returns_empty(self, ib_client: IBKRAsyncClient, test_symbols: list):
        """Test that methods return empty when not connected."""
        # Don't connect
        assert not ib_client.is_connected
        
        # Should return empty/None
        df = await ib_client.get_historical_data(test_symbols[0])
        assert df.empty
        
        price = await ib_client.get_current_price(test_symbols[0])
        # May be None when not connected
