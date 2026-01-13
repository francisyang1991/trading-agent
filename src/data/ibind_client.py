"""
IBKR Client using IBind library
IBind is a comprehensive REST and WebSocket client for IBKR Client Portal Web API.

IBind features:
- REST and WebSocket APIs
- OAuth 1.0a authentication (fully headless)
- Automated question/answer handling
- Parallel requests and rate limiting
- Conid unpacking

Documentation: https://github.com/Voyz/ibind
"""

from typing import Optional, List, Dict, Any, Union
import asyncio
from datetime import datetime, timedelta
import pandas as pd
from loguru import logger

try:
    from ibind import IbkrClient, IbkrWsClient, IbkrWsKey
    from ibind.client.ibkr_client import IbkrClient as IbkrClientBase
    from ibind.client.ibkr_ws_client import IbkrWsClient as IbkrWsClientBase
    IBIND_AVAILABLE = True
except ImportError:
    logger.warning("ibind not installed. Install with: pip install ibind")
    IBIND_AVAILABLE = False
    IbkrClient = None
    IbkrWsClient = None
    IbkrWsKey = None


class IBindRESTClient:
    """
    IBKR REST API client using IBind library.
    Provides high-level interface for market data, orders, and portfolio management.

    Designed to work with IBeam for authentication and session management.
    IBeam provides automated authentication to IBKR's REST API.
    """

    def __init__(
        self,
        ibind_account_id: Optional[str] = None,
        ibind_cacert: Optional[str] = None,
        host: str = "localhost",
        port: str = "5000"
    ):
        """
        Initialize IBind REST client.

        Args:
            ibind_account_id: IBKR account ID (optional, auto-detected from IBeam)
            ibind_cacert: CA certificate path for SSL verification (None disables verification)
            host: IBeam Gateway host (default: localhost)
            port: IBeam Gateway port (default: 5000)
        """
        if not IBIND_AVAILABLE:
            raise ImportError("ibind not available. Install with: pip install ibind")

        self.host = host
        self.port = port

        # Initialize IBind client with IBeam authentication
        # Use False for cacert to disable SSL verification (common for localhost)
        cacert = ibind_cacert if ibind_cacert is not None else False

        self.client = IbkrClient(
            account_id=ibind_account_id,
            cacert=cacert,
            host=host,
            port=port
        )

        self._authenticated = False

    async def authenticate(self) -> bool:
        """
        Authenticate with IBKR.

        Returns:
            True if authenticated successfully
        """
        try:
            # Test authentication
            result = await self.client.tickle()
            if result.data.get('session') == 'active':
                self._authenticated = True
                logger.info("IBind REST client authenticated")
                return True
            else:
                logger.warning("IBind authentication failed")
                return False
        except Exception as e:
            logger.error(f"IBind authentication error: {e}")
            return False

    @property
    def is_authenticated(self) -> bool:
        """Check if authenticated."""
        return self._authenticated

    # =========================================================================
    # Market Data
    # =========================================================================

    async def get_historical_data(
        self,
        symbol: str,
        period: str = "1d",
        bar: str = "1min",
        outside_rth: bool = False
    ) -> pd.DataFrame:
        """
        Get historical market data.

        Args:
            symbol: Stock symbol
            period: Time period (1d, 1w, 1m, 3m, 6m, 1y, 2y, 3y, 5y)
            bar: Bar size (1min, 5min, 15min, 30min, 1h, 4h, 1d, 1w, 1m)
            outside_rth: Include outside regular trading hours

        Returns:
            DataFrame with OHLCV data
        """
        # First get conid for symbol
        conid = await self.get_conid(symbol)
        if not conid:
            raise ValueError(f"Cannot find contract for {symbol}")

        result = await self.client.marketdata_history(
            conid=conid,
            period=period,
            bar=bar,
            outside_rth=outside_rth
        )

        if not result.data:
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(result.data)

        if df.empty:
            return df

        # Rename columns to match our format
        column_map = {
            'o': 'open',
            'h': 'high',
            'l': 'low',
            'c': 'close',
            'v': 'volume',
            't': 'timestamp'
        }
        df = df.rename(columns=column_map)

        # Convert timestamp
        if 'timestamp' in df.columns:
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            df = df.set_index('datetime')
            df = df.drop(columns=['timestamp'], errors='ignore')

        return df

    async def get_conid(self, symbol: str) -> Optional[str]:
        """
        Get contract ID for a symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Contract ID string
        """
        result = await self.client.secdef_search(symbol=symbol)

        if result.data and len(result.data) > 0:
            # Find exact match
            for contract in result.data:
                if contract.get('symbol', '').upper() == symbol.upper():
                    return str(contract.get('conid'))

            # Return first result
            return str(result.data[0].get('conid'))

        return None

    async def get_market_snapshot(
        self,
        symbols: List[str],
        fields: Optional[List[str]] = None
    ) -> Dict[str, Dict]:
        """
        Get market data snapshot for multiple symbols.

        Args:
            symbols: List of stock symbols
            fields: Fields to request (default: common fields)

        Returns:
            Dict mapping symbol to market data
        """
        if fields is None:
            fields = ["31", "84", "85", "86", "88"]  # last, bid, ask, etc.

        # Get conids for all symbols
        conids = []
        symbol_to_conid = {}

        for symbol in symbols:
            conid = await self.get_conid(symbol)
            if conid:
                conids.append(conid)
                symbol_to_conid[conid] = symbol

        if not conids:
            return {}

        # Get market data
        conid_str = ",".join(conids)
        fields_str = ",".join(fields)

        result = await self.client.marketdata_snapshot(
            conids=conid_str,
            fields=fields_str
        )

        # Convert back to symbol mapping
        snapshot = {}
        if result.data:
            for item in result.data:
                conid = str(item.get('conid'))
                symbol = symbol_to_conid.get(conid)
                if symbol:
                    snapshot[symbol] = item

        return snapshot

    # =========================================================================
    # Orders & Portfolio
    # =========================================================================

    async def get_accounts(self) -> List[Dict]:
        """Get list of accounts."""
        result = await self.client.portfolio_accounts()
        return result.data if result.data else []

    async def get_account_id(self) -> Optional[str]:
        """Get first account ID."""
        accounts = await self.get_accounts()
        if accounts:
            return accounts[0].get('accountId')
        return None

    async def get_positions(self) -> List[Dict]:
        """Get current positions."""
        result = await self.client.portfolio_positions()
        return result.data if result.data else []

    async def get_account_summary(self) -> Dict:
        """Get account summary."""
        account_id = await self.get_account_id()
        if not account_id:
            return {}

        result = await self.client.portfolio_summary(account_id)
        return result.data if result.data else {}

    async def place_market_order(
        self,
        symbol: str,
        quantity: int,
        side: str
    ) -> Dict:
        """
        Place a market order.

        Args:
            symbol: Stock symbol
            quantity: Number of shares
            side: BUY or SELL

        Returns:
            Order response
        """
        conid = await self.get_conid(symbol)
        if not conid:
            raise ValueError(f"Cannot find contract for {symbol}")

        account_id = await self.get_account_id()
        if not account_id:
            raise ValueError("No account found")

        result = await self.client.place_order(
            account_id=account_id,
            conid=conid,
            secType="STK",
            orderType="MKT",
            side=side,
            quantity=quantity,
            tif="DAY"
        )

        return result.data if result.data else {}

    async def place_limit_order(
        self,
        symbol: str,
        quantity: int,
        side: str,
        limit_price: float
    ) -> Dict:
        """
        Place a limit order.

        Args:
            symbol: Stock symbol
            quantity: Number of shares
            side: Limit price
            side: BUY or SELL

        Returns:
            Order response
        """
        conid = await self.get_conid(symbol)
        if not conid:
            raise ValueError(f"Cannot find contract for {symbol}")

        account_id = await self.get_account_id()
        if not account_id:
            raise ValueError("No account found")

        result = await self.client.place_order(
            account_id=account_id,
            conid=conid,
            secType="STK",
            orderType="LMT",
            side=side,
            quantity=quantity,
            price=limit_price,
            tif="DAY"
        )

        return result.data if result.data else {}

    async def get_orders(self) -> List[Dict]:
        """Get open orders."""
        result = await self.client.orders()
        return result.data if result.data else []

    async def cancel_order(self, order_id: str) -> Dict:
        """
        Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            Cancellation response
        """
        account_id = await self.get_account_id()
        if not account_id:
            raise ValueError("No account found")

        result = await self.client.cancel_order(account_id, order_id)
        return result.data if result.data else {}

    # =========================================================================
    # Utility Methods
    # =========================================================================

    async def check_health(self) -> Dict:
        """Check API health."""
        result = await self.client.check_health()
        return result.data if result.data else {}

    async def tickle(self) -> Dict:
        """Keep session alive."""
        result = await self.client.tickle()
        return result.data if result.data else {}


