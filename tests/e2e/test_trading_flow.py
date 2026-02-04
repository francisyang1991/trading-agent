"""
Full Trading Flow Tests for IB Gateway Paper Trading.

Tests complete trading workflows:
- Buy and sell round trip
- Signal to execution flow
- Multi-step trade management

IMPORTANT: All tests use paper trading account only.

Run with: pytest tests/e2e/test_trading_flow.py -v
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
# Round Trip Tests
# =============================================================================

class TestRoundTrip:
    """Complete buy-sell round trip tests."""
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_full_buy_sell_cycle(
        self,
        safe_executor: tuple,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test complete buy-sell round trip."""
        executor, tracker = safe_executor
        symbol = test_symbols[0]  # SPY
        
        # Step 1: Buy
        buy_result = await executor.execute_market_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            timeout=60.0
        )
        
        if buy_result.order_id:
            tracker.add_order(buy_result.order_id)
        
        assert buy_result is not None, "Buy order should return result"
        
        # During market hours, should fill
        if buy_result.success and buy_result.status == OrderStatus.FILLED:
            buy_price = buy_result.filled_price
            
            # Wait a moment
            await asyncio.sleep(2)
            
            # Step 2: Sell
            sell_result = await executor.execute_market_order(
                symbol=symbol,
                quantity=max_test_quantity,
                action="SELL",
                timeout=60.0
            )
            
            if sell_result.order_id:
                tracker.add_order(sell_result.order_id)
            
            assert sell_result is not None, "Sell order should return result"
            
            if sell_result.success and sell_result.status == OrderStatus.FILLED:
                sell_price = sell_result.filled_price
                
                # Calculate P&L (excluding commissions)
                pnl = (sell_price - buy_price) * max_test_quantity
                
                # Log the round trip
                print(f"\nRound trip completed:")
                print(f"  Buy: {max_test_quantity} {symbol} @ ${buy_price:.2f}")
                print(f"  Sell: {max_test_quantity} {symbol} @ ${sell_price:.2f}")
                print(f"  P&L: ${pnl:.2f}")
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_buy_with_stop_loss(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test buying with a protective stop loss."""
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        # Get current price
        current_price = await connected_client.get_current_price(symbol)
        
        if current_price is None:
            pytest.skip("Could not get current price")
        
        # Step 1: Buy
        buy_result = await executor.execute_market_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            timeout=60.0
        )
        
        if buy_result.order_id:
            tracker.add_order(buy_result.order_id)
        
        if buy_result.success and buy_result.status == OrderStatus.FILLED:
            fill_price = buy_result.filled_price
            
            # Step 2: Place stop loss 5% below entry
            stop_price = round(fill_price * 0.95, 2)
            
            stop_result = await executor.execute_stop_order(
                symbol=symbol,
                quantity=max_test_quantity,
                action="SELL",
                stop_price=stop_price
            )
            
            if stop_result.order_id:
                tracker.add_order(stop_result.order_id)
            
            assert stop_result.success, "Stop order should be submitted"
            
            print(f"\nBuy with stop loss:")
            print(f"  Entry: ${fill_price:.2f}")
            print(f"  Stop: ${stop_price:.2f}")
            print(f"  Risk: {(fill_price - stop_price) / fill_price * 100:.1f}%")


# =============================================================================
# Multi-Step Trade Tests
# =============================================================================

class TestMultiStepTrades:
    """Multi-step trade management tests."""
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_bracket_order_simulation(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test simulating a bracket order (entry + stop + target)."""
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        current_price = await connected_client.get_current_price(symbol)
        
        if current_price is None:
            pytest.skip("Could not get current price")
        
        # Entry limit below market
        entry_price = round(current_price * 0.995, 2)  # 0.5% below
        stop_price = round(entry_price * 0.97, 2)       # 3% below entry
        target_price = round(entry_price * 1.06, 2)     # 6% above entry
        
        # Submit entry order
        entry_result = await executor.execute_limit_order(
            symbol=symbol,
            quantity=max_test_quantity,
            action="BUY",
            limit_price=entry_price,
            wait_for_fill=False
        )
        
        if entry_result.order_id:
            tracker.add_order(entry_result.order_id)
        
        assert entry_result.success, "Entry order should be submitted"
        
        print(f"\nBracket order setup:")
        print(f"  Entry: ${entry_price:.2f}")
        print(f"  Stop: ${stop_price:.2f}")
        print(f"  Target: ${target_price:.2f}")
        print(f"  R:R = 1:{(target_price - entry_price) / (entry_price - stop_price):.1f}")
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.asyncio
    async def test_scale_in_positions(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test scaling into a position with multiple orders."""
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        current_price = await connected_client.get_current_price(symbol)
        
        if current_price is None:
            pytest.skip("Could not get current price")
        
        # Scale-in prices
        prices = [
            round(current_price * 0.99, 2),   # 1% below
            round(current_price * 0.97, 2),   # 3% below
            round(current_price * 0.95, 2),   # 5% below
        ]
        
        orders = []
        for i, price in enumerate(prices):
            result = await executor.execute_limit_order(
                symbol=symbol,
                quantity=max_test_quantity,
                action="BUY",
                limit_price=price,
                wait_for_fill=False
            )
            
            if result.order_id:
                tracker.add_order(result.order_id)
                orders.append(result)
            
            await asyncio.sleep(0.5)
        
        print(f"\nScale-in orders placed:")
        for i, (order, price) in enumerate(zip(orders, prices)):
            print(f"  Level {i+1}: ${price:.2f} - Order ID: {order.order_id}")


# =============================================================================
# Data to Order Flow Tests
# =============================================================================

class TestDataToOrderFlow:
    """Tests combining data fetching with order execution."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_fetch_data_then_analyze(
        self,
        connected_client: IBKRAsyncClient,
        test_symbols: list
    ):
        """Test fetching data for analysis."""
        symbol = test_symbols[0]
        
        # Fetch historical data
        df = await connected_client.get_historical_data(
            symbol=symbol,
            bar_size="1 day",
            duration="30 D"
        )
        
        if df.empty:
            pytest.skip("No data available")
        
        # Simple analysis
        current_price = df['close'].iloc[-1]
        sma_20 = df['close'].tail(20).mean()
        sma_50 = df['close'].tail(50).mean() if len(df) >= 50 else sma_20
        
        # Determine trend
        if current_price > sma_20 > sma_50:
            trend = "UPTREND"
        elif current_price < sma_20 < sma_50:
            trend = "DOWNTREND"
        else:
            trend = "SIDEWAYS"
        
        print(f"\n{symbol} Analysis:")
        print(f"  Current: ${current_price:.2f}")
        print(f"  SMA(20): ${sma_20:.2f}")
        print(f"  SMA(50): ${sma_50:.2f}")
        print(f"  Trend: {trend}")
    
    @pytest.mark.live
    @pytest.mark.order
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_signal_to_order_pipeline(
        self,
        safe_executor: tuple,
        connected_client: IBKRAsyncClient,
        test_symbols: list,
        max_test_quantity: int
    ):
        """Test complete signal-to-order pipeline."""
        executor, tracker = safe_executor
        symbol = test_symbols[0]
        
        # 1. Fetch data
        df = await connected_client.get_historical_data(
            symbol=symbol,
            bar_size="1 day",
            duration="30 D"
        )
        
        if df.empty:
            pytest.skip("No data available")
        
        # 2. Generate simple signal
        current_price = df['close'].iloc[-1]
        sma_20 = df['close'].tail(20).mean()
        
        # Simple mean reversion signal: buy if below SMA
        if current_price < sma_20 * 0.98:  # 2% below SMA
            signal = "BUY"
            entry_price = current_price
            stop_price = round(current_price * 0.95, 2)
            target_price = sma_20
        elif current_price > sma_20 * 1.02:  # 2% above SMA
            signal = "SELL"
            entry_price = current_price
            stop_price = round(current_price * 1.05, 2)
            target_price = sma_20
        else:
            signal = "HOLD"
            entry_price = stop_price = target_price = None
        
        print(f"\n{symbol} Signal:")
        print(f"  Signal: {signal}")
        
        # 3. Execute if signal generated
        if signal in ["BUY", "SELL"]:
            # Use limit order slightly away from market
            if signal == "BUY":
                order_price = round(current_price * 0.999, 2)
            else:
                order_price = round(current_price * 1.001, 2)
            
            result = await executor.execute_limit_order(
                symbol=symbol,
                quantity=max_test_quantity,
                action=signal,
                limit_price=order_price,
                wait_for_fill=False
            )
            
            if result.order_id:
                tracker.add_order(result.order_id)
            
            print(f"  Entry Price: ${entry_price:.2f}")
            print(f"  Stop: ${stop_price:.2f}")
            print(f"  Target: ${target_price:.2f}")
            print(f"  Order: {result.order_id}")


# =============================================================================
# Portfolio Integration Tests
# =============================================================================

class TestPortfolioIntegration:
    """Portfolio integration tests."""
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_check_position_before_trade(
        self,
        connected_client: IBKRAsyncClient,
        test_symbols: list
    ):
        """Test checking existing position before trading."""
        symbol = test_symbols[0]
        
        # Get current positions
        positions = await connected_client.get_positions()
        
        # Check if we have position in symbol
        existing_position = None
        for pos in positions:
            if pos["symbol"] == symbol:
                existing_position = pos
                break
        
        if existing_position:
            print(f"\nExisting position in {symbol}:")
            print(f"  Quantity: {existing_position['quantity']}")
            print(f"  Avg Cost: ${existing_position['avg_cost']:.2f}")
        else:
            print(f"\nNo existing position in {symbol}")
    
    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_check_buying_power(
        self,
        connected_client: IBKRAsyncClient
    ):
        """Test checking available buying power before trade."""
        net_liq = await connected_client.get_account_value("NetLiquidation")
        
        if net_liq:
            print(f"\nAccount Value: ${net_liq:,.2f}")
            
            # Calculate position size (example: 2% of portfolio)
            risk_pct = 0.02
            max_position_value = net_liq * risk_pct
            
            print(f"Max Position (2% risk): ${max_position_value:,.2f}")
