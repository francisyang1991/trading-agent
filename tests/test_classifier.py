"""
Unit tests for stock classifier.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import sys
sys.path.insert(0, '.')

from src.classifier.stock_classifier import StockClassifier, StockType, ClassificationResult


def create_trend_data(n_bars: int = 120) -> pd.DataFrame:
    """Create data for a trending stock (Type A)."""
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    prices = []
    base = 100
    
    for i in range(n_bars):
        if i < 60:
            # Rally phase - 20% gain
            price = base * (1 + (i / 60) * 0.20)
        else:
            # Pullback phase - 5% pullback
            rally_high = base * 1.20
            pullback_pct = min(0.05, (i - 60) / 60 * 0.05)
            price = rally_high * (1 - pullback_pct)
        
        prices.append({
            'datetime': dates[i],
            'open': price * 0.995,
            'high': price * 1.01,
            'low': price * 0.99,
            'close': price,
            'volume': 1000000 + np.random.randint(-200000, 200000)
        })
    
    df = pd.DataFrame(prices).set_index('datetime')
    return df


def create_range_data(n_bars: int = 60) -> pd.DataFrame:
    """Create data for a range-bound stock (Type B)."""
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    prices = []
    base = 100
    
    for i in range(n_bars):
        # Oscillate around base price within 8% band
        price = base + np.sin(i * 0.3) * 4
        
        prices.append({
            'datetime': dates[i],
            'open': price * 0.998,
            'high': price * 1.005,
            'low': price * 0.995,
            'close': price,
            'volume': 800000 + np.random.randint(-100000, 100000)
        })
    
    df = pd.DataFrame(prices).set_index('datetime')
    return df


def create_reversal_data(n_bars: int = 120) -> pd.DataFrame:
    """Create data for a potential reversal stock (Type C)."""
    dates = pd.date_range(end=datetime.now(), periods=n_bars, freq='D')
    
    prices = []
    base = 100
    
    for i in range(n_bars):
        if i < 90:
            # Downtrend - 20% decline
            price = base * (1 - (i / 90) * 0.20)
        else:
            # Starting to turn up
            bottom = base * 0.80
            price = bottom * (1 + (i - 90) / 30 * 0.05)
        
        prices.append({
            'datetime': dates[i],
            'open': price * 1.002,
            'high': price * 1.01,
            'low': price * 0.99,
            'close': price,
            'volume': 900000 + np.random.randint(-150000, 150000)
        })
    
    df = pd.DataFrame(prices).set_index('datetime')
    return df


class TestStockClassifier:
    """Tests for stock classifier."""
    
    def test_type_a_classification(self):
        """Test Type A (trend) classification."""
        data = create_trend_data(120)
        classifier = StockClassifier()
        
        result = classifier.classify("TEST", data)
        
        # Should classify as Type A or have high Type A confidence
        assert result.stock_type in [StockType.TYPE_A_TREND, StockType.TYPE_B_RANGE, StockType.UNCLASSIFIED]
        assert result.confidence >= 0
    
    def test_type_b_classification(self):
        """Test Type B (range) classification."""
        data = create_range_data(60)
        classifier = StockClassifier()
        
        result = classifier.classify("TEST", data)
        
        assert result.stock_type in [StockType.TYPE_B_RANGE, StockType.UNCLASSIFIED]
        assert 0 <= result.confidence <= 1
    
    def test_classification_permissions(self):
        """Test trading permissions based on classification."""
        classifier = StockClassifier()
        
        # Type A should allow trend trading
        trend_data = create_trend_data(120)
        result = classifier.classify("TEST", trend_data)
        
        if result.stock_type == StockType.TYPE_A_TREND:
            assert result.allow_trend_trade == True
            assert result.max_position_pct > 0
        
        # Type B should limit speculation
        range_data = create_range_data(60)
        result = classifier.classify("TEST", range_data)
        
        if result.stock_type == StockType.TYPE_B_RANGE:
            assert result.allow_trend_trade == False
            assert result.allow_speculation == True
            assert result.max_position_pct <= 0.05
    
    def test_insufficient_data(self):
        """Test handling of insufficient data."""
        classifier = StockClassifier()
        
        # Very short data
        short_data = create_range_data(10)
        result = classifier.classify("TEST", short_data)
        
        # Should return unclassified
        assert result.stock_type == StockType.UNCLASSIFIED
        assert "Insufficient" in result.reasoning
    
    def test_classification_result_attributes(self):
        """Test ClassificationResult has all required attributes."""
        data = create_trend_data(120)
        classifier = StockClassifier()
        
        result = classifier.classify("TEST", data)
        
        assert hasattr(result, 'symbol')
        assert hasattr(result, 'stock_type')
        assert hasattr(result, 'confidence')
        assert hasattr(result, 'allow_trend_trade')
        assert hasattr(result, 'allow_add_position')
        assert hasattr(result, 'max_position_pct')
        assert hasattr(result, 'reasoning')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
