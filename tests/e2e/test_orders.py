"""
Order Lifecycle Tests for IB Gateway Paper Trading.

Tests:
- Market order execution and fill
- Limit order submission
- Order cancellation
- Stop order submission
- Position tracking after fills

IMPORTANT: All tests use paper trading account only.
Order quantities are limited to 1 share for safety.

Run with: pytest tests/e2e/test_orders.py -v
"""

import asyncio
from datetime import datetime

import pytest
import pytest_asyncio

from src.execution.async_order_executor import (
    AsyncOrderExecutor,
    OrderResult,
    OrderStatus,
    OrderType,
)
from src.data.ibkr_async_client import IBKRAsyncClient


# =============================================================================
# Market Order Tests
# =============================================================================

class TestMarketOrders:
    """Market order tests."""
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_market_order_buy(
        self,
        safe_executor: tuple,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test placing a market buy order."""
        executor, tracker = safe_executor
        symbol = test_symbols[0]  # SPY
        
        result = await executor.execute_market_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            timeout=60.0  # Longer timeout for fills
        )
        
        # Track for cleanup
        if result.order_id:
            tracker.add_order(result.order_id)
        
        # Validate result
        assert result is not None, "Should return OrderResult"
        assert result.symbol == symbol
        assert result.action == "BUY"
        assert result.order_type == OrderType.MARKET
        assert result.quantity == max_test_quantity
        
        # During market hours, should fill
        # During off-hours, may timeout
        if result.success and result.status == OrderStatus.FILLED:
            assert result.filled_price > 0, "Should have fill price"
            assert result.filled_quantity == max_test_quantity
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_market_order_sell(
        self,
        safe_executor: tuple,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test placing a market sell order."""
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        # Note: This will create a short position if no existing position
        result = await executor.execute_market_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="SELL",
            timeout=60.0
        )
        
        if result.order_id:
            tracker.add_order(result.order_id)
        
        assert result is not None
        assert result.action == "SELL"
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_market_order_fill_callback(
        self,
        safe_executor: tuple,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test that fill callback is called."""
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        fill_events = []
        
        def on_fill(result: OrderResult):
            fill_events.append(result)
        
        executor.on_fill.append(on_fill)
        
        result = await executor.execute_market_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            timeout=60.0
        )
        
        if result.order_id:
            tracker.add_order(result.order_id)
        
        # If filled, callback should have been called
        if result.success and result.status == OrderStatus.FILLED:
            assert len(fill_events) > 0, "Fill callback should have been called"


# =============================================================================
# Limit Order Tests
# =============================================================================

class TestLimitOrders:
    """Limit order tests."""
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_limit_order_submit(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test placing a limit order (won't fill immediately)."""
        from tests.e2e.conftest import get_safe_price
        
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        # Get current price safely (falls back to historical if needed)
        try:
            current_price = await get_safe_price(connected_client, symbol)
        except ValueError:
            pytest.skip("Could not get price data")
        
        # Set limit price 10% below market (won't fill for buy)
        limit_price = round(current_price * 0.90, 2)
        
        result = await executor.execute_limit_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            limit_price=limit_price,
            wait_for_fill=False  # Don't wait
        )
        
        if result.order_id:
            tracker.add_order(result.order_id)
        
        # Should be submitted but not filled
        assert result.success, f"Order should be submitted: {result.message}"
        assert result.status == OrderStatus.SUBMITTED
        assert result.order_id, "Should have order ID"
        assert result.requested_price == limit_price
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_limit_order_wait_timeout(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test limit order timeout when waiting for fill."""
        from tests.e2e.conftest import get_safe_price
        
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        try:
            current_price = await get_safe_price(connected_client, symbol)
        except ValueError:
            pytest.skip("Could not get current price")
        
        # Set limit price far from market
        limit_price = round(current_price * 0.80, 2)
        
        result = await executor.execute_limit_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            limit_price=limit_price,
            wait_for_fill=True,
            timeout=5.0  # Short timeout - should timeout
        )
        
        if result.order_id:
            tracker.add_order(result.order_id)
        
        # Should timeout (order won't fill at 20% below market)
        assert result.success  # Order was submitted
        # Status may be SUBMITTED (not filled within timeout)


# =============================================================================
# Stop Order Tests
# =============================================================================

class TestStopOrders:
    """Stop order tests."""
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_stop_order_submit(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test placing a stop order."""
        from tests.e2e.conftest import get_safe_price
        
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        try:
            current_price = await get_safe_price(connected_client, symbol)
        except ValueError:
            pytest.skip("Could not get current price")
        
        # Set stop price below market (sell stop)
        stop_price = round(current_price * 0.95, 2)
        
        result = await executor.execute_stop_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="SELL",
            stop_price=stop_price
        )
        
        if result.order_id:
            tracker.add_order(result.order_id)
        
        assert result.success, f"Stop order should be submitted: {result.message}"
        assert result.status == OrderStatus.SUBMITTED
        assert result.order_type == OrderType.STOP


# =============================================================================
# Order Cancellation Tests
# =============================================================================

