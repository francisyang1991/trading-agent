"""
IB Connection Manager - Production-grade connection management for ib_async.

Handles:
- Auto-reconnection with exponential backoff
- Heartbeat monitoring to detect connection issues
- Connection state machine with event callbacks
- Graceful shutdown and resource cleanup

For 24/7 cloud deployment reliability.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

try:
    from ib_async import IB
    IB_ASYNC_AVAILABLE = True
except ImportError:
    pass  # Optional: ib_async only needed for live IBKR trading
    IB_ASYNC_AVAILABLE = False
    IB = None


class ConnectionState(Enum):
    """Connection state machine states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class TradingMode(Enum):
    """Trading mode - paper or live."""
    PAPER = "paper"
    LIVE = "live"
    
    @property
    def default_port(self) -> int:
        """Get default port for this trading mode."""
        return 4002 if self == TradingMode.PAPER else 4001


@dataclass
class ConnectionConfig:
    """Configuration for IB Gateway connection."""
    host: str = "127.0.0.1"
    port: int = 4002  # 4001=live, 4002=paper
    client_id: int = 1
    readonly: bool = False
    trading_mode: TradingMode = TradingMode.PAPER  # paper or live
    
    # Connection reliability settings
    reconnect_delay: float = 5.0
    max_reconnect_attempts: int = 10
    heartbeat_interval: float = 30.0
    connection_timeout: float = 30.0
    
    # Request throttling
    requests_per_second: int = 45  # IBKR limit is 50
    
    @classmethod
    def from_env(cls) -> "ConnectionConfig":
        """Create config from environment variables."""
        import os
        
        # Determine trading mode
        mode_str = os.getenv("IB_TRADING_MODE", "paper").lower()
        trading_mode = TradingMode.LIVE if mode_str == "live" else TradingMode.PAPER
        
        # Port defaults based on trading mode if not explicitly set
        default_port = trading_mode.default_port
        port = int(os.getenv("IB_PORT", str(default_port)))
        
        return cls(
            host=os.getenv("IB_HOST", "127.0.0.1"),
            port=port,
            client_id=int(os.getenv("IB_CLIENT_ID", "1")),
            readonly=os.getenv("IB_READONLY", "no").lower() == "yes",
            trading_mode=trading_mode,
            reconnect_delay=float(os.getenv("IB_RECONNECT_DELAY", "5")),
            max_reconnect_attempts=int(os.getenv("IB_MAX_RECONNECT_ATTEMPTS", "10")),
            heartbeat_interval=float(os.getenv("IB_HEARTBEAT_INTERVAL", "30")),
            connection_timeout=float(os.getenv("IB_CONNECTION_TIMEOUT", "30")),
        )
    
    @property
    def is_paper(self) -> bool:
        """Check if this is paper trading mode."""
        return self.trading_mode == TradingMode.PAPER
    
    @property
    def is_live(self) -> bool:
        """Check if this is live trading mode."""
        return self.trading_mode == TradingMode.LIVE


@dataclass
class AccountStatus:
    """Account status snapshot for monitoring."""
    net_liquidation: Optional[float] = None
    total_cash: Optional[float] = None
    buying_power: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    day_trades_remaining: Optional[int] = None
    position_count: int = 0
    open_order_count: int = 0
    updated_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "net_liquidation": self.net_liquidation,
            "total_cash": self.total_cash,
            "buying_power": self.buying_power,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "day_trades_remaining": self.day_trades_remaining,
            "position_count": self.position_count,
            "open_order_count": self.open_order_count,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


@dataclass
class ConnectionStats:
    """Connection statistics for monitoring."""
    connect_time: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    reconnect_count: int = 0
    total_reconnects: int = 0
    failed_heartbeats: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None
    account_status: Optional[AccountStatus] = None


