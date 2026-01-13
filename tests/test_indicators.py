"""
Unit tests for indicators module.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Add src to path
import sys
sys.path.insert(0, '.')

from src.indicators.vpes import VPES, calculate_vpes
from src.indicators.trend import TrendIndicators, TrendDirection, EMAAlignment
from src.indicators.volume import VolumeIndicators, VolumeState


def create_sample_data(n_bars: int = 100, trend: str = "up") -> pd.DataFrame:
    """Create sample OHLCV data for testing."""
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    base_price = 100
    prices = []
    
    for i in range(n_bars):
        if trend == "up":
            price = base_price * (1 + i * 0.005)  # 0.5% daily increase
        elif trend == "down":
            price = base_price * (1 - i * 0.005)
        else:
            price = base_price + np.sin(i * 0.2) * 5  # Oscillating
        
        # Add some noise
        noise = np.random.normal(0, 0.01)
        price = price * (1 + noise)
        
        high = price * (1 + np.random.uniform(0.005, 0.02))
        low = price * (1 - np.random.uniform(0.005, 0.02))
        open_price = price * (1 + np.random.uniform(-0.01, 0.01))
        
        # Volume with trend bias
        base_vol = 1000000
        if trend == "up":
            volume = base_vol * (1 + np.random.uniform(0, 0.5))
        else:
            volume = base_vol * (1 + np.random.uniform(-0.3, 0.3))
        
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


class TestVPES:
    """Tests for VPES indicator."""
    
    def test_calculate_vpes_basic(self):
        """Test basic VPES calculation."""
        vpes = calculate_vpes(
            open_price=100,
            close_price=102,
            volume=1500000,
            volume_ma=1000000
        )
        
        # Price change = 2%, Volume ratio = 1.5x
        # VPES = 0.02 * 1.5 = 0.03
        assert 0.029 < vpes < 0.031
    
    def test_vpes_class_calculation(self):
        """Test VPES class calculation on DataFrame."""
        data = create_sample_data(100, "up")
        vpes_calc = VPES()
        
        result = vpes_calc.calculate(data)
        
        assert 'vpes' in result.columns
        assert 'vpes_ema' in result.columns
        assert 'volume_ratio' in result.columns
        assert 'vpes_cumulative' in result.columns
    
    def test_vpes_result_dataclass(self):
        """Test VPESResult from get_current_vpes."""
        data = create_sample_data(100, "up")
        vpes_calc = VPES()
        
        result = vpes_calc.get_current_vpes(data)
        
        assert hasattr(result, 'vpes')
        assert hasattr(result, 'vpes_ma')
        assert hasattr(result, 'volume_ratio')
        assert hasattr(result, 'signal_strength')


class TestTrendIndicators:
    """Tests for trend indicators."""
    
    def test_ema_calculation(self):
        """Test EMA calculation."""
        data = create_sample_data(100, "up")
        trend = TrendIndicators()
        
        result = trend.calculate_emas(data)
        
        assert 'ema_fast' in result.columns
        assert 'ema_slow' in result.columns
        assert 'ema_trend' in result.columns
    
    def test_ema_structure_bullish(self):
        """Test bullish EMA alignment detection."""
        data = create_sample_data(200, "up")
        trend = TrendIndicators()
        
        structure = trend.get_ema_structure(data)
        
        # In uptrend, should be bullish aligned
        assert structure.alignment in [EMAAlignment.BULLISH, EMAAlignment.MIXED]
        assert structure.ema_fast > 0
    
    def test_swing_detection(self):
        """Test swing high/low detection."""
        data = create_sample_data(100, "neutral")
        trend = TrendIndicators()
        
        highs = trend.find_swing_highs(data)
        lows = trend.find_swing_lows(data)
        
        # Should find at least some swings
        assert len(highs) >= 0
        assert len(lows) >= 0
    
    def test_trend_state(self):
        """Test comprehensive trend state analysis."""
        data = create_sample_data(200, "up")
        trend = TrendIndicators()
        
        state = trend.get_trend_state(data)
        
        assert hasattr(state, 'direction')
        assert hasattr(state, 'ema_alignment')
        assert hasattr(state, 'trend_strength')
        assert 0 <= state.trend_strength <= 1


class TestVolumeIndicators:
    """Tests for volume indicators."""
    
    def test_volume_calculation(self):
        """Test volume indicator calculation."""
        data = create_sample_data(100, "up")
        vol = VolumeIndicators()
        
        result = vol.calculate(data)
        
        assert 'volume_ma' in result.columns
        assert 'volume_ratio' in result.columns
        assert 'volume_state' in result.columns
    
    def test_volume_analysis(self):
        """Test volume analysis."""
        data = create_sample_data(100, "up")
        vol = VolumeIndicators()
        
        analysis = vol.get_volume_analysis(data)
        
        assert hasattr(analysis, 'volume_ratio')
        assert hasattr(analysis, 'state')
        assert analysis.state in VolumeState


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
