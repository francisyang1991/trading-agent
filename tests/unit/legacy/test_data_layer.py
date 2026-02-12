#!/usr/bin/env python3
"""
Data Layer Testing Script
Test IBKR data connections and historical data loading.
"""

import asyncio
import sys
import os
sys.path.insert(0, '.')

from src.data.ibkr_client import IBKRClient
from src.data.ibkr_web_client import IBKRWebClient, IBeamManager
from src.data.data_manager import BacktestDataManager
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def test_historical_data_loading():
    """Test loading historical data from CSV files."""
    print("🧪 Testing Historical Data Loading")

    # Create data manager
    data_manager = BacktestDataManager(data_dir="data/historical")

    # Create sample AAPL data (simulated)
    sample_data = create_sample_aapl_data()

    # Save to CSV
    os.makedirs("data/historical", exist_ok=True)
    sample_data.to_csv("data/historical/AAPL.csv", index=True)

    # Test loading
    success = data_manager.load_historical_data("AAPL", "data/historical/AAPL.csv", "1 day")

    if success:
        print("✅ Historical data loaded successfully")
        loaded_data = data_manager.get_data("AAPL", "1 day", lookback_bars=5)
        print(f"   Loaded {len(loaded_data)} bars")
        print(f"   Sample data: {loaded_data.head(2).to_dict('records')}")
    else:
        print("❌ Failed to load historical data")

    return success


def create_sample_aapl_data():
    """Create sample AAPL data for testing."""
    dates = pd.date_range(end=datetime.now(), periods=100, freq='D')
    data = []

    base_price = 150
    for i, date in enumerate(dates):
        # Simulate trending price
        price = base_price * (1 + i * 0.001) + np.random.normal(0, 2)
        high = price * (1 + np.random.uniform(0.005, 0.015))
        low = price * (1 - np.random.uniform(0.005, 0.015))
        open_price = price * (1 + np.random.uniform(-0.005, 0.005))
        volume = 50000000 + np.random.randint(-10000000, 10000000)

        data.append({
            'date': date,
            'open': open_price,
            'high': high,
            'low': low,
            'close': price,
            'volume': volume
        })

    df = pd.DataFrame(data)
    return df


async def test_ibeam_connection():
    """Test IBeam Web API connection (requires running IBeam)."""
    print("\n🧪 Testing IBeam Web API Connection")

    try:
        async with IBKRWebClient() as client:
            if client.is_connected:
                print("✅ Connected to IBeam Gateway")

                # Test basic operations
                accounts = await client.get_accounts()
                print(f"   Accounts found: {len(accounts)}")

                # Try to get AAPL contract
                conid = await client.get_conid("AAPL")
                if conid:
                    print(f"   AAPL contract ID: {conid}")

                    # Try historical data
                    hist_data = await client.get_historical_data(conid, period="1d", bar="1d")
                    if not hist_data.empty:
                        print(f"   Historical data: {len(hist_data)} bars")
                        print(f"   Latest close: ${hist_data['close'].iloc[-1]:.2f}")
                    else:
                        print("   No historical data returned")
                else:
                    print("   Could not find AAPL contract")

                return True
            else:
                print("❌ Not connected to IBeam Gateway")
                print("   Make sure IBeam is running:")
                print("   docker compose up -d")
                return False

    except Exception as e:
        print(f"❌ IBeam test failed: {e}")
        return False


def test_tws_connection():
    """Test TWS/Gateway connection (requires running TWS)."""
    print("\n🧪 Testing TWS/Gateway Connection")

    try:
        client = IBKRClient(port=7497)  # Paper trading port

        if client.connect():
            print("✅ Connected to TWS/Gateway")

            # Test basic data fetch
            data = client.get_historical_data("AAPL", "1 day", duration="5 D")

            if not data.empty:
                print(f"   Historical data: {len(data)} bars")
                print(f"   Latest close: ${data['close'].iloc[-1]:.2f}")
                client.disconnect()
                return True
            else:
                print("   No data returned")
                client.disconnect()
                return False
        else:
            print("❌ Cannot connect to TWS/Gateway")
            print("   Make sure TWS or Gateway is running on port 7497")
            return False

    except Exception as e:
        print(f"❌ TWS test failed: {e}")
        return False


async def test_data_manager():
    """Test data manager multi-timeframe aggregation."""
    print("\n🧪 Testing Data Manager")

    try:
        # Create data manager with backtest mode
        data_manager = BacktestDataManager()

        # Load sample data
        sample_data = create_sample_aapl_data()
        os.makedirs("data/historical", exist_ok=True)
        sample_data.to_csv("data/historical/AAPL.csv", index=True)

        success = data_manager.load_historical_data("AAPL", "data/historical/AAPL.csv", "1 day")

        if success:
            print("✅ Data loaded")

            # Test multi-timeframe
            mtf_data = data_manager.get_multi_timeframe_data(
                "AAPL",
                ["1 day", "5 mins"],  # Note: 5 mins would need more granular data
                lookback_bars=10
            )

            print(f"   Multi-timeframe data: {list(mtf_data.keys())}")

            for tf, data in mtf_data.items():
                if not data.empty:
                    print(f"   {tf}: {len(data)} bars")
                else:
                    print(f"   {tf}: No data")

            return True
        else:
            print("❌ Data loading failed")
            return False

    except Exception as e:
        print(f"❌ Data manager test failed: {e}")
        return False


def main():
    """Run all data layer tests."""
    print("🚀 SAIYAN Trading Agent - Data Layer Tests")
    print("="*50)

    # Test 1: Historical data loading
    test1_result = test_historical_data_loading()

    # Test 2: Data manager
    test2_result = asyncio.run(test_data_manager())

    # Test 3: IBeam Web API (optional)
    test3_result = asyncio.run(test_ibeam_connection())

    # Test 4: TWS/Gateway API (optional)
    test4_result = test_tws_connection()

    print("\n" + "="*50)
    print("📊 Test Results:")
    print(f"   Historical Data Loading: {'✅ PASS' if test1_result else '❌ FAIL'}")
    print(f"   Data Manager: {'✅ PASS' if test2_result else '❌ FAIL'}")
    print(f"   IBeam Web API: {'✅ PASS' if test3_result else '❌ FAIL'} (requires IBeam)")
    print(f"   TWS/Gateway API: {'✅ PASS' if test4_result else '❌ FAIL'} (requires TWS)")

    # Core functionality test
    core_pass = test1_result and test2_result
    print(f"\n🎯 Core Data Layer: {'✅ READY' if core_pass else '❌ NEEDS FIXING'}")

    if core_pass:
        print("\n✅ Data layer tests passed! Ready to test indicators next.")
        print("   Run: python test_indicators.py")
    else:
        print("\n❌ Fix data layer issues before proceeding.")


if __name__ == "__main__":
    import numpy as np
    main()