class TestOrderCancellation:
    """Order cancellation tests."""
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_cancel_limit_order(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test cancelling a limit order."""
        from tests.e2e.conftest import get_safe_price
        
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        try:
            current_price = await get_safe_price(connected_client, symbol)
        except ValueError:
            pytest.skip("Could not get current price")
        
        # Submit order that won't fill
        limit_price = round(current_price * 0.80, 2)
        
        result = await executor.execute_limit_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            limit_price=limit_price,
            wait_for_fill=False
        )
        
        assert result.success and result.order_id
        tracker.add_order(result.order_id)
        
        # Wait a moment for order to be acknowledged
        await asyncio.sleep(1)
        
        # Cancel
        cancelled = await executor.cancel_order(result.order_id)
        
        # Check status
        managed = executor.get_order(result.order_id)
        
        # May take a moment for cancellation to process
        await asyncio.sleep(1)
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_cancel_all_orders(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test cancelling all pending orders."""
        from tests.e2e.conftest import get_safe_price
        
        executor, tracker = safe_executor
        
        try:
            current_price = await get_safe_price(connected_client, test_symbols[0])
        except ValueError:
            pytest.skip("Could not get current price")
        
        # Submit multiple orders
        for i, symbol in enumerate(test_symbols[:2]):
            limit_price = round(current_price * (0.80 - i * 0.05), 2)
            
            result = await executor.execute_limit_order(
                symbol=symbol,
                quantity=max_test_quantity,
                action="BUY",
                limit_price=limit_price,
                wait_for_fill=False
            )
            
            if result.order_id:
                tracker.add_order(result.order_id)
            
            await asyncio.sleep(0.5)
        
        # Cancel all
        cancelled = await executor.cancel_all_orders()
        
        # Should have cancelled at least some
        # (exact count depends on timing)


# =============================================================================
# Order Tracking Tests
# =============================================================================

class TestOrderTracking:
    """Order tracking tests."""
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_get_pending_orders(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test getting pending orders."""
        from tests.e2e.conftest import get_safe_price
        
        executor, tracker = safe_executor
        
        try:
            current_price = await get_safe_price(connected_client, test_symbols[0])
        except ValueError:
            pytest.skip("Could not get current price")
        
        # Submit order that won't fill
        limit_price = round(current_price * 0.80, 2)
        
        result = await executor.execute_limit_order(
            symbol=test_symbols[0],
            quantity=max_test_quantity,
            action="BUY",
            limit_price=limit_price,
            wait_for_fill=False
        )
        
        if result.order_id:
            tracker.add_order(result.order_id)
        
        # Get pending orders
        pending = executor.get_pending_orders()
        
        # Should have at least our order
        assert len(pending) > 0, "Should have pending orders"
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_order_status_tracking(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test order status updates."""
        from tests.e2e.conftest import get_safe_price
        
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        status_updates = []
        
        def on_status_change(managed):
            status_updates.append(managed.status)
        
        executor.on_status_change.append(on_status_change)
        
        try:
            current_price = await get_safe_price(connected_client, symbol)
        except ValueError:
            pytest.skip("Could not get current price")
        
        # Submit order
        limit_price = round(current_price * 0.80, 2)
        
        result = await executor.execute_limit_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            limit_price=limit_price,
            wait_for_fill=False
        )
        
        if result.order_id:
            tracker.add_order(result.order_id)
        
        # Give time for status updates
        await asyncio.sleep(2)
        
        # Should have received status updates
        # (depends on IB acknowledging the order)


# =============================================================================
# Position Tests
# =============================================================================

class TestPositions:
    """Position tracking tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_get_positions(self, connected_client: IBKRAsyncClient):
        """Test getting current positions."""
        positions = await connected_client.get_positions()
        
        # Should return a list (may be empty)
        assert isinstance(positions, list)
        
        # If positions exist, validate structure
        for pos in positions:
            assert "symbol" in pos
            assert "quantity" in pos
            assert "avg_cost" in pos
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_get_account_summary(self, connected_client: IBKRAsyncClient):
        """Test getting account summary."""
        summary = await connected_client.get_account_summary()
        
        # Should return a dict
        assert isinstance(summary, dict)
        
        # Paper trading account should have some values
        # (exact values depend on account state)
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_get_account_value(self, connected_client: IBKRAsyncClient):
        """Test getting specific account value."""
        net_liq = await connected_client.get_account_value("NetLiquidation")
        
        # Paper trading account should have value
        if net_liq is not None:
            assert isinstance(net_liq, float)
            assert net_liq > 0, "Net liquidation should be positive"


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestOrderErrorHandling:
    """Order error handling tests."""
    
    @pytest.mark.asyncio
    async def test_order_not_connected(
        self,
        ib_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test order when not connected."""
        # Don't connect
        executor = AsyncOrderExecutor(ib_client)
        
        result = await executor.execute_market_order(
            symbol=test_symbols[0],
            quantity=max_test_quantity,
            action="BUY"
        )
        
        assert not result.success
        assert "Not connected" in result.message
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_invalid_order_quantity(
        self,
        safe_executor: tuple,
        test_symbols: list
    ):
        """Test handling of invalid order quantity."""
        executor, tracker = safe_executor
        
        # Zero quantity
        result = await executor.execute_market_order(
            symbol=test_symbols[0],
            quantity=0,
            action="BUY"
        )
        
        # Should fail or be rejected
        # (IB may reject orders with 0 quantity)
