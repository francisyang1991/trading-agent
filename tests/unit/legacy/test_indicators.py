#!/usr/bin/env python3
"""
Indicators Testing Script
Test VPES, trend, and volume indicators.
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime
sys.path.insert(0, '.')

from src.indicators.vpes import VPES, VPESAnalyzer
from src.indicators.trend import TrendIndicators, TrendDirection, EMAAlignment
from src.indicators.volume import VolumeIndicators, VolumeState


def create_sample_data(n_bars=100, trend="up"):
    """Create sample OHLCV data for testing."""
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')

    base_price = 100
    prices = []

    for i in range(n_bars):
        if trend == "up":
            # Uptrend with some volatility
            price = base_price * (1 + i * 0.005) + np.random.normal(0, 1)
        elif trend == "down":
            # Downtrend
            price = base_price * (1 - i * 0.005) + np.random.normal(0, 1)
        else:
            # Sideways
            price = base_price + np.sin(i * 0.2) * 5 + np.random.normal(0, 1)

        # Create OHLCV
        high = price * (1 + np.random.uniform(0.005, 0.02))
        low = price * (1 - np.random.uniform(0.005, 0.02))
        open_price = price * (1 + np.random.uniform(-0.01, 0.01))
        volume = 1000000 + np.random.randint(-200000, 200000)

        prices.append({
            'datetime': dates[i],
            'open': open_price,
            'high': high,
            'low': low,
            'close': price,
            'volume': volume
        })

    df = pd.DataFrame(prices)
    df = df.set_index('datetime')
    return df


def test_vpes_indicator():
    """Test VPES (Volume-Price Expansion Score) indicator."""
    print("🧪 Testing VPES Indicator")

    # Create trending data
    up_data = create_sample_data(50, "up")

    # Initialize VPES
    vpes_calc = VPES()

    # Calculate VPES
    result = vpes_calc.calculate(up_data)

    # Check results
    required_cols = ['vpes', 'vpes_ema', 'volume_ratio', 'vpes_cumulative', 'vpes_signal']
    has_all_cols = all(col in result.columns for col in required_cols)

    if has_all_cols:
        print("✅ VPES calculation successful")
        print(f"   Columns added: {len(result.columns)} total")

        # Show sample values
        latest = result.iloc[-1]
        print(".4f")
        print(".3f")
        print(".3f")
        print(f"   VPES Signal: {latest['vpes_signal']}")

        # Test VPES analyzer
        analyzer = VPESAnalyzer(vpes_calc)

        # Test accumulation detection
        is_accumulating, confidence = analyzer.detect_accumulation(result, lookback=20)
        print(f"   Accumulation detected: {is_accumulating} (confidence: {confidence:.2f})")

        # Test current VPES reading
        current_vpes = vpes_calc.get_current_vpes(result)
        print(f"   Current VPES strength: {current_vpes.signal_strength}")

        return True
    else:
        print("❌ VPES calculation failed - missing columns")
        missing = [col for col in required_cols if col not in result.columns]
        print(f"   Missing: {missing}")
        return False


def test_trend_indicators():
    """Test trend indicators (EMA, swing points)."""
    print("\n🧪 Testing Trend Indicators")

    # Create uptrend data
    up_data = create_sample_data(100, "up")

    # Initialize trend indicators
    trend = TrendIndicators()

    # Test EMA calculation
    ema_data = trend.calculate_emas(up_data)

    ema_cols = ['ema_fast', 'ema_slow', 'ema_trend']
    has_ema_cols = all(col in ema_data.columns for col in ema_cols)

    if has_ema_cols:
        print("✅ EMA calculation successful")

        # Test EMA structure analysis
        ema_struct = trend.get_ema_structure(ema_data)
        print(f"   EMA Alignment: {ema_struct.alignment.value}")
        print(".2f")

        # Test trend state
        trend_state = trend.get_trend_state(ema_data)
        print(f"   Trend Direction: {trend_state.direction.value}")
        print(".2f")
        print(f"   Higher Highs: {trend_state.higher_highs}")
        print(f"   Higher Lows: {trend_state.higher_lows}")

        # Test swing point detection
        swing_highs = trend.find_swing_highs(ema_data, lookback=3)
        swing_lows = trend.find_swing_lows(ema_data, lookback=3)
        print(f"   Swing Highs found: {len(swing_highs)}")
        print(f"   Swing Lows found: {len(swing_lows)}")

        return True
    else:
        print("❌ EMA calculation failed")
        return False


def test_volume_indicators():
    """Test volume indicators."""
    print("\n🧪 Testing Volume Indicators")

    # Create data with varying volume
    data = create_sample_data(50, "up")

    # Make volume more interesting - add some spikes
    data['volume'] = data['volume'] * (1 + np.random.exponential(0.5, len(data)))

    # Initialize volume indicators
    vol = VolumeIndicators()

    # Calculate volume indicators
    result = vol.calculate(data)

    vol_cols = ['volume_ma', 'volume_ratio', 'volume_state', 'obv', 'vpt', 'ad_line']
    has_vol_cols = all(col in result.columns for col in vol_cols)

    if has_vol_cols:
        print("✅ Volume calculation successful")

        # Test volume analysis
        analysis = vol.get_volume_analysis(result)
        print(f"   Current Volume Ratio: {analysis.volume_ratio:.2f}")
        print(f"   Volume State: {analysis.state.value}")
        print(f"   Volume Trend: {analysis.trend_volume}")

        # Test volume climax detection
        has_climax, climax_type = vol.detect_volume_climax(result)
        print(f"   Volume Climax: {has_climax} ({climax_type if has_climax else 'none'})")

        # Test breakout confirmation
        is_confirming = vol.is_volume_confirming_breakout(result)
        print(f"   Breakout Confirmation: {is_confirming}")

        return True
    else:
        print("❌ Volume calculation failed")
        missing = [col for col in vol_cols if col not in result.columns]
        print(f"   Missing: {missing}")
        return False


def test_indicator_integration():
    """Test indicators working together."""
    print("\n🧪 Testing Indicator Integration")

    try:
        # Create comprehensive data
        data = create_sample_data(100, "up")

        # Apply all indicators
        vpes_calc = VPES()
        trend = TrendIndicators()
        volume = VolumeIndicators()

        # Calculate step by step
        data_with_emas = trend.calculate_emas(data)
        data_with_volume = volume.calculate(data_with_emas)
        data_with_vpes = vpes_calc.calculate(data_with_volume)

        # Check we have all indicators
        all_indicators = [
            'ema_fast', 'ema_slow', 'ema_trend',  # Trend
            'volume_ma', 'volume_ratio', 'obv',   # Volume
            'vpes', 'vpes_ema', 'vpes_cumulative'  # VPES
        ]

        has_all = all(col in data_with_vpes.columns for col in all_indicators)

        if has_all:
            print("✅ All indicators integrated successfully")
            print(f"   Total columns: {len(data_with_vpes.columns)}")
            print(f"   Indicator columns: {len(all_indicators)}")
            print(f"   Original OHLCV: 5")

            # Show final result
            latest = data_with_vpes.iloc[-1]
            print("\n   Latest Values:")
            print(".4f")
            print(".2f")
            print(".3f")
            print(f"   Volume Ratio: {latest['volume_ratio']:.2f}")

            return True
        else:
            print("❌ Indicator integration failed")
            missing = [col for col in all_indicators if col not in data_with_vpes.columns]
            print(f"   Missing indicators: {missing}")
            return False

    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        return False


def main():
    """Run all indicator tests."""
    print("🚀 SAIYAN Trading Agent - Indicator Tests")
    print("="*50)

    # Test 1: VPES
    test1_result = test_vpes_indicator()

    # Test 2: Trend
    test2_result = test_trend_indicators()

    # Test 3: Volume
    test3_result = test_volume_indicators()

    # Test 4: Integration
    test4_result = test_indicator_integration()

    print("\n" + "="*50)
    print("📊 Test Results:")
    print(f"   VPES Indicator: {'✅ PASS' if test1_result else '❌ FAIL'}")
    print(f"   Trend Indicators: {'✅ PASS' if test2_result else '❌ FAIL'}")
    print(f"   Volume Indicators: {'✅ PASS' if test3_result else '❌ FAIL'}")
    print(f"   Integration: {'✅ PASS' if test4_result else '❌ FAIL'}")

    # Overall result
    all_pass = test1_result and test2_result and test3_result and test4_result
    print(f"\n🎯 Indicators Module: {'✅ READY' if all_pass else '❌ NEEDS FIXING'}")

    if all_pass:
        print("\n✅ All indicators working! Ready to test classification.")
        print("   Run: python test_classification.py")
    else:
        print("\n❌ Fix indicator issues before proceeding.")


if __name__ == "__main__":
    main()
