from __future__ import annotations

"""
IBKR API Client Wrapper using ib_async
Handles connection management, data requests, and order execution.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable
import pandas as pd
from loguru import logger

try:
    from ib_async import IB, Stock, Contract, Order, Trade, BarData
    from ib_async import MarketOrder, LimitOrder, StopOrder
    from ib_async.util import df
    IB_ASYNC_AVAILABLE = True
except ImportError:
    pass  # Optional: ib_async only needed for live IBKR trading
    IB_ASYNC_AVAILABLE = False
    IB = None
    # Provide safe fallbacks so type hints don't raise at import time
    Stock = Contract = Order = Trade = BarData = None
    MarketOrder = LimitOrder = StopOrder = None
    df = None


class IBKRClient:
    """
    Interactive Brokers API client wrapper using ib_async.
    Provides clean interface for data fetching and order management.
    
    Uses async/await pattern for non-blocking operations.
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
    
    # Duration strings for historical data
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
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        readonly: bool = False,
        timeout: int = 30
    ):
        """
        Initialize IBKR client.
        
        Args:
            host: TWS/Gateway host address
            port: TWS/Gateway port (7497=TWS Paper, 7496=TWS Live, 4001/4002=Gateway)
            client_id: Unique client identifier
            readonly: If True, only read operations allowed
            timeout: Connection timeout in seconds
        """
        self.host = host
        self.port = port
        self.client_id = client_id
        self.readonly = readonly
        self.timeout = timeout
        
        self._ib: Optional[IB] = None
        self._connected = False
        self._subscriptions: Dict[str, Any] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
    @property
    def is_connected(self) -> bool:
        """Check if connected to IBKR."""
        return self._ib is not None and self._ib.isConnected()
    
    def _get_or_create_event_loop(self) -> asyncio.AbstractEventLoop:
        """Get existing event loop or create a new one."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    
    def connect(self) -> bool:
        """
        Establish connection to TWS/Gateway.
        
        Returns:
            True if connection successful
        """
        if not IB_ASYNC_AVAILABLE:
            logger.error("ib_async not available. Install with: pip install ib_async")
            return False
            
        if self.is_connected:
            logger.info("Already connected to IBKR")
            return True
        
        self._loop = self._get_or_create_event_loop()
        return self._loop.run_until_complete(self._connect_async())
    
    async def _connect_async(self) -> bool:
        """Async connection to IBKR."""
        try:
            self._ib = IB()
            await self._ib.connectAsync(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                readonly=self.readonly,
                timeout=self.timeout
            )
            self._connected = True
            logger.info(f"Connected to IBKR at {self.host}:{self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to IBKR: {e}")
            self._ib = None
            self._connected = False
            return False
    
    async def connect_async(self) -> bool:
        """
        Async connection method for use in async contexts.
        
        Returns:
            True if connection successful
        """
        return await self._connect_async()
    
    def disconnect(self):
        """Disconnect from TWS/Gateway."""
        if self._ib:
            self._ib.disconnect()
            self._ib = None
            self._connected = False
            logger.info("Disconnected from IBKR")
    
    def create_stock_contract(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD"
    ) -> Contract:
        """
        Create a stock contract.
        
        Args:
            symbol: Stock ticker symbol
            exchange: Exchange (SMART for best execution)
            currency: Trading currency
            
        Returns:
            IBKR Contract object
        """
        return Stock(symbol, exchange, currency)
    
    def get_historical_data(
        self,
        symbol: str,
        bar_size: str = "1 day",
        duration: Optional[str] = None,
        end_datetime: Optional[datetime] = None,
        what_to_show: str = "TRADES",
        use_rth: bool = True
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data (sync wrapper).
        
        Args:
            symbol: Stock ticker symbol
            bar_size: Bar size (e.g., "5 mins", "1 hour", "1 day")
            duration: Duration string (e.g., "30 D", "1 Y")
            end_datetime: End date for data (None = now)
            what_to_show: Data type (TRADES, MIDPOINT, BID, ASK)
            use_rth: Regular trading hours only
            
        Returns:
            DataFrame with OHLCV data
        """
        if not self._loop:
            self._loop = self._get_or_create_event_loop()
        
        return self._loop.run_until_complete(
            self.get_historical_data_async(
                symbol, bar_size, duration, end_datetime, what_to_show, use_rth
            )
        )
    
    async def get_historical_data_async(
        self,
        symbol: str,
        bar_size: str = "1 day",
        duration: Optional[str] = None,
        end_datetime: Optional[datetime] = None,
        what_to_show: str = "TRADES",
        use_rth: bool = True
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV data (async).
        
        Args:
            symbol: Stock ticker symbol
            bar_size: Bar size (e.g., "5 mins", "1 hour", "1 day")
            duration: Duration string (e.g., "30 D", "1 Y")
            end_datetime: End date for data (None = now)
            what_to_show: Data type (TRADES, MIDPOINT, BID, ASK)
            use_rth: Regular trading hours only
            
        Returns:
            DataFrame with OHLCV data
        """
        if not self.is_connected:
            logger.error("Not connected to IBKR")
            return pd.DataFrame()
        
        # Validate bar size
        if bar_size not in self.BAR_SIZES:
            logger.error(f"Invalid bar size: {bar_size}")
            return pd.DataFrame()
        
        # Use default duration if not specified
        if duration is None:
            duration = self.DURATION_MAP.get(bar_size, "30 D")
        
        # Create contract
        contract = self.create_stock_contract(symbol)
        
        try:
            # Qualify contract first
            await self._ib.qualifyContractsAsync(contract)
            
            # Request historical data
            bars = await self._ib.reqHistoricalDataAsync(
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
            
            logger.info(f"Fetched {len(data)} bars for {symbol} ({bar_size})")
            return data
            
        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return pd.DataFrame()
    
    async def get_historical_data_range_async(
        self,
        symbol: str,
        bar_size: str,
        start_date: datetime,
        end_date: datetime,
        what_to_show: str = "TRADES",
        use_rth: bool = True
    ) -> pd.DataFrame:
        """
        Fetch historical data for a specific date range (async).
        Handles IBKR's duration limits by making multiple requests.
        
        Args:
            symbol: Stock ticker symbol
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
            
            data = await self.get_historical_data_async(
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
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)
        
        if not all_data:
            return pd.DataFrame()
        
        # Combine and sort
        combined = pd.concat(all_data, ignore_index=False)
        combined = combined[~combined.index.duplicated(keep='first')]
        combined = combined.sort_index()
        
        # Filter to exact date range
        combined = combined[(combined.index >= start_date) & (combined.index <= end_date)]
        
        return combined
    
    def get_historical_data_range(
        self,
        symbol: str,
        bar_size: str,
        start_date: datetime,
        end_date: datetime,
        what_to_show: str = "TRADES",
        use_rth: bool = True
    ) -> pd.DataFrame:
        """Sync wrapper for get_historical_data_range_async."""
        if not self._loop:
            self._loop = self._get_or_create_event_loop()
        
        return self._loop.run_until_complete(
            self.get_historical_data_range_async(
                symbol, bar_size, start_date, end_date, what_to_show, use_rth
            )
        )
    
    def _clean_ohlcv_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Clean and standardize OHLCV data.
        
        Args:
            data: Raw DataFrame from IBKR
            
        Returns:
            Cleaned DataFrame
        """
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
        
        # Drop any rows with NaN in essential columns
        data = data.dropna(subset=['open', 'high', 'low', 'close'])
        
        return data
    
    def subscribe_realtime(
        self,
        symbol: str,
        callback: Callable[[BarData], None],
        bar_size: str = "5 secs"
    ) -> bool:
        """
        Subscribe to real-time bar updates (sync wrapper).
        
        Args:
            symbol: Stock ticker symbol
            callback: Function to call on new bar
            bar_size: Bar size for updates
            
        Returns:
            True if subscription successful
        """
        if not self._loop:
            self._loop = self._get_or_create_event_loop()
        
        return self._loop.run_until_complete(
            self.subscribe_realtime_async(symbol, callback, bar_size)
        )
    
    async def subscribe_realtime_async(
        self,
        symbol: str,
        callback: Callable[[BarData], None],
        bar_size: str = "5 secs"
    ) -> bool:
        """
        Subscribe to real-time bar updates (async).
        
        Args:
            symbol: Stock ticker symbol
            callback: Function to call on new bar
            bar_size: Bar size for updates
            
        Returns:
            True if subscription successful
        """
        if not self.is_connected:
            logger.error("Not connected to IBKR")
            return False
        
        contract = self.create_stock_contract(symbol)
        
        try:
            await self._ib.qualifyContractsAsync(contract)
            
            bars = self._ib.reqRealTimeBars(
                contract=contract,
                barSize=5,  # IBKR only supports 5-second real-time bars
                whatToShow="TRADES",
                useRTH=True
            )
            
            bars.updateEvent += callback
            self._subscriptions[symbol] = bars
            
            logger.info(f"Subscribed to real-time data for {symbol}")
            return True
            
        except Exception as e:
            logger.error(f"Error subscribing to {symbol}: {e}")
            return False
    
    def unsubscribe_realtime(self, symbol: str):
        """Unsubscribe from real-time updates."""
        if symbol in self._subscriptions:
            self._ib.cancelRealTimeBars(self._subscriptions[symbol])
            del self._subscriptions[symbol]
            logger.info(f"Unsubscribed from {symbol}")
    
    # =========================================================================
    # Fundamental Data
    # =========================================================================

    def get_fundamental_data(
        self,
        symbol: str,
        report_type: str = "ReportsFinSummary",
    ) -> Optional[str]:
        """
        Fetch fundamental data (sync wrapper).

        Args:
            symbol: Stock ticker symbol
            report_type: One of:
                - ReportsFinSummary  (key ratios: ROE, margins, D/E)
                - ReportsFinStatements  (quarterly income/balance/cashflow)
                - RESC  (analyst estimates)
                - CalendarReport  (earnings calendar)

        Returns:
            Raw XML string, or None on failure.

        Note:
            Requires "Reuters Global Fundamentals" subscription on your IBKR account.
        """
        if not self._loop:
            self._loop = self._get_or_create_event_loop()
        return self._loop.run_until_complete(
            self.get_fundamental_data_async(symbol, report_type)
        )

    async def get_fundamental_data_async(
        self,
        symbol: str,
        report_type: str = "ReportsFinSummary",
    ) -> Optional[str]:
        """Fetch fundamental data (async)."""
        if not self.is_connected:
            logger.error("Not connected to IBKR")
            return None

        contract = self.create_stock_contract(symbol)

        try:
            await self._ib.qualifyContractsAsync(contract)
            xml = await self._ib.reqFundamentalDataAsync(
                contract, reportType=report_type
            )
            if not xml:
                logger.debug(f"No fundamental data for {symbol} ({report_type})")
                return None
            return xml
        except Exception as e:
            logger.error(f"Error fetching fundamental data for {symbol}: {e}")
            return None

    # =========================================================================
    # Order Management
    # =========================================================================
    
    def place_market_order(
        self,
        symbol: str,
        quantity: int,
        action: str = "BUY"
    ) -> Optional[Trade]:
        """
        Place a market order (sync wrapper).
        
        Args:
            symbol: Stock ticker symbol
            quantity: Number of shares
            action: BUY or SELL
            
        Returns:
            Trade object or None if failed
        """
        if not self._loop:
            self._loop = self._get_or_create_event_loop()
        
        return self._loop.run_until_complete(
            self.place_market_order_async(symbol, quantity, action)
        )
    
    async def place_market_order_async(
        self,
        symbol: str,
        quantity: int,
        action: str = "BUY"
    ) -> Optional[Trade]:
        """
        Place a market order (async).
        
        Args:
            symbol: Stock ticker symbol
            quantity: Number of shares
            action: BUY or SELL
            
        Returns:
            Trade object or None if failed
        """
        if not self.is_connected:
            logger.error("Not connected to IBKR")
            return None
        
        if self.readonly:
            logger.error("Client is in readonly mode")
            return None
        
        contract = self.create_stock_contract(symbol)
        
        try:
            await self._ib.qualifyContractsAsync(contract)
            order = MarketOrder(action, quantity)
            trade = self._ib.placeOrder(contract, order)
            logger.info(f"Placed market {action} order: {symbol} x {quantity}")
            return trade
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
    
    def place_limit_order(
        self,
        symbol: str,
        quantity: int,
        limit_price: float,
        action: str = "BUY"
    ) -> Optional[Trade]:
        """
        Place a limit order (sync wrapper).
        
        Args:
            symbol: Stock ticker symbol
            quantity: Number of shares
            limit_price: Limit price
            action: BUY or SELL
            
        Returns:
            Trade object or None if failed
        """
        if not self._loop:
            self._loop = self._get_or_create_event_loop()
        
        return self._loop.run_until_complete(
            self.place_limit_order_async(symbol, quantity, limit_price, action)
        )
    
    async def place_limit_order_async(
        self,
        symbol: str,
        quantity: int,
        limit_price: float,
        action: str = "BUY"
    ) -> Optional[Trade]:
        """
        Place a limit order (async).
        
        Args:
            symbol: Stock ticker symbol
            quantity: Number of shares
            limit_price: Limit price
            action: BUY or SELL
            
        Returns:
            Trade object or None if failed
        """
        if not self.is_connected:
            logger.error("Not connected to IBKR")
            return None
        
        if self.readonly:
            logger.error("Client is in readonly mode")
            return None
        
        contract = self.create_stock_contract(symbol)
        
        try:
            await self._ib.qualifyContractsAsync(contract)
            order = LimitOrder(action, quantity, limit_price)
            trade = self._ib.placeOrder(contract, order)
            logger.info(f"Placed limit {action} order: {symbol} x {quantity} @ {limit_price}")
            return trade
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
    
    def place_stop_order(
        self,
        symbol: str,
        quantity: int,
        stop_price: float,
        action: str = "SELL"
    ) -> Optional[Trade]:
        """
        Place a stop order (sync wrapper).
        
        Args:
            symbol: Stock ticker symbol
            quantity: Number of shares
            stop_price: Stop trigger price
            action: BUY or SELL
            
        Returns:
            Trade object or None if failed
        """
        if not self._loop:
            self._loop = self._get_or_create_event_loop()
        
        return self._loop.run_until_complete(
            self.place_stop_order_async(symbol, quantity, stop_price, action)
        )
    
    async def place_stop_order_async(
        self,
        symbol: str,
        quantity: int,
        stop_price: float,
        action: str = "SELL"
    ) -> Optional[Trade]:
        """
        Place a stop order (async).
        
        Args:
            symbol: Stock ticker symbol
            quantity: Number of shares
            stop_price: Stop trigger price
            action: BUY or SELL
            
        Returns:
            Trade object or None if failed
        """
        if not self.is_connected:
            logger.error("Not connected to IBKR")
            return None
        
        if self.readonly:
            logger.error("Client is in readonly mode")
            return None
        
        contract = self.create_stock_contract(symbol)
        
        try:
            await self._ib.qualifyContractsAsync(contract)
            order = StopOrder(action, quantity, stop_price)
            trade = self._ib.placeOrder(contract, order)
            logger.info(f"Placed stop {action} order: {symbol} x {quantity} @ {stop_price}")
            return trade
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
    
    def cancel_order(self, trade: Trade) -> bool:
        """Cancel an open order."""
        if not self.is_connected:
            return False
        
        try:
            self._ib.cancelOrder(trade.order)
            logger.info(f"Cancelled order: {trade.order.orderId}")
            return True
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all current positions."""
        if not self.is_connected:
            return []
        
        positions = []
        for pos in self._ib.positions():
            positions.append({
                "symbol": pos.contract.symbol,
                "quantity": pos.position,
                "avg_cost": pos.avgCost,
                "market_value": pos.position * pos.avgCost
            })
        
        return positions
    
    async def get_positions_async(self) -> List[Dict[str, Any]]:
        """Get all current positions (async)."""
        if not self.is_connected:
            return []
        
        await self._ib.reqPositionsAsync()
        return self.get_positions()
    
    def get_account_summary(self) -> Dict[str, Any]:
        """Get account summary."""
        if not self.is_connected:
            return {}
        
        summary = {}
        for item in self._ib.accountSummary():
            summary[item.tag] = {
                "value": item.value,
                "currency": item.currency
            }
        
        return summary
    
    async def get_account_summary_async(self) -> Dict[str, Any]:
        """Get account summary (async)."""
        if not self.is_connected:
            return {}
        
        await self._ib.reqAccountSummaryAsync()
        return self.get_account_summary()
    
    def get_open_orders(self) -> List[Trade]:
        """Get all open orders."""
        if not self.is_connected:
            return []
        
        return self._ib.openTrades()
    
    async def get_open_orders_async(self) -> List[Trade]:
        """Get all open orders (async)."""
        if not self.is_connected:
            return []
        
        await self._ib.reqOpenOrdersAsync()
        return self._ib.openTrades()
    
    def run(self):
        """Run the event loop (for keeping subscriptions active)."""
        if self._ib:
            self._ib.run()
    
    async def sleep(self, seconds: float):
        """Async sleep that keeps IB connection alive."""
        if self._ib:
            await self._ib.sleepAsync(seconds)
        else:
            await asyncio.sleep(seconds)
    
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect_async()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        self.disconnect()