class IBindWebSocketClient:
    """
    IBKR WebSocket API client using IBind library.
    Provides real-time market data streaming.

    Features:
    - Thread-safe queue data streaming
    - Automatic subscription management
    - Health monitoring
    """

    def __init__(
        self,
        ibind_account_id: Optional[str] = None,
        ibind_cacert: Optional[str] = None,
        host: str = "localhost",
        port: str = "5000"
    ):
        """
        Initialize IBind WebSocket client.

        Args:
            ibind_account_id: IBKR account ID
            ibind_cacert: CA certificate path
            host: IBeam Gateway host (default: localhost)
            port: IBeam Gateway port (default: 5000)
        """
        if not IBIND_AVAILABLE:
            raise ImportError("ibind not available. Install with: pip install ibind")

        self.account_id = ibind_account_id
        self.host = host
        self.port = port

        # Use False for cacert to disable SSL verification
        cacert = ibind_cacert if ibind_cacert is not None else False

        # WebSocket client
        self.ws_client = IbkrWsClient(
            account_id=ibind_account_id,
            cacert=cacert,
            host=host,
            port=port,
            start=False  # We'll start it manually
        )

        self._running = False
        self._subscriptions = set()

    async def start(self) -> bool:
        """
        Start the WebSocket client.

        Returns:
            True if started successfully
        """
        try:
            self.ws_client.start()
            self._running = True
            logger.info("IBind WebSocket client started")
            return True
        except Exception as e:
            logger.error(f"Failed to start WebSocket client: {e}")
            return False

    def stop(self):
        """Stop the WebSocket client."""
        if self._running:
            self.ws_client.stop()
            self._running = False
            logger.info("IBind WebSocket client stopped")

    @property
    def is_running(self) -> bool:
        """Check if WebSocket client is running."""
        return self._running

    async def subscribe_market_data(self, symbols: List[str]) -> bool:
        """
        Subscribe to real-time market data.

        Args:
            symbols: List of symbols to subscribe to

        Returns:
            True if subscription successful
        """
        if not self._running:
            logger.error("WebSocket client not running")
            return False

        try:
            # Get conids
            rest_client = IBindRESTClient(
                ibind_account_id=self.account_id,
                gateway_url=self.gateway_url.replace('wss://', 'https://')
            )

            conids = []
            for symbol in symbols:
                conid = await rest_client.get_conid(symbol)
                if conid:
                    conids.append(conid)

            if conids:
                # Subscribe to market data
                conid_str = ",".join(conids)
                fields = "31,84,85,86,88"  # Common fields

                self.ws_client.subscribe(
                    channel='md',
                    conids=conid_str,
                    fields=fields
                )

                self._subscriptions.update(symbols)
                logger.info(f"Subscribed to market data for: {symbols}")
                return True

        except Exception as e:
            logger.error(f"Subscription failed: {e}")

        return False

    def get_market_data(self) -> List[Dict]:
        """
        Get available market data from queue.

        Returns:
            List of market data updates
        """
        if not self._running:
            return []

        data = []
        try:
            # Get data from market data queue
            while not self.ws_client.empty(IbkrWsKey.MD):
                item = self.ws_client.get(IbkrWsKey.MD)
                data.append(item)
        except Exception as e:
            logger.error(f"Error getting market data: {e}")

        return data

    async def subscribe_pnl(self) -> bool:
        """
        Subscribe to P&L updates.

        Returns:
            True if subscription successful
        """
        if not self._running:
            return False

        try:
            self.ws_client.subscribe(channel='pnl')
            logger.info("Subscribed to P&L updates")
            return True
        except Exception as e:
            logger.error(f"P&L subscription failed: {e}")
            return False

    def get_pnl_updates(self) -> List[Dict]:
        """
        Get P&L updates from queue.

        Returns:
            List of P&L updates
        """
        if not self._running:
            return []

        data = []
        try:
            while not self.ws_client.empty(IbkrWsKey.PNL):
                item = self.ws_client.get(IbkrWsKey.PNL)
                data.append(item)
        except Exception as e:
            logger.error(f"Error getting P&L data: {e}")

        return data

    def get_subscriptions(self) -> set:
        """Get current subscriptions."""
        return self._subscriptions.copy()


