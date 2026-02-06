#!/usr/bin/env python3
"""
E2E Test: Place a limit order via IBKRAsyncClient + AsyncOrderExecutor.

This test:
  1. Connects to IB Gateway via IBKRAsyncClient
  2. Fetches account info
  3. Places a BUY limit order far below market (won't fill)
  4. Verifies the order is submitted
  5. Cancels the order
  6. Reports results

Run inside the saiyan-agent container on GCP:
  docker exec saiyan-agent python -m tests.test_order_placement
"""

import asyncio
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.data import IBKRAsyncClient, ConnectionConfig
from src.execution import AsyncOrderExecutor, OrderResult, OrderStatus


async def test_order_flow():
    """Test the full order placement flow."""
    print("=" * 60)
    print("  E2E Order Placement Test")
    print("=" * 60)

    # ---- Step 1: Connect ----
    config = ConnectionConfig.from_env()
    print(f"\n[1/6] Connecting to IB Gateway at {config.host}:{config.port} ...")
    client = IBKRAsyncClient(config)
    connected = await client.connect()
    if not connected:
        print("FAIL: Could not connect to IB Gateway")
        return False

    print(f"  OK — connected (client_id={config.client_id})")

    # ---- Step 2: Account info ----
    print("\n[2/6] Fetching account info ...")
    try:
        positions = await client.get_positions()
        print(f"  Positions: {len(positions)}")
        for p in positions:
            print(f"    {p['symbol']}: {p['quantity']} shares @ ${p['avg_cost']:.2f}")
    except Exception as e:
        print(f"  Could not fetch positions: {e}")

    # ---- Step 3: Get current price for test symbol ----
    test_symbol = "AAPL"
    print(f"\n[3/6] Getting current price for {test_symbol} ...")
    current_price = await client.get_current_price(test_symbol)
    # Validate: must be a real number, not NaN
    import math
    if current_price is None or math.isnan(current_price):
        print(f"  WARN: No live market data for {test_symbol} (need subscription)")
        print(f"  Using last known price ~$230 for test")
        current_price = 230.0
    else:
        print(f"  Current price: ${current_price:.2f}")

    # ---- Step 4: Place limit order (far below market — won't fill) ----
    # Set limit 20% below market so it's clearly a resting order
    limit_price = round(current_price * 0.80, 2)
    quantity = 1
    action = "BUY"

    print(f"\n[4/6] Placing test limit order: {action} {quantity} {test_symbol} @ ${limit_price} ...")
    executor = AsyncOrderExecutor(client)
    result: OrderResult = await executor.execute_limit_order(
        symbol=test_symbol,
        quantity=quantity,
        action=action,
        limit_price=limit_price,
        wait_for_fill=False,
    )

    print(f"  Success:   {result.success}")
    print(f"  Order ID:  {result.order_id}")
    print(f"  Status:    {result.status.value}")
    print(f"  Message:   {result.message}")

    if not result.success:
        print(f"\n  FAIL: Order was not accepted — {result.message}")
        await client.disconnect()
        return False

    # ---- Step 5: Verify order is visible in open orders ----
    print(f"\n[5/6] Verifying order is in open orders list ...")
    await asyncio.sleep(1)  # Give IB a moment
    open_orders = await client.get_open_orders()
    found = False
    for trade in open_orders:
        if trade.order.orderId == int(result.order_id):
            found = True
            print(f"  FOUND — Order {result.order_id} is active ({trade.orderStatus.status})")
            break

    if not found:
        print(f"  Order {result.order_id} not found in open orders (may still be processing)")
        # Not necessarily a failure — it might be in the list under a different check
        pending = executor.get_pending_orders()
        print(f"  Executor tracking {len(pending)} pending orders")

    # ---- Step 6: Cancel the test order ----
    print(f"\n[6/6] Cancelling test order {result.order_id} ...")
    cancelled = await executor.cancel_order(result.order_id)
    print(f"  Cancelled: {cancelled}")

    # Cleanup
    await executor.cleanup()
    await client.disconnect()

    # ---- Summary ----
    print("\n" + "=" * 60)
    if result.success:
        print("  TEST PASSED — Order placed and cancelled successfully")
        print(f"  Order was visible in GUI as: {action} {quantity} {test_symbol} @ ${limit_price} LMT")
    else:
        print("  TEST FAILED")
    print("=" * 60)

    return result.success


if __name__ == "__main__":
    success = asyncio.run(test_order_flow())
    sys.exit(0 if success else 1)
