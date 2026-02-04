"""
IBKR Async Client - Pure async client for IB Gateway using ib_async.

Features:
- Pure async - no sync wrappers or run_until_complete()
- Automatic retry with exponential backoff
- Contract caching to reduce API calls
- Request throttling to prevent rate limiting
- Auto-resubscribe on reconnection
- Proper cleanup on shutdown

For 24/7 cloud deployment reliability.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set
import pandas as pd
from loguru import logger

try:
    from ib_async import IB, Stock, Contract, Trade, BarData, Ticker
    from ib_async import MarketOrder, LimitOrder, StopOrder
    from ib_async.order import Order
    from ib_async.util import df
    IB_ASYNC_AVAILABLE = True
except ImportError:
    logger.warning("ib_async not installed. Install with: pip install ib_async")
    IB_ASYNC_AVAILABLE = False
    IB = Stock = Contract = Trade = BarData = Ticker = None
    MarketOrder = LimitOrder = StopOrder = Order = None

from .connection_manager import IBConnectionManager, ConnectionConfig, ConnectionState, TradingMode


@dataclass
class SubscriptionInfo:
    """Information about an active subscription."""
    symbol: str
    contract: Contract
    subscription: Any  # RealTimeBarList or similar
    callback: Callable
    created_at: datetime


class IBKRAsyncClient:
    """
    Pure async IBKR client for 24/7 operation.
    
    All methods are async - no sync wrappers.
    Uses IBConnectionManager for reliable connectivity.
    
    Usage:
        config = ConnectionConfig.from_env()
        client = IBKRAsyncClient(config)
        
        async with client:
            # Fetch historical data
            df = await client.get_historical_data("AAPL", "1 day", "30 D")
            
            # Place order
            trade = await client.place_market_order("AAPL", 10, "BUY")
            
            # Subscribe to real-time data
            await client.subscribe_bars("AAPL", my_callback)
    """
    
    # IBKR bar size mappings
    BAR_SIZES = {
        "1 min": "1 min",
        "5 mins": "5 mins", 
        "15 mins": "15 mins",
        "30 mins": "30 mins",
        "1 hour": "1 hour",
        "4 hours": "4 hours",
        "1 day": "1 day",
        "1 week": "1 week",
    }
    
    # Default duration for each bar size
    DURATION_MAP = {
        "1 min": "1 D",
        "5 mins": "5 D",
        "15 mins": "10 D",
        "30 mins": "20 D",
        "1 hour": "30 D",
        "4 hours": "60 D",
        "1 day": "365 D",
        "1 week": "2 Y",
    }
    
    def __init__(self, config: Optional[ConnectionConfig] = None):
        """
        Initialize async client.
        
        Args:
            config: Connection configuration (defaults to env vars)
        """
        if not IB_ASYNC_AVAILABLE:
            raise RuntimeError("ib_async not available. Install with: pip install ib_async")
        
        self.config = config or ConnectionConfig.from_env()
        self.conn_manager = IBConnectionManager(self.config)
        
        # Contract cache to reduce API calls
        self._contracts_cache: Dict[str, Contract] = {}
        self._contract_cache_ttl = timedelta(hours=24)
        self._contract_cache_times: Dict[str, datetime] = {}
        
        # Active subscriptions for auto-resubscribe
        self._subscriptions: Dict[str, SubscriptionInfo] = {}
        
        # Request throttling
        self._request_lock = asyncio.Lock()
        self._last_request_time: Optional[datetime] = None
        self._min_request_interval = 1.0 / self.config.requests_per_second
        
        # Register connection callbacks
        self.conn_manager.on_connected.append(self._on_connect)
        self.conn_manager.on_disconnected.append(self._on_disconnect)
    
    @property
    def ib(self) -> Optional[IB]:
        """Get underlying IB instance."""
        return self.conn_manager.ib
    
    @property
    def is_connected(self) -> bool:
        """Check if connected and ready."""
        return self.conn_manager.is_connected
    
    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self.conn_manager.state
    
    async def connect(self) -> bool:
        """
        Connect to IB Gateway.
        
        Returns:
            True if connection successful
        """
        return await self.conn_manager.connect()
    
    async def disconnect(self):
        """Disconnect and cleanup."""
        await self.conn_manager.shutdown()
    
    async def _on_connect(self):
        """Handle reconnection - restore subscriptions."""
        logger.info("Client connected, restoring subscriptions...")
        
        # Resubscribe to all active subscriptions
        for symbol, info in list(self._subscriptions.items()):
            try:
                await self._resubscribe(info)
                logger.info(f"Restored subscription for {symbol}")
            except Exception as e:
                logger.error(f"Failed to restore subscription for {symbol}: {e}")
    
    async def _on_disconnect(self, error: Optional[Exception]):
        """Handle disconnection."""
        logger.warning(f"Client disconnected: {error}")
    
    async def _resubscribe(self, info: SubscriptionInfo):
        """Resubscribe after reconnection."""
        # Cancel old subscription if exists
        if info.subscription:
            try:
                self.ib.cancelRealTimeBars(info.subscription)
            except Exception:
                pass
        
        # Create new subscription
        contract = await self._get_qualified_contract(info.symbol)
        bars = self.ib.reqRealTimeBars(
            contract=contract,
            barSize=5,
            whatToShow="TRADES",
            useRTH=True
        )
        bars.updateEvent += info.callback
        
        # Update subscription info
        info.contract = contract
        info.subscription = bars
    
    async def _throttle_request(self):
        """Throttle requests to prevent rate limiting."""
        async with self._request_lock:
            if self._last_request_time:
                elapsed = (datetime.now() - self._last_request_time).total_seconds()
                if elapsed < self._min_request_interval:
                    await asyncio.sleep(self._min_request_interval - elapsed)
            self._last_request_time = datetime.now()
    
    async def _retry_operation(
        self,
        operation: Callable,
        *args,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        **kwargs
    ):
        """
        Retry failed operations with exponential backoff.
        
        Args:
            operation: Async operation to retry
            *args: Positional arguments for operation
            max_retries: Maximum retry attempts
            retry_delay: Initial delay between retries
            **kwargs: Keyword arguments for operation
            
        Returns:
            Operation result
            
        Raises:
            Last exception if all retries fail
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                await self._throttle_request()
                return await operation(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = retry_delay * (2 ** attempt)
                    logger.warning(
                        f"Operation failed (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
        
        raise last_error
    
    async def _get_qualified_contract(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD"
    ) -> Contract:
        """
        Get a qualified contract, using cache when possible.
        
        Args:
            symbol: Stock symbol
            exchange: Exchange (SMART for best execution)
            currency: Trading currency
            
        Returns:
            Qualified Contract object
        """
        cache_key = f"{symbol}:{exchange}:{currency}"
        
        # Check cache
        if cache_key in self._contracts_cache:
            cache_time = self._contract_cache_times.get(cache_key)
            if cache_time and datetime.now() - cache_time < self._contract_cache_ttl:
                return self._contracts_cache[cache_key]
        
        # Create and qualify contract
        contract = Stock(symbol, exchange, currency)
        
        if self.ib:
            await self.ib.qualifyContractsAsync(contract)
        
        # Cache result
        self._contracts_cache[cache_key] = contract
        self._contract_cache_times[cache_key] = datetime.now()
        
        return contract
    
    # =========================================================================
    # Market Data
    # =========================================================================
    
    async def get_historical_data(
        self,
        symbol: str,
        bar_size: str = "1 day",
        duration: Optional[str] = None,
        end_datetime: Optional[datetime] = None,
        what_to_show: str = "TRADES",
        use_rth: bool = True
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data with automatic retry.
        
        Args:
            symbol: Stock symbol
            bar_size: Bar size (e.g., "5 mins", "1 hour", "1 day")
            duration: Duration string (e.g., "30 D", "1 Y")
            end_datetime: End date for data (None = now)
            what_to_show: Data type (TRADES, MIDPOINT, BID, ASK)
            use_rth: Regular trading hours only
            
        Returns:
            DataFrame with OHLCV data (empty if error)
        """
        if not self.is_connected:
            logger.error("Not connected to IB Gateway")
            return pd.DataFrame()
        
        if bar_size not in self.BAR_SIZES:
            logger.error(f"Invalid bar size: {bar_size}")
            return pd.DataFrame()
        
        if duration is None:
            duration = self.DURATION_MAP.get(bar_size, "30 D")
        
        try:
            return await self._retry_operation(
                self._fetch_historical_data,
                symbol, bar_size, duration, end_datetime, what_to_show, use_rth
            )
        except Exception as e:
            logger.error(f"Failed to fetch historical data for {symbol}: {e}")
            return pd.DataFrame()
    
    async def _fetch_historical_data(
        self,
        symbol: str,
        bar_size: str,
        duration: str,
        end_datetime: Optional[datetime],
        what_to_show: str,
        use_rth: bool
    ) -> pd.DataFrame:
        """Internal method to fetch historical data."""
        contract = await self._get_qualified_contract(symbol)
        
        bars = await self.ib.reqHistoricalDataAsync(
            contract=contract,
            endDateTime=end_datetime or "",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1
        )
        
        if not bars:
            logger.warning(f"No data returned for {symbol}")
            return pd.DataFrame()
        
        # Convert to DataFrame
        data = df(bars)
        data = self._clean_ohlcv_data(data)
        
        logger.debug(f"Fetched {len(data)} bars for {symbol} ({bar_size})")
        return data
    
    async def get_historical_data_range(
        self,
        symbol: str,
        bar_size: str,
        start_date: datetime,
        end_date: datetime,
        what_to_show: str = "TRADES",
        use_rth: bool = True
    ) -> pd.DataFrame:
        """
        Fetch historical data for a specific date range.
        Handles IBKR's duration limits by making multiple requests.
        
        Args:
            symbol: Stock symbol
            bar_size: Bar size
            start_date: Start date
            end_date: End date
            what_to_show: Data type
            use_rth: Regular trading hours only
            
        Returns:
            DataFrame with OHLCV data
        """
        all_data = []
        current_end = end_date
        
        # Maximum duration per request based on bar size
        max_days = {
            "1 min": 1,
            "5 mins": 5,
            "15 mins": 10,
            "30 mins": 20,
            "1 hour": 30,
            "4 hours": 60,
            "1 day": 365,
            "1 week": 730,
        }
        
        chunk_days = max_days.get(bar_size, 30)
        
        while current_end > start_date:
            chunk_start = max(start_date, current_end - timedelta(days=chunk_days))
            duration_days = (current_end - chunk_start).days + 1
            duration_str = f"{duration_days} D"
            
            data = await self.get_historical_data(
                symbol=symbol,
                bar_size=bar_size,
                duration=duration_str,
                end_datetime=current_end,
                what_to_show=what_to_show,
                use_rth=use_rth
            )
            
            if not data.empty:
                all_data.append(data)
            
            current_end = chunk_start - timedelta(days=1)
            
            # Small delay between requests
            await asyncio.sleep(0.5)
        
        if not all_data:
            return pd.DataFrame()
        
        # Combine and deduplicate
        combined = pd.concat(all_data, ignore_index=False)
        combined = combined[~combined.index.duplicated(keep='first')]
        combined = combined.sort_index()
        
        # Filter to exact date range
        combined = combined[(combined.index >= start_date) & (combined.index <= end_date)]
        
        return combined
    
    def _clean_ohlcv_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Clean and standardize OHLCV data."""
        if data.empty:
            return data
        
        # Rename columns to standard names
        column_map = {
            'date': 'datetime',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'volume': 'volume',
            'average': 'vwap',
            'barCount': 'bar_count'
        }
        
        data = data.rename(columns=column_map)
        
        # Set datetime as index
        if 'datetime' in data.columns:
            data['datetime'] = pd.to_datetime(data['datetime'])
            data = data.set_index('datetime')
        
        # Ensure numeric types
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors='coerce')
        
        # Drop rows with NaN in essential columns
        data = data.dropna(subset=['open', 'high', 'low', 'close'])
        
        return data
    
    async def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get current market price for a symbol.
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Current price or None if unavailable
        """
        if not self.is_connected:
            return None
        
        try:
            contract = await self._get_qualified_contract(symbol)
            
            # Request market data snapshot
            ticker = await asyncio.wait_for(
                self.ib.reqTickersAsync(contract),
                timeout=10.0
            )
            
            if ticker and len(ticker) > 0:
                t = ticker[0]
                # Try different price fields
                price = t.last or t.close or t.bid or t.ask
                return float(price) if price else None
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get price for {symbol}: {e}")
            return None
    
    # =========================================================================
    # Real-time Data Subscriptions
    # =========================================================================
    
    async def subscribe_bars(
        self,
        symbol: str,
        callback: Callable[[BarData], None]
    ) -> bool:
        """
        Subscribe to real-time 5-second bars with auto-resubscribe on reconnect.
        
        Args:
            symbol: Stock symbol
            callback: Function to call on each new bar
            
        Returns:
            True if subscription successful
        """
        if not self.is_connected:
            logger.error("Not connected to IB Gateway")
            return False
        
        try:
            contract = await self._get_qualified_contract(symbol)
            
            # IBKR only supports 5-second real-time bars
            bars = self.ib.reqRealTimeBars(
                contract=contract,
                barSize=5,
                whatToShow="TRADES",
                useRTH=True
            )
            
            bars.updateEvent += callback
            
            # Store for auto-resubscribe
            self._subscriptions[symbol] = SubscriptionInfo(
                symbol=symbol,
                contract=contract,
                subscription=bars,
                callback=callback,
                created_at=datetime.now()
            )
            
            logger.info(f"Subscribed to real-time bars for {symbol}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to subscribe to {symbol}: {e}")
            return False
    
    async def unsubscribe_bars(self, symbol: str):
        """Unsubscribe from real-time bars."""
        if symbol in self._subscriptions:
            info = self._subscriptions[symbol]
            
            if info.subscription and self.ib:
                try:
                    self.ib.cancelRealTimeBars(info.subscription)
                except Exception as e:
                    logger.error(f"Error canceling subscription for {symbol}: {e}")
            
            del self._subscriptions[symbol]
            logger.info(f"Unsubscribed from {symbol}")
    
    async def unsubscribe_all(self):
        """Unsubscribe from all real-time data."""
        for symbol in list(self._subscriptions.keys()):
            await self.unsubscribe_bars(symbol)
    
    # =========================================================================
    # Order Management
    # =========================================================================
    
    def _is_regular_trading_hours(self) -> bool:
        """
        Check if we're currently in regular trading hours (RTH).
        US Market: 9:30 AM - 4:00 PM ET
        
        Returns:
            True if within regular trading hours
        """
        from datetime import timezone
        import pytz
        
        try:
            et_tz = pytz.timezone('US/Eastern')
            now_et = datetime.now(et_tz)
            
            # Check weekday (Mon=0, Fri=4)
            if now_et.weekday() > 4:
                return False
            
            # Check time (9:30 AM - 4:00 PM ET)
            market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
            
            return market_open <= now_et <= market_close
            
        except Exception as e:
            logger.warning(f"Could not determine trading hours: {e}")
            return True  # Assume RTH if can't determine
    
    def _is_extended_hours(self) -> bool:
        """
        Check if we're in extended hours (pre-market or after-hours).
        Pre-market: 4:00 AM - 9:30 AM ET
        After-hours: 4:00 PM - 8:00 PM ET
        
        Returns:
            True if within extended hours
        """
        from datetime import timezone
        import pytz
        
        try:
            et_tz = pytz.timezone('US/Eastern')
            now_et = datetime.now(et_tz)
            
            # Check weekday (Mon=0, Fri=4)
            if now_et.weekday() > 4:
                return False
            
            # Pre-market: 4:00 AM - 9:30 AM
            premarket_start = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
            premarket_end = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
            
            # After-hours: 4:00 PM - 8:00 PM
            afterhours_start = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
            afterhours_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)
            
            return (premarket_start <= now_et < premarket_end or 
                    afterhours_start < now_et <= afterhours_end)
            
        except Exception as e:
            logger.warning(f"Could not determine trading hours: {e}")
            return False
    
    async def place_market_order(
        self,
        symbol: str,
        quantity: int,
        action: str = "BUY",
        use_adaptive: bool = True
    ) -> Optional[Trade]:
        """
        Place a market order. During extended hours, automatically uses
        limit order at current price since market orders are not allowed.
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            action: BUY or SELL
            use_adaptive: Use adaptive order for better fills (ignored in extended hours)
            
        Returns:
            Trade object or None if failed
        """
        if not self.is_connected:
            logger.error("Not connected to IB Gateway")
            return None
        
        if self.config.readonly:
            logger.error("Client is in readonly mode")
            return None
        
        try:
            contract = await self._get_qualified_contract(symbol)
            
            # During extended hours, market orders are not allowed
            # Use limit order at current price instead
            if not self._is_regular_trading_hours():
                logger.info(f"Extended hours - using limit order for {symbol}")
                return await self._place_extended_hours_order(
                    contract=contract,
                    quantity=quantity,
                    action=action
                )
            
            # Regular hours - use market order
            order = MarketOrder(action, quantity)
            trade = self.ib.placeOrder(contract, order)
            
            logger.info(f"Placed market {action} order: {symbol} x {quantity}")
            return trade
            
        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            return None
    
    async def _place_extended_hours_order(
        self,
        contract: Contract,
        quantity: int,
        action: str
    ) -> Optional[Trade]:
        """
        Place order during extended hours using limit order at current price.
        
        Extended hours require:
        1. Limit orders (no market orders)
        2. outsideRth=True flag
        3. Reasonable limit price
        """
        try:
            # Get current bid/ask to set limit price
            ticker = await asyncio.wait_for(
                self.ib.reqTickersAsync(contract),
                timeout=10.0
            )
            
            if not ticker:
                logger.error("Could not get ticker data for extended hours order")
                return None
            
            t = ticker[0]
            
            # Set limit price based on action
            if action.upper() == "BUY":
                # Buy at ask price (or slightly above)
                limit_price = t.ask or t.last or t.close
                if limit_price:
                    limit_price = round(limit_price * 1.001, 2)  # 0.1% above ask
            else:
                # Sell at bid price (or slightly below)
                limit_price = t.bid or t.last or t.close
                if limit_price:
                    limit_price = round(limit_price * 0.999, 2)  # 0.1% below bid
            
            if not limit_price or limit_price <= 0:
                logger.error(f"Could not determine limit price for {contract.symbol}")
                return None
            
            # Create limit order with extended hours flag
            order = LimitOrder(action, quantity, limit_price)
            order.outsideRth = True  # Allow execution outside regular hours
            order.tif = 'DAY'  # Good for day only
            
            trade = self.ib.placeOrder(contract, order)
            
            logger.info(
                f"Placed extended hours limit {action} order: "
                f"{contract.symbol} x {quantity} @ ${limit_price}"
            )
            return trade
            
        except Exception as e:
            logger.error(f"Failed to place extended hours order: {e}")
            return None
    
    async def place_limit_order(
        self,
        symbol: str,
        quantity: int,
        limit_price: float,
        action: str = "BUY"
    ) -> Optional[Trade]:
        """
        Place a limit order.
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            limit_price: Limit price
            action: BUY or SELL
            
        Returns:
            Trade object or None if failed
        """
        if not self.is_connected:
            logger.error("Not connected to IB Gateway")
            return None
        
        if self.config.readonly:
            logger.error("Client is in readonly mode")
            return None
        
        try:
            contract = await self._get_qualified_contract(symbol)
            order = LimitOrder(action, quantity, limit_price)
            trade = self.ib.placeOrder(contract, order)
            
            logger.info(f"Placed limit {action} order: {symbol} x {quantity} @ {limit_price}")
            return trade
            
        except Exception as e:
            logger.error(f"Failed to place limit order: {e}")
            return None
    
    async def place_stop_order(
        self,
        symbol: str,
        quantity: int,
        stop_price: float,
        action: str = "SELL"
    ) -> Optional[Trade]:
        """
        Place a stop order.
        
        Args:
            symbol: Stock symbol
            quantity: Number of shares
            stop_price: Stop trigger price
            action: BUY or SELL
            
        Returns:
            Trade object or None if failed
        """
        if not self.is_connected:
            logger.error("Not connected to IB Gateway")
            return None
        
        if self.config.readonly:
            logger.error("Client is in readonly mode")
            return None
        
        try:
            contract = await self._get_qualified_contract(symbol)
            order = StopOrder(action, quantity, stop_price)
            trade = self.ib.placeOrder(contract, order)
            
            logger.info(f"Placed stop {action} order: {symbol} x {quantity} @ {stop_price}")
            return trade
            
        except Exception as e:
            logger.error(f"Failed to place stop order: {e}")
            return None
    
    async def cancel_order(self, trade: Trade) -> bool:
        """
        Cancel an open order.
        
        Args:
            trade: Trade object to cancel
            
        Returns:
            True if cancellation request sent
        """
        if not self.is_connected:
            return False
        
        try:
            self.ib.cancelOrder(trade.order)
            logger.info(f"Cancelled order: {trade.order.orderId}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return False
    
    async def get_open_orders(self) -> List[Trade]:
        """Get all open orders."""
        if not self.is_connected:
            return []
        
        try:
            await self.ib.reqOpenOrdersAsync()
            return self.ib.openTrades()
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            return []
    
    # =========================================================================
    # Account & Positions
    # =========================================================================
    
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get all current positions."""
        if not self.is_connected:
            return []
        
        try:
            await self.ib.reqPositionsAsync()
            
            positions = []
            for pos in self.ib.positions():
                positions.append({
                    "symbol": pos.contract.symbol,
                    "quantity": pos.position,
                    "avg_cost": pos.avgCost,
                    "market_value": pos.position * pos.avgCost
                })
            
            return positions
            
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []
    
    async def get_account_summary(self) -> Dict[str, Any]:
        """Get account summary."""
        if not self.is_connected:
            return {}
        
        try:
            await self.ib.reqAccountSummaryAsync()
            
            summary = {}
            for item in self.ib.accountSummary():
                summary[item.tag] = {
                    "value": item.value,
                    "currency": item.currency
                }
            
            return summary
            
        except Exception as e:
            logger.error(f"Failed to get account summary: {e}")
            return {}
    
    async def get_account_value(self, tag: str = "NetLiquidation") -> Optional[float]:
        """
        Get specific account value.
        
        Args:
            tag: Account value tag (NetLiquidation, TotalCashValue, etc.)
            
        Returns:
            Value as float or None
        """
        summary = await self.get_account_summary()
        
        if tag in summary:
            try:
                return float(summary[tag]["value"])
            except (ValueError, TypeError):
                return None
        
        return None
    
    # =========================================================================
    # Utilities
    # =========================================================================
    
    async def sleep(self, seconds: float):
        """Async sleep that keeps IB connection alive."""
        if self.ib:
            await self.ib.sleepAsync(seconds)
        else:
            await asyncio.sleep(seconds)
    
    def get_status(self) -> Dict[str, Any]:
        """Get client status for monitoring."""
        return {
            "connection": self.conn_manager.get_status(),
            "subscriptions": list(self._subscriptions.keys()),
            "cached_contracts": len(self._contracts_cache),
        }
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.unsubscribe_all()
        await self.disconnect()