# =========================================================================
# Example Usage Functions
# =========================================================================

async def example_basic_rest_client():
    """
    Example: Basic REST client usage with IBeam authentication.
    """
    print("🔗 IBind REST Client Example (with IBeam)")
    print("=" * 50)

    try:
        # Initialize client (uses IBeam authentication)
        client = IBindRESTClient(
            ibind_account_id="YOUR_ACCOUNT_ID",  # Set in environment or here
            gateway_url="https://localhost:5000"  # IBeam default
        )

        # Authenticate
        print("Authenticating...")
        authenticated = await client.authenticate()

        if not authenticated:
            print("❌ Authentication failed. Make sure IBeam is running.")
            return

        print("✅ Authenticated successfully!")

        # Get account info
        accounts = await client.get_accounts()
        print(f"📊 Accounts: {len(accounts)}")
        if accounts:
            print(f"   Account ID: {accounts[0].get('accountId')}")

        # Get AAPL historical data
        print("\n📈 Getting AAPL historical data...")
        hist_data = await client.get_historical_data("AAPL", period="1d", bar="1d")

        if not hist_data.empty:
            print(f"   Received {len(hist_data)} bars")
            print(".2f")
        else:
            print("   No data received")

        # Get market snapshot
        print("\n📊 Getting market snapshot...")
        snapshot = await client.get_market_snapshot(["AAPL", "MSFT"])

        for symbol, data in snapshot.items():
            last_price = data.get('31', 'N/A')
            print(f"   {symbol}: ${last_price}")

        # Get positions
        print("\n💼 Getting positions...")
        positions = await client.get_positions()
        print(f"   Open positions: {len(positions)}")

        for pos in positions[:3]:  # Show first 3
            print(f"   {pos.get('ticker')}: {pos.get('position')} shares @ ${pos.get('avgCost', 0):.2f}")

    except Exception as e:
        print(f"❌ Error: {e}")


