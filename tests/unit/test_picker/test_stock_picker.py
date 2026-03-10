"""
Unit tests for stock picker module.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from src.picker.stock_picker import (
    StockPicker, FactorCalculator, pick_stocks,
    calculate_ema, calculate_rsi, calculate_atr
)
from src.picker.ranking import RankingEngine, RankingCriteria, rank_picks
from src.core.types import Regime, VolatilityLevel, Strategy


class TestTechnicalIndicators:
    """Tests for technical indicator calculations."""
    
    def test_calculate_ema(self, trending_up_data):
        """Test EMA calculation."""
        close = trending_up_data['close']
        ema = calculate_ema(close, 21)
        
        assert len(ema) == len(close)
        assert not ema.isna().all()
        # EMA should be smoother than raw prices
        assert ema.std() < close.std()
    
    def test_calculate_rsi(self, trending_up_data):
        """Test RSI calculation."""
        close = trending_up_data['close']
        rsi = calculate_rsi(close)
        
        # RSI should be between 0 and 100
        valid_rsi = rsi.dropna()
        assert (valid_rsi >= 0).all()
        assert (valid_rsi <= 100).all()
        
        # Uptrending data should have higher RSI
        assert valid_rsi.iloc[-1] > 50
    
    def test_calculate_rsi_downtrend(self, downtrending_data):
        """Test RSI in downtrend."""
        close = downtrending_data['close']
        rsi = calculate_rsi(close)
        
        valid_rsi = rsi.dropna()
        # Downtrending should have lower RSI
        assert valid_rsi.iloc[-1] < 50
    
    def test_calculate_atr(self, volatile_data):
        """Test ATR calculation."""
        atr = calculate_atr(
            volatile_data['high'],
            volatile_data['low'],
            volatile_data['close']
        )
        
        assert len(atr) == len(volatile_data)
        assert (atr.dropna() > 0).all()


class TestFactorCalculator:
    """Tests for factor calculation."""
    
    @pytest.fixture
    def factor_calc(self, trending_up_data):
        """Create factor calculator with SPY proxy."""
        return FactorCalculator(spy_data=trending_up_data)
    
    def test_momentum_score_uptrend(self, factor_calc, trending_up_data):
        """Test momentum score for uptrending stock."""
        score, metrics = factor_calc.momentum_score(trending_up_data)
        
        assert 0 <= score <= 100
        assert 'momentum_6m' in metrics
        assert metrics['momentum_6m'] > 0  # Uptrend should have positive momentum
    
    def test_momentum_score_downtrend(self, factor_calc, downtrending_data):
        """Test momentum score for downtrending stock."""
        score, metrics = factor_calc.momentum_score(downtrending_data)
        
        assert 0 <= score <= 100
        assert metrics['momentum_6m'] < 0  # Downtrend
        assert score < 50  # Below average score
    
    def test_technical_score_uptrend(self, factor_calc, trending_up_data):
        """Test technical score for uptrending stock."""
        score, metrics = factor_calc.technical_score(trending_up_data)
        
        assert 0 <= score <= 100
        assert 'rsi' in metrics
        assert 'dist_ema21' in metrics
    
    def test_volume_score(self, factor_calc, trending_up_data):
        """Test volume analysis."""
        score, metrics = factor_calc.volume_score(trending_up_data)
        
        assert 0 <= score <= 100
        assert 'volume_ratio' in metrics
        assert metrics['volume_ratio'] > 0
    
    def test_base_pattern_score_consolidation(self, factor_calc, consolidation_data):
        """Test base pattern detection."""
        score, pattern, days = factor_calc.base_pattern_score(consolidation_data)
        
        assert 0 <= score <= 100
        # Should detect some pattern in consolidation data
        assert pattern != "INSUFFICIENT_DATA"
    
    def test_risk_reward_score(self, factor_calc, trending_up_data):
        """Test risk/reward calculation."""
        score, metrics = factor_calc.risk_reward_score(
            trending_up_data,
            entry_price=100,
            stop_price=95,
            target_price=115
        )
        
        assert 0 <= score <= 100
        assert metrics['rr_ratio'] == 3.0  # 15/5 = 3
        assert metrics['risk_pct'] == 5.0
        assert metrics['reward_pct'] == 15.0
    
    def test_relative_strength_without_spy(self, trending_up_data):
        """Test RS score without SPY data."""
        calc = FactorCalculator(spy_data=None)
        score, metrics = calc.relative_strength_score(trending_up_data)
        
        assert score == 50.0  # Default when no SPY
        assert metrics['rs_vs_spy'] == 0


class TestStockPicker:
    """Tests for StockPicker class."""
    
    @pytest.fixture
    def picker(self):
        """Create stock picker."""
        return StockPicker(min_score=0)  # Include all for testing
    
    def test_analyze_stock_uptrend(self, picker, trending_up_data):
        """Test analyzing uptrending stock."""
        pick = picker.analyze_stock("TEST_UP", trending_up_data)
        
        assert pick is not None
        assert pick.symbol == "TEST_UP"
        assert pick.total_score > 50  # Above average
        assert pick.regime in [Regime.STRONG_UP, Regime.MODERATE_UP, Regime.PARABOLIC]
    
    def test_analyze_stock_downtrend(self, picker, downtrending_data):
        """Test analyzing downtrending stock."""
        pick = picker.analyze_stock("TEST_DOWN", downtrending_data)
        
        assert pick is not None
        assert pick.regime in [Regime.DOWNTREND, Regime.SIDEWAYS]
        assert pick.strategy == Strategy.STAY_CASH or pick.total_score < 50
    
    def test_analyze_stock_sideways(self, picker, sideways_data):
        """Test analyzing sideways stock."""
        pick = picker.analyze_stock("TEST_SIDE", sideways_data)
        
        assert pick is not None
        assert pick.regime in [Regime.SIDEWAYS, Regime.WEAK_UP]
    
    def test_analyze_stock_volatile(self, picker, volatile_data):
        """Test analyzing volatile stock."""
        pick = picker.analyze_stock("TEST_VOL", volatile_data)
        
        assert pick is not None
        assert pick.volatility_level in [VolatilityLevel.HIGH, VolatilityLevel.ULTRA_HIGH]
        # Position size should be reduced for high vol
        assert pick.position_size_pct < 15
    
    def test_analyze_multiple(self, picker, multi_symbol_data):
        """Test analyzing multiple stocks."""
        picks = picker.analyze(
            list(multi_symbol_data.keys()),
            multi_symbol_data
        )
        
        assert len(picks) > 0
        # Should be sorted by score
        scores = [p.total_score for p in picks]
        assert scores == sorted(scores, reverse=True)
        # Should have ranks
        assert picks[0].rank == 1
    
    def test_analyze_with_spy(self, multi_symbol_data, sideways_data):
        """Test analysis with SPY data for RS."""
        # Use sideways data as SPY proxy (different from uptrend stocks)
        picker = StockPicker(min_score=0, spy_data=sideways_data)
        picks = picker.analyze(
            list(multi_symbol_data.keys()),
            multi_symbol_data
        )
        
        assert len(picks) > 0
        # RS should be calculated (some stocks will outperform/underperform)
        # At least one stock should have non-default RS score
        has_rs_calc = any(
            pick.rs_vs_spy != 0 or pick.relative_strength_score != 50 
            for pick in picks
        )
        assert has_rs_calc or len(picks) == 0
    
    def test_min_score_filter(self, multi_symbol_data):
        """Test minimum score filter."""
        picker = StockPicker(min_score=70)
        picks = picker.analyze(
            list(multi_symbol_data.keys()),
            multi_symbol_data
        )
        
        # All picks should meet minimum score
        for pick in picks:
            assert pick.total_score >= 70
    
    def test_entry_zone_calculation(self, picker, trending_up_data):
        """Test entry zone is calculated correctly."""
        pick = picker.analyze_stock("TEST", trending_up_data)
        
        assert pick is not None
        assert pick.entry_zone_low < pick.entry_zone_high
        assert pick.stop_loss < pick.entry_zone_low
        assert pick.target_1 > pick.entry_zone_high
        assert pick.target_2 > pick.target_1
    
    def test_expected_value_calculation(self, picker, trending_up_data):
        """Test expected value is calculated."""
        pick = picker.analyze_stock("TEST", trending_up_data)
        
        assert pick is not None
        assert 0 <= pick.win_rate <= 1
        assert pick.avg_win >= 0
        assert pick.avg_loss >= 0
        # EV = win_rate * avg_win - (1 - win_rate) * avg_loss
        expected_ev = pick.win_rate * pick.avg_win - (1 - pick.win_rate) * pick.avg_loss
        assert abs(pick.expected_value - expected_ev) < 0.01
    
    def test_strategy_assignment(self, picker, trending_up_data):
        """Test strategy is assigned based on regime/vol."""
        pick = picker.analyze_stock("TEST", trending_up_data)
        
        assert pick is not None
        assert pick.strategy in Strategy
        # Uptrend shouldn't be STAY_CASH
        if pick.regime in [Regime.STRONG_UP, Regime.MODERATE_UP, Regime.PARABOLIC]:
            assert pick.strategy != Strategy.STAY_CASH
    
    def test_grade_assignment(self, picker, trending_up_data):
        """Test grade is correctly assigned."""
        pick = picker.analyze_stock("TEST", trending_up_data)
        
        assert pick is not None
        assert pick.grade in ['A', 'B', 'C', 'D', 'F']
        
        # Verify grade matches score
        if pick.total_score >= 80:
            assert pick.grade == 'A'
        elif pick.total_score >= 70:
            assert pick.grade == 'B'


class TestRankingEngine:
    """Tests for RankingEngine."""
    
    @pytest.fixture
    def picks(self, strong_pick, weak_pick):
        """Create list of picks for testing."""
        return [strong_pick, weak_pick]
    
    def test_rank_picks(self, picks):
        """Test basic ranking."""
        engine = RankingEngine()
        ranked = engine.rank(picks)
        
        # Strong pick should be ranked higher
        assert ranked[0].symbol == "NVDA"
        assert ranked[0].rank == 1
    
    def test_filter_by_score(self, picks):
        """Test score filtering."""
        engine = RankingEngine()
        criteria = RankingCriteria(min_score=70)
        ranked = engine.rank(picks, criteria)
        
        # Only strong pick should pass
        assert len(ranked) == 1
        assert ranked[0].symbol == "NVDA"
    
    def test_filter_by_rr(self, picks):
        """Test R:R filtering."""
        engine = RankingEngine()
        criteria = RankingCriteria(min_score=0, min_rr_ratio=2.0)
        ranked = engine.rank(picks, criteria)
        
        for pick in ranked:
            assert pick.risk_reward >= 2.0
    
    def test_top_n(self, picks):
        """Test top N selection."""
        engine = RankingEngine()
        top = engine.top_n(picks, n=1)
        
        assert len(top) == 1
        assert top[0].symbol == "NVDA"
    
    def test_by_grade(self, picks):
        """Test grade filtering."""
        engine = RankingEngine()
        a_grade = engine.by_grade(picks, ['A'])
        
        assert len(a_grade) == 1
        assert a_grade[0].grade == 'A'
    
    def test_by_regime(self, picks):
        """Test regime filtering."""
        engine = RankingEngine()
        uptrend = engine.by_regime(picks, [Regime.STRONG_UP, Regime.MODERATE_UP])
        
        for pick in uptrend:
            assert pick.regime in [Regime.STRONG_UP, Regime.MODERATE_UP]


class TestConvenienceFunctions:
    """Tests for convenience functions."""
    
    def test_pick_stocks(self, multi_symbol_data):
        """Test pick_stocks function."""
        picks = pick_stocks(
            symbols=list(multi_symbol_data.keys()),
            data_dict=multi_symbol_data,
            min_score=0
        )
        
        assert len(picks) > 0
        assert all(hasattr(p, 'total_score') for p in picks)
    
    def test_rank_picks_function(self, strong_pick, weak_pick):
        """Test rank_picks function."""
        picks = [weak_pick, strong_pick]  # Wrong order
        ranked = rank_picks(picks, min_score=0)
        
        # Should be reordered by score
        assert ranked[0].symbol == "NVDA"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