class IBConnectionManager:
    """
    Production-grade connection manager for ib_async.
    
    Handles auto-reconnection, heartbeat monitoring, and state management
    for reliable 24/7 operation.
    
    Usage:
        config = ConnectionConfig.from_env()
        conn_mgr = IBConnectionManager(config)
        
        # Register callbacks
        conn_mgr.on_connected.append(my_connected_handler)
        conn_mgr.on_disconnected.append(my_disconnected_handler)
        
        # Connect
        await conn_mgr.connect()
        
        # Use the IB instance
        ib = conn_mgr.ib
        
        # Graceful shutdown
        await conn_mgr.shutdown()
    """
    
    def __init__(self, config: Optional[ConnectionConfig] = None):
        """
        Initialize connection manager.
        
        Args:
            config: Connection configuration (defaults to env vars)
        """
        if not IB_ASYNC_AVAILABLE:
            raise RuntimeError("ib_async not available. Install with: pip install ib_async")
        
        self.config = config or ConnectionConfig.from_env()
        self._ib: Optional[IB] = None
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_count = 0
        self._shutdown_requested = False
        
        # Background tasks
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        
        # Event callbacks - list of async callables
        self.on_connected: List[Callable[[], Any]] = []
        self.on_disconnected: List[Callable[[Optional[Exception]], Any]] = []
        self.on_reconnecting: List[Callable[[int], Any]] = []
        self.on_error: List[Callable[[str], Any]] = []
        self.on_state_change: List[Callable[[ConnectionState, ConnectionState], Any]] = []
        
        # Statistics
        self.stats = ConnectionStats()
        
        # Lock for connection operations
        self._connect_lock = asyncio.Lock()
    
    @property
    def ib(self) -> Optional[IB]:
        """Get the underlying IB instance."""
        return self._ib
    
    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state
    
    @property
    def is_connected(self) -> bool:
        """Check if connected and ready."""
        return (
            self._state == ConnectionState.CONNECTED 
            and self._ib is not None 
            and self._ib.isConnected()
        )
    
    async def connect(self) -> bool:
        """
        Establish connection to IB Gateway.
        
        Returns:
            True if connection successful
        """
        async with self._connect_lock:
            if self._state == ConnectionState.CONNECTED and self.is_connected:
                logger.debug("Already connected to IB Gateway")
                return True
            
            if self._shutdown_requested:
                logger.warning("Cannot connect - shutdown requested")
                return False
            
            return await self._try_connect()
    
    async def _try_connect(self) -> bool:
        """Attempt to establish connection."""
        old_state = self._state
        self._set_state(ConnectionState.CONNECTING)
        
        try:
            # Create new IB instance
            self._ib = IB()
            
            # Register disconnect handler
            self._ib.disconnectedEvent += self._on_ib_disconnected
            
            # Connect with timeout
            logger.info(
                f"Connecting to IB Gateway at {self.config.host}:{self.config.port} "
                f"(client_id={self.config.client_id})"
            )
            
            await asyncio.wait_for(
                self._ib.connectAsync(
                    host=self.config.host,
                    port=self.config.port,
                    clientId=self.config.client_id,
                    readonly=self.config.readonly,
                    timeout=self.config.connection_timeout
                ),
                timeout=self.config.connection_timeout + 5
            )
            
            # Connection successful
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_count = 0
            self.stats.connect_time = datetime.now()
            self.stats.last_heartbeat = datetime.now()
            
            logger.info(f"Connected to IB Gateway successfully")
            
            # Start heartbeat monitoring
            self._start_heartbeat()
            
            # Notify callbacks
            await self._notify_callbacks(self.on_connected)
            
            return True
            
        except asyncio.TimeoutError:
            error_msg = f"Connection timeout after {self.config.connection_timeout}s"
            logger.error(error_msg)
            self._handle_connection_error(error_msg)
            return False
            
        except Exception as e:
            error_msg = f"Connection failed: {e}"
            logger.error(error_msg)
            self._handle_connection_error(str(e))
            return False
    
    def _handle_connection_error(self, error: str):
        """Handle connection error."""
        self.stats.last_error = error
        self.stats.last_error_time = datetime.now()
        
        if self._ib:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None
        
        self._set_state(ConnectionState.DISCONNECTED)
    
    def _on_ib_disconnected(self):
        """Handle IB disconnection event."""
        if self._shutdown_requested:
            logger.info("Disconnected (shutdown requested)")
            return
        
        logger.warning("IB Gateway disconnected unexpectedly")
        self._set_state(ConnectionState.DISCONNECTED)
        
        # Trigger reconnection
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._handle_disconnect())
    
    async def _handle_disconnect(self):
        """Handle disconnection and trigger reconnection."""
        # Stop heartbeat
        self._stop_heartbeat()
        
        # Notify callbacks
        await self._notify_callbacks(self.on_disconnected, None)
        
        # Attempt reconnection
        if not self._shutdown_requested:
            await self._reconnect()
    
    async def _reconnect(self):
        """Auto-reconnect with exponential backoff."""
        self._set_state(ConnectionState.RECONNECTING)
        
        while (
            self._reconnect_count < self.config.max_reconnect_attempts
            and not self._shutdown_requested
        ):
            self._reconnect_count += 1
            self.stats.reconnect_count = self._reconnect_count
            self.stats.total_reconnects += 1
            
            # Calculate delay with exponential backoff (capped at 60s)
            delay = min(
                self.config.reconnect_delay * (2 ** (self._reconnect_count - 1)),
                60.0
            )
            
            logger.info(
                f"Reconnection attempt {self._reconnect_count}/{self.config.max_reconnect_attempts} "
                f"in {delay:.1f}s"
            )
            
            # Notify callbacks
            await self._notify_callbacks(self.on_reconnecting, self._reconnect_count)
            
            # Wait before reconnecting
            await asyncio.sleep(delay)
            
            if self._shutdown_requested:
                break
            
            # Try to connect
            if await self._try_connect():
                logger.info("Reconnection successful")
                return
        
        # Max attempts exceeded
        if not self._shutdown_requested:
            error_msg = f"Max reconnection attempts ({self.config.max_reconnect_attempts}) exceeded"
            logger.error(error_msg)
            self._set_state(ConnectionState.ERROR)
            self.stats.last_error = error_msg
            self.stats.last_error_time = datetime.now()
            await self._notify_callbacks(self.on_error, error_msg)
    
    def _start_heartbeat(self):
        """Start heartbeat monitoring task."""
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.debug("Heartbeat monitoring started")
    
    def _stop_heartbeat(self):
        """Stop heartbeat monitoring task."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
            logger.debug("Heartbeat monitoring stopped")
    
    async def _heartbeat_loop(self):
        """Periodic heartbeat to maintain and verify connection."""
        consecutive_failures = 0
        max_failures = 3
        
        while self._state == ConnectionState.CONNECTED and not self._shutdown_requested:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                
                if self._shutdown_requested:
                    break
                
                # Check connection status
                if self._ib and self._ib.isConnected():
                    self.stats.last_heartbeat = datetime.now()
                    consecutive_failures = 0
                    logger.debug("Heartbeat OK")
                else:
                    raise ConnectionError("IB not connected")
                    
            except asyncio.CancelledError:
                break
                
            except Exception as e:
                consecutive_failures += 1
                self.stats.failed_heartbeats += 1
                logger.warning(f"Heartbeat failed ({consecutive_failures}/{max_failures}): {e}")
                
                if consecutive_failures >= max_failures:
                    logger.error("Too many heartbeat failures, triggering reconnection")
                    await self._handle_disconnect()
                    break
    
    def fetch_account_status(self) -> Optional[AccountStatus]:
        """
        Fetch current account status from IB (synchronous).
        Call this method explicitly to get account data.
        
        Returns:
            AccountStatus object or None if not connected
        """
        if not self._ib or not self._ib.isConnected():
            return None
        
        try:
            # Request and wait for account values
            self._ib.reqAccountSummary()
            self._ib.sleep(2)  # Wait for data
            
            # Parse account values
            account_status = AccountStatus(updated_at=datetime.now())
            
            for item in self._ib.accountSummary():
                tag = item.tag
                try:
                    value = float(item.value)
                except (ValueError, TypeError):
                    continue
                
                if tag == "NetLiquidation":
                    account_status.net_liquidation = value
                elif tag == "TotalCashValue":
                    account_status.total_cash = value
                elif tag == "BuyingPower":
                    account_status.buying_power = value
                elif tag == "UnrealizedPnL":
                    account_status.unrealized_pnl = value
                elif tag == "RealizedPnL":
                    account_status.realized_pnl = value
                elif tag == "DayTradesRemaining":
                    account_status.day_trades_remaining = int(value)
            
            # Get position and order counts
            self._ib.reqPositions()
            self._ib.sleep(1)
            account_status.position_count = len(self._ib.positions())
            
            self._ib.reqOpenOrders()
            self._ib.sleep(1)
            account_status.open_order_count = len(self._ib.openTrades())
            
            self.stats.account_status = account_status
            return account_status
            
        except Exception as e:
            logger.error(f"Failed to fetch account status: {e}")
            return None
    
    def _set_state(self, new_state: ConnectionState):
        """Set connection state and notify listeners."""
        if new_state == self._state:
            return
        
        old_state = self._state
        self._state = new_state
        logger.info(f"Connection state: {old_state.value} -> {new_state.value}")
        
        # Notify state change callbacks (fire and forget)
        asyncio.create_task(
            self._notify_callbacks(self.on_state_change, old_state, new_state)
        )
    
    async def _notify_callbacks(self, callbacks: List[Callable], *args):
        """Notify all registered callbacks."""
        for callback in callbacks:
            try:
                result = callback(*args)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    async def shutdown(self):
        """Graceful shutdown - disconnect and cleanup."""
        logger.info("Initiating connection manager shutdown")
        self._shutdown_requested = True
        self._set_state(ConnectionState.SHUTDOWN)
        
        # Stop heartbeat
        self._stop_heartbeat()
        
        # Cancel reconnection task
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        
        # Disconnect IB
        if self._ib:
            try:
                self._ib.disconnectedEvent -= self._on_ib_disconnected
                self._ib.disconnect()
            except Exception as e:
                logger.error(f"Error during disconnect: {e}")
            finally:
                self._ib = None
        
        logger.info("Connection manager shutdown complete")
    
    async def wait_for_connection(self, timeout: float = 60.0) -> bool:
        """
        Wait for connection to be established.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if connected within timeout
        """
        start = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start < timeout:
            if self.is_connected:
                return True
            
            if self._state == ConnectionState.ERROR:
                return False
            
            await asyncio.sleep(0.5)
        
        return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get current connection status for monitoring."""
        return {
            "state": self._state.value,
            "is_connected": self.is_connected,
            "trading_mode": self.config.trading_mode.value,
            "host": self.config.host,
            "port": self.config.port,
            "client_id": self.config.client_id,
            "stats": {
                "connect_time": self.stats.connect_time.isoformat() if self.stats.connect_time else None,
                "last_heartbeat": self.stats.last_heartbeat.isoformat() if self.stats.last_heartbeat else None,
                "reconnect_count": self.stats.reconnect_count,
                "total_reconnects": self.stats.total_reconnects,
                "failed_heartbeats": self.stats.failed_heartbeats,
                "last_error": self.stats.last_error,
                "last_error_time": self.stats.last_error_time.isoformat() if self.stats.last_error_time else None,
            },
            "account": self.stats.account_status.to_dict() if self.stats.account_status else None
        }
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.shutdown()