async def example_websocket_client():
    """
    Example: WebSocket client for real-time data.
    """
    print("\n🔗 IBind WebSocket Client Example")
    print("=" * 50)

    try:
        # Initialize WebSocket client
        ws_client = IBindWebSocketClient(
            ibind_account_id="YOUR_ACCOUNT_ID",
            gateway_url="wss://localhost:5000"
        )

        # Start client
        print("Starting WebSocket client...")
        started = await ws_client.start()

        if not started:
            print("❌ Failed to start WebSocket client")
            return

        print("✅ WebSocket client started!")

        # Subscribe to market data
        print("Subscribing to market data...")
        subscribed = await ws_client.subscribe_market_data(["AAPL", "MSFT"])

        if subscribed:
            print("✅ Subscribed to AAPL and MSFT market data")

            # Listen for 10 seconds
            print("Listening for market data (10 seconds)...")
            import time
            start_time = time.time()

            while time.time() - start_time < 10:
                data = ws_client.get_market_data()
                if data:
                    for item in data:
                        conid = item.get('conid')
                        price = item.get('31')  # Last price
                        print(f"   Conid {conid}: ${price}")
                await asyncio.sleep(0.1)

        # Subscribe to P&L
        print("Subscribing to P&L updates...")
        pnl_subscribed = await ws_client.subscribe_pnl()

        if pnl_subscribed:
            print("✅ Subscribed to P&L updates")

            # Listen for P&L for 5 seconds
            start_time = time.time()
            while time.time() - start_time < 5:
                pnl_data = ws_client.get_pnl_updates()
                if pnl_data:
                    for item in pnl_data:
                        print(f"   P&L Update: {item}")
                await asyncio.sleep(0.1)

        # Stop client
        ws_client.stop()
        print("✅ WebSocket client stopped")

    except Exception as e:
        print(f"❌ Error: {e}")


async def example_order_placement():
    """
    Example: Placing orders with IBind.
    """
    print("\n📝 IBind Order Placement Example")
    print("=" * 50)
    print("⚠️  This example places REAL ORDERS - use PAPER ACCOUNT only!")

    try:
        client = IBindRESTClient(
            ibind_account_id="YOUR_ACCOUNT_ID",
            gateway_url="https://localhost:5000"
        )

        authenticated = await client.authenticate()
        if not authenticated:
            print("❌ Authentication failed")
            return

        # Example: Place market order (COMMENTED OUT FOR SAFETY)
        print("Market order example (commented out for safety):")
        print("# order = await client.place_market_order('AAPL', 1, 'BUY')")
        print("# print(f'Order placed: {order}')")

        # Example: Place limit order (COMMENTED OUT FOR SAFETY)
        print("\nLimit order example (commented out for safety):")
        print("# order = await client.place_limit_order('AAPL', 1, 'BUY', 150.00)")
        print("# print(f'Order placed: {order}')")

        # Get open orders
        print("\n📋 Getting open orders...")
        orders = await client.get_orders()
        print(f"   Open orders: {len(orders)}")

        for order in orders[:3]:  # Show first 3
            print(f"   Order {order.get('orderId')}: {order.get('side')} {order.get('quantity')} {order.get('ticker')}")

    except Exception as e:
        print(f"❌ Error: {e}")


async def main():
    """Run all IBind examples."""
    print("🚀 SAIYAN Trading Agent - IBind Examples")
    print("=" * 60)
    print("Make sure IBeam is running: docker compose up -d")
    print("Set your account ID in the examples below.")
    print("=" * 60)

    # Run examples
    await example_basic_rest_client()
    await example_websocket_client()
    await example_order_placement()


if __name__ == "__main__":
    asyncio.run(main())
