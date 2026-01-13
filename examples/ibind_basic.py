#!/usr/bin/env python3
"""
IBind Basic Usage Example
Simple example showing how to use IBind REST client with IBeam authentication.
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.data.ibind_client import IBindRESTClient


async def main():
    """
    Basic IBind example - requires IBeam running.
    """
    print("🔗 IBind Basic Example")
    print("=" * 30)

    # Initialize IBind REST client
    # Uses IBeam authentication (set account_id if needed)
    client = IBindRESTClient(
        ibind_account_id=None,  # Will be auto-detected from IBeam
        host="localhost",       # IBeam host
        port="5000"            # IBeam port
    )

    try:
        # Test authentication
        print("🔐 Authenticating...")
        authenticated = await client.authenticate()

        if not authenticated:
            print("❌ Authentication failed!")
            print("   Make sure IBeam is running:")
            print("   docker compose up -d")
            return

        print("✅ Authentication successful!")

        # Get account information
        print("\n📊 Account Information:")
        accounts = await client.get_accounts()
        print(f"   Accounts found: {len(accounts)}")

        if accounts:
            account_id = accounts[0].get('accountId')
            print(f"   Account ID: {account_id}")

            # Get account summary
            summary = await client.get_account_summary()
            if summary:
                net_liq = summary.get('netliquidation', {}).get('amount')
                print(f"   Net Liquidation: ${net_liq}")

        # Get current positions
        print("\n💼 Current Positions:")
        positions = await client.get_positions()
        print(f"   Open positions: {len(positions)}")

        for pos in positions[:5]:  # Show first 5
            symbol = pos.get('ticker', pos.get('contractDesc', 'Unknown'))
            quantity = pos.get('position', 0)
            avg_cost = pos.get('avgCost', 0)
            print(f"   {symbol}: {quantity} shares @ ${avg_cost:.2f}")

        # Get market data for AAPL
        print("\n📈 Market Data (AAPL):")
        try:
            hist_data = await client.get_historical_data("AAPL", period="1d", bar="1d")

            if not hist_data.empty:
                print(f"   Historical bars: {len(hist_data)}")
                latest = hist_data.iloc[-1]
                print(".2f")
            else:
                print("   No historical data received")

        except Exception as e:
            print(f"   Error getting market data: {e}")

        # Get market snapshot
        print("\n📊 Market Snapshot:")
        try:
            snapshot = await client.get_market_snapshot(["AAPL", "MSFT"])

            for symbol, data in snapshot.items():
                last_price = data.get('31', 'N/A')  # Last price field
                bid = data.get('84', 'N/A')         # Bid price
                ask = data.get('85', 'N/A')         # Ask price
                print(f"   {symbol}: Last=${last_price}, Bid=${bid}, Ask=${ask}")

        except Exception as e:
            print(f"   Error getting snapshot: {e}")

        # Get open orders
        print("\n📋 Open Orders:")
        orders = await client.get_orders()
        print(f"   Open orders: {len(orders)}")

        for order in orders[:3]:  # Show first 3
            order_id = order.get('orderId')
            side = order.get('side')
            quantity = order.get('quantity')
            symbol = order.get('ticker', order.get('contractDesc', 'Unknown'))
            order_type = order.get('orderType')
            status = order.get('status')
            print(f"   Order {order_id}: {side} {quantity} {symbol} ({order_type}) - {status}")

        print("\n✅ IBind basic example completed!")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("   Make sure:")
        print("   1. IBeam is running (docker compose up -d)")
        print("   2. You have internet connection")
        print("   3. Your IBKR account is authenticated")


if __name__ == "__main__":
    asyncio.run(main())
