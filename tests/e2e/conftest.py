"""
E2E Test Fixtures and Configuration.

Provides fixtures for IB Gateway connection testing with safety guardrails.
"""

import asyncio
import os
import socket
from datetime import datetime
from typing import AsyncGenerator, List

import pytest
import pytest_asyncio

from src.data.connection_manager import IBConnectionManager, ConnectionConfig, ConnectionState
from src.data.ibkr_async_client import IBKRAsyncClient
from src.execution.async_order_executor import AsyncOrderExecutor


# =============================================================================
# Configuration
# =============================================================================

# Fixed test client ID - use a dedicated range to avoid conflicts with production
# Tests use client IDs 100-109 (max 10 concurrent test connections)
_TEST_CLIENT_ID = int(os.getenv("IB_TEST_CLIENT_ID", "100"))


def get_test_config(client_id: int = None) -> ConnectionConfig:
    """Get test configuration from environment."""
    if client_id is None:
        client_id = _TEST_CLIENT_ID
    
    return ConnectionConfig(
        host=os.getenv("IB_HOST", "127.0.0.1"),
        port=int(os.getenv("IB_PORT", "4002")),  # Paper trading port
        client_id=client_id,  # Fixed client ID for tests
        readonly=False,
        reconnect_delay=2.0,  # Faster for tests
        max_reconnect_attempts=3,  # Fewer retries for tests
        heartbeat_interval=10.0,  # Faster heartbeat for tests
        connection_timeout=15.0,  # Shorter timeout for tests
    )


# =============================================================================
# Session Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_config() -> ConnectionConfig:
    """Provide test configuration with unique client ID per test."""
    return get_test_config()


@pytest.fixture(scope="session")
def test_symbols() -> List[str]:
    """Safe test symbols (high liquidity, low price impact)."""
    return ["SPY", "AAPL", "QQQ"]


@pytest.fixture(scope="session")
def max_test_quantity() -> int:
    """Maximum shares per test order - keep small for safety."""
    return 1


# =============================================================================
# Gateway Availability
# =============================================================================

