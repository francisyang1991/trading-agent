#!/usr/bin/env python3
"""
IBind WebSocket Example
Real-time market data streaming using IBind WebSocket client.
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.data.ibind_client import IBindWebSocketClient


async def websocket_market_data_example():
    """
    Example: Stream real-time market data for multiple stocks.
    """
    print("🔗 IBind WebSocket Market Data Example")
    print("=" * 50)

    # Initialize WebSocket client
    ws_client = IBindWebSocketClient(
        ibind_account_id=None,  # Auto-detect from IBeam
        host="localhost",       # IBeam host
        port="5000"            # IBeam port
    )

    try:
        # Start the WebSocket client
        print("🚀 Starting WebSocket client...")
        started = await ws_client.start()

        if not started:
            print("❌ Failed to start WebSocket client")
            print("   Make sure IBeam is running with WebSocket support")
            return

        print("✅ WebSocket client started!")

        # Subscribe to market data for multiple stocks
        symbols = ["AAPL", "MSFT", "NVDA", "TSLA"]
        print(f"📡 Subscribing to market data for: {symbols}")

        subscribed = await ws_client.subscribe_market_data(symbols)

        if subscribed:
            print("✅ Successfully subscribed to market data")

            # Stream data for 30 seconds
            print("📊 Streaming market data (30 seconds)...")
            print("   Press Ctrl+C to stop early")
            print()

            import time
            start_time = time.time()
            update_count = 0

            while time.time() - start_time < 30:
                # Get available market data
                data_updates = ws_client.get_market_data()

                # Process updates
                for update in data_updates:
                    update_count += 1
                    conid = update.get('conid', 'Unknown')
                    timestamp = update.get('_updated', 'Unknown')

                    # Extract price fields (IBind field codes)
                    last_price = update.get('31')  # Last price
                    bid_price = update.get('84')   # Bid price
                    ask_price = update.get('85')   # Ask price
                    volume = update.get('77')      # Volume

                    print(f"📈 Conid {conid}: Last=${last_price}, Bid=${bid_price}, Ask=${ask_price}, Vol={volume}")

                # Small delay to prevent busy waiting
                await asyncio.sleep(0.1)

            print(f"\n✅ Received {update_count} market data updates")

        else:
            print("❌ Failed to subscribe to market data")

        # Stop the client
        ws_client.stop()
        print("🔌 WebSocket client stopped")

    except KeyboardInterrupt:
        print("\n⏹️  Stopped by user")
        ws_client.stop()

    except Exception as e:
        print(f"❌ Error: {e}")
        ws_client.stop()


async def websocket_pnl_example():
    """
    Example: Stream real-time P&L updates.
    """
    print("\n💰 IBind WebSocket P&L Example")
    print("=" * 50)

    # Initialize WebSocket client
    ws_client = IBindWebSocketClient(
        ibind_account_id=None,
        host="localhost",
        port="5000"
    )

    try:
        # Start client
        print("🚀 Starting WebSocket client...")
        started = await ws_client.start()

        if not started:
            print("❌ Failed to start WebSocket client")
            return

        print("✅ WebSocket client started!")

        # Subscribe to P&L updates
        print("📊 Subscribing to P&L updates...")
        subscribed = await ws_client.subscribe_pnl()

        if subscribed:
            print("✅ Successfully subscribed to P&L")

            # Stream P&L for 20 seconds
            print("💰 Streaming P&L updates (20 seconds)...")
            print()

            import time
            start_time = time.time()
            pnl_count = 0

            while time.time() - start_time < 20:
                # Get P&L updates
                pnl_updates = ws_client.get_pnl_updates()

                # Process updates
                for update in pnl_updates:
                    pnl_count += 1
                    timestamp = update.get('_updated', 'Unknown')
                    account_id = update.get('account', 'Unknown')

                    # P&L fields
                    daily_pnl = update.get('dpl', 0)      # Daily P&L
                    unrealized_pnl = update.get('upl', 0) # Unrealized P&L
                    realized_pnl = update.get('rpl', 0)   # Realized P&L
                    net_liq = update.get('nl', 0)         # Net Liquidation

                    print(f"💰 Account {account_id}: Daily=${daily_pnl:.2f}, "
                          f"Unrealized=${unrealized_pnl:.2f}, Realized=${realized_pnl:.2f}, "
                          f"NetLiq=${net_liq:.2f}")

                await asyncio.sleep(0.5)  # Check every half second

            print(f"\n✅ Received {pnl_count} P&L updates")

        else:
            print("❌ Failed to subscribe to P&L")

        # Stop client
        ws_client.stop()
        print("🔌 WebSocket client stopped")

    except KeyboardInterrupt:
        print("\n⏹️  Stopped by user")
        ws_client.stop()

    except Exception as e:
        print(f"❌ Error: {e}")
        ws_client.stop()


async def combined_websocket_example():
    """
    Example: Subscribe to both market data and P&L simultaneously.
    """
    print("\n🎯 IBind Combined WebSocket Example")
    print("=" * 50)

    ws_client = IBindWebSocketClient(
        ibind_account_id=None,
        gateway_url="wss://localhost:5000"
    )

    try:
        # Start client
        print("🚀 Starting WebSocket client...")
        started = await ws_client.start()

        if not started:
            print("❌ Failed to start WebSocket client")
            return

        print("✅ WebSocket client started!")

        # Subscribe to both market data and P&L
        print("📡 Setting up subscriptions...")

        # Market data
        market_subscribed = await ws_client.subscribe_market_data(["AAPL", "SPY"])
        print(f"   Market data: {'✅' if market_subscribed else '❌'}")

        # P&L
        pnl_subscribed = await ws_client.subscribe_pnl()
        print(f"   P&L: {'✅' if pnl_subscribed else '❌'}")

        if market_subscribed or pnl_subscribed:
            print("\n📊 Streaming combined data (15 seconds)...")
            print("   Market data (📈) and P&L (💰) updates:")
            print()

            import time
            start_time = time.time()
            total_updates = 0

            while time.time() - start_time < 15:
                # Get market data
                market_data = ws_client.get_market_data()
                for update in market_data:
                    total_updates += 1
                    conid = update.get('conid', 'Unknown')
                    last_price = update.get('31', 'N/A')
                    print(f"📈 Conid {conid}: ${last_price}")

                # Get P&L data
                pnl_data = ws_client.get_pnl_updates()
                for update in pnl_data:
                    total_updates += 1
                    daily_pnl = update.get('dpl', 0)
                    unrealized_pnl = update.get('upl', 0)
                    print(f"💰 Daily P&L: ${daily_pnl:.2f}, Unrealized: ${unrealized_pnl:.2f}")

                await asyncio.sleep(0.2)

            print(f"\n✅ Total updates received: {total_updates}")

        # Show current subscriptions
        subscriptions = ws_client.get_subscriptions()
        print(f"   Active subscriptions: {subscriptions}")

        # Stop client
        ws_client.stop()
        print("🔌 WebSocket client stopped")

    except KeyboardInterrupt:
        print("\n⏹️  Stopped by user")
        ws_client.stop()

    except Exception as e:
        print(f"❌ Error: {e}")
        ws_client.stop()


async def main():
    """Run WebSocket examples."""
    print("🚀 SAIYAN Trading System - IBind WebSocket Examples")
    print("=" * 70)
    print("Real-time data streaming examples using IBind WebSocket client.")
    print("Make sure IBeam is running: docker compose up -d")
    print("=" * 70)

    try:
        # Run examples
        await websocket_market_data_example()
        await websocket_pnl_example()
        await combined_websocket_example()

        print("\n" + "=" * 70)
        print("✅ All WebSocket examples completed!")
        print("\n💡 Tips:")
        print("   - WebSocket connections stay alive automatically")
        print("   - Data is queued internally for thread-safe access")
        print("   - Use empty() method to check for new data")
        print("   - Call stop() to cleanly disconnect")

    except Exception as e:
        print(f"❌ Fatal error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
