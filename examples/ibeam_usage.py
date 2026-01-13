#!/usr/bin/env python3
"""
Example: Using IBeam with IBKR Client Portal Web API

IBeam (https://github.com/Voyz/ibeam) handles authentication to IBKR's
Client Portal Web API Gateway, enabling headless automated trading.

Prerequisites:
1. Docker installed
2. IBeam container running with your IBKR credentials:
   docker run -d --name ibeam \
       --env IBEAM_ACCOUNT=your_username \
       --env IBEAM_PASSWORD=your_password \
       -p 5000:5000 voyz/ibeam

3. Wait ~30 seconds for IBeam to authenticate
"""

import asyncio
import sys
sys.path.insert(0, '..')

from src.data.ibkr_web_client import IBKRWebClient, IBeamManager


async def check_auth_status():
    """Check if IBeam Gateway is authenticated."""
    print("Checking IBeam Gateway status...")
    
    async with IBKRWebClient() as client:
        if client.is_connected:
            print("✅ Gateway authenticated!")
            return True
        else:
            print("❌ Gateway not authenticated. Is IBeam running?")
            return False


async def get_market_data_example():
    """Example: Get market data for a stock."""
    async with IBKRWebClient() as client:
        if not client.is_connected:
            print("Not connected")
            return
        
        # Search for Apple stock
        print("\nSearching for AAPL...")
        results = await client.search_stocks("AAPL")
        
        if results:
            conid = results[0].get('conid')
            print(f"Found AAPL with conid: {conid}")
            
            # Get historical data
            print("\nFetching historical data...")
            df = await client.get_historical_data(
                conid=conid,
                period="1m",  # 1 month
                bar="1d"      # Daily bars
            )
            
            if not df.empty:
                print(f"\nReceived {len(df)} bars:")
                print(df.tail())
            else:
                print("No data returned")


async def get_account_info_example():
    """Example: Get account information."""
    async with IBKRWebClient() as client:
        if not client.is_connected:
            print("Not connected")
            return
        
        # Get accounts
        accounts = await client.get_accounts()
        print(f"\nAccounts: {accounts}")
        
        if accounts:
            account_id = accounts[0].get('accountId')
            
            # Get positions
            positions = await client.get_positions(account_id)
            print(f"\nPositions ({len(positions)}):")
            for pos in positions[:5]:
                print(f"  {pos.get('contractDesc')}: {pos.get('position')} shares")
            
            # Get account summary
            summary = await client.get_account_summary(account_id)
            print(f"\nAccount Summary:")
            print(f"  Net Liquidation: ${summary.get('netliquidation', {}).get('amount', 'N/A')}")


async def place_order_example():
    """Example: Place a market order (PAPER TRADING ONLY!)."""
    async with IBKRWebClient() as client:
        if not client.is_connected:
            print("Not connected")
            return
        
        print("\n⚠️  ORDER EXAMPLE - Uncomment to actually place order")
        print("This would place a market order for 1 share of AAPL")
        
        # UNCOMMENT BELOW TO ACTUALLY PLACE ORDER (PAPER TRADING ONLY!)
        # response = await client.place_market_order(
        #     symbol="AAPL",
        #     quantity=1,
        #     side="BUY"
        # )
        # print(f"Order response: {response}")


async def keep_session_alive():
    """Example: Keep session alive with periodic tickle."""
    async with IBKRWebClient() as client:
        if not client.is_connected:
            print("Not connected")
            return
        
        print("\nKeeping session alive (tickle every 60 seconds)...")
        
        for i in range(5):
            result = await client.tickle()
            print(f"Tickle {i+1}: session={result.get('session')}")
            await asyncio.sleep(60)


def print_docker_instructions():
    """Print IBeam Docker setup instructions."""
    print("""
╔══════════════════════════════════════════════════════════════════╗
║                    IBeam Setup Instructions                       ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                   ║
║  1. Start IBeam with Docker:                                     ║
║                                                                   ║
║     docker run -d --name ibeam \\                                 ║
║         --env IBEAM_ACCOUNT=your_username \\                      ║
║         --env IBEAM_PASSWORD=your_password \\                     ║
║         -p 5000:5000 voyz/ibeam                                  ║
║                                                                   ║
║  2. Or use Docker Compose:                                       ║
║                                                                   ║
║     cp env.example env.list                                      ║
║     # Edit env.list with your credentials                        ║
║     docker compose up -d                                          ║
║                                                                   ║
║  3. Wait 30-60 seconds for authentication                        ║
║                                                                   ║
║  4. Verify:                                                       ║
║     curl -k https://localhost:5000/v1/api/iserver/auth/status    ║
║                                                                   ║
║  For Two-Factor Authentication, see:                             ║
║  https://github.com/Voyz/ibeam#two-factor-authentication         ║
║                                                                   ║
╚══════════════════════════════════════════════════════════════════╝
""")


async def main():
    """Run all examples."""
    print_docker_instructions()
    
    # Check if Gateway is running
    is_connected = await check_auth_status()
    
    if not is_connected:
        print("\nStart IBeam first, then run this example again.")
        return
    
    # Run examples
    await get_account_info_example()
    await get_market_data_example()
    await place_order_example()


if __name__ == "__main__":
    asyncio.run(main())