def is_gateway_available(host: str = "127.0.0.1", port: int = 4002) -> bool:
    """Quick check if IB Gateway is accepting connections."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


@pytest.fixture
def gateway_available() -> bool:
    """Check if IB Gateway is available."""
    host = os.getenv("IB_HOST", "127.0.0.1")
    port = int(os.getenv("IB_PORT", "4002"))
    return is_gateway_available(host, port)


@pytest.fixture
def skip_if_no_gateway(gateway_available: bool):
    """Skip test if IB Gateway is not available."""
    if not gateway_available:
        pytest.skip("IB Gateway not available - start with: docker compose --profile gateway up -d")


# =============================================================================
# Connection Manager Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def connection_manager(
    test_config: ConnectionConfig,
    skip_if_no_gateway
) -> AsyncGenerator[IBConnectionManager, None]:
    """Create connection manager for testing."""
    manager = IBConnectionManager(test_config)
    yield manager
    
    # Cleanup
    if manager.state != ConnectionState.SHUTDOWN:
        await manager.shutdown()


@pytest_asyncio.fixture
async def connected_manager(
    connection_manager: IBConnectionManager
) -> AsyncGenerator[IBConnectionManager, None]:
    """Provide a connected connection manager."""
    success = await connection_manager.connect()
    if not success:
        pytest.skip("Failed to connect to IB Gateway")
    
    yield connection_manager


# =============================================================================
# Client Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def ib_client(
    test_config: ConnectionConfig,
    skip_if_no_gateway
) -> AsyncGenerator[IBKRAsyncClient, None]:
    """Create IBKRAsyncClient for testing."""
    client = IBKRAsyncClient(test_config)
    yield client
    
    # Cleanup
    await client.disconnect()


@pytest_asyncio.fixture
async def connected_client(
    ib_client: IBKRAsyncClient
) -> AsyncGenerator[IBKRAsyncClient, None]:
    """Provide a connected client."""
    success = await ib_client.connect()
    if not success:
        pytest.skip("Failed to connect to IB Gateway")
    
    yield ib_client


# =============================================================================
# Order Executor Fixtures
# =============================================================================

@pytest_asyncio.fixture
async def order_executor(
    connected_client: IBKRAsyncClient
) -> AsyncGenerator[AsyncOrderExecutor, None]:
    """Create order executor for testing."""
    executor = AsyncOrderExecutor(connected_client)
    yield executor
    
    # Cleanup - cancel any pending test orders
    await executor.cleanup()


# =============================================================================
# Safety Fixtures
# =============================================================================

class OrderTracker:
    """Track orders placed during tests for cleanup."""
    
    def __init__(self):
        self.order_ids: List[str] = []
        self.max_orders = 10  # Circuit breaker
    
    def add_order(self, order_id: str):
        """Track an order."""
        self.order_ids.append(order_id)
        if len(self.order_ids) >= self.max_orders:
            raise RuntimeError(f"Circuit breaker: {self.max_orders}+ orders placed in test run")
    
    async def cancel_all(self, executor: AsyncOrderExecutor):
        """Cancel all tracked orders."""
        for order_id in self.order_ids:
            try:
                await executor.cancel_order(order_id)
            except Exception:
                pass
        self.order_ids.clear()


@pytest.fixture
def order_tracker() -> OrderTracker:
    """Provide order tracker for test cleanup."""
    return OrderTracker()


@pytest_asyncio.fixture
async def safe_executor(
    order_executor: AsyncOrderExecutor,
    order_tracker: OrderTracker
) -> AsyncGenerator[tuple, None]:
    """Provide executor with order tracking for cleanup."""
    yield order_executor, order_tracker
    
    # Cleanup any test orders
    await order_tracker.cancel_all(order_executor)


# =============================================================================
# Test Markers
# =============================================================================

def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "live: marks tests that require live IB Gateway connection"
    )
    config.addinivalue_line(
        "markers", "order: marks tests that place real orders (paper trading)"
    )
    config.addinivalue_line(
        "markers", "reliability: marks long-running reliability tests"
    )


# =============================================================================
# Helper Functions
# =============================================================================

async def get_safe_price(client: IBKRAsyncClient, symbol: str) -> float:
    """
    Get price safely, falling back to historical data if real-time unavailable.
    
    Paper trading accounts often lack real-time market data subscription.
    
    Args:
        client: Connected IBKRAsyncClient
        symbol: Stock symbol
        
    Returns:
        Price as float, or raises ValueError if unavailable
    """
    import math
    
    # Try real-time price first
    price = await client.get_current_price(symbol)
    
    if price is not None and not math.isnan(price) and price > 0:
        return price
    
    # Fall back to historical data
    df = await client.get_historical_data(symbol, "1 day", "5 D")
    
    if not df.empty:
        return float(df['close'].iloc[-1])
    
    raise ValueError(f"Could not get price for {symbol}")


async def wait_for_condition(
    condition_fn,
    timeout: float = 30.0,
    interval: float = 0.5
) -> bool:
    """
    Wait for a condition to become true.
    
    Args:
        condition_fn: Callable that returns True when condition is met
        timeout: Maximum wait time in seconds
        interval: Check interval in seconds
        
    Returns:
        True if condition met within timeout
    """
    start = asyncio.get_event_loop().time()
    
    while asyncio.get_event_loop().time() - start < timeout:
        if condition_fn():
            return True
        await asyncio.sleep(interval)
    
    return False


def assert_order_result(result, expected_success: bool = True):
    """Assert order result is valid."""
    assert result is not None, "Order result is None"
    assert result.success == expected_success, f"Order success={result.success}, expected={expected_success}: {result.message}"
    assert result.symbol, "Order symbol is empty"
    assert result.action in ["BUY", "SELL"], f"Invalid action: {result.action}"
    assert result.quantity > 0, f"Invalid quantity: {result.quantity}"
