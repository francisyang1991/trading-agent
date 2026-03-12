"""
Tests for Wyckoff Model (5-layer analysis).

Tests context classification, phase scoring, and setup evaluation.
"""

import pytest
import pandas as pd
import numpy as np

from src.indicators.wyckoff_model import (
    WyckoffModel,
    WyckoffAnalysis,
    MarketContext,
    WyckoffPhase,
    ContextResult,
    WyckoffPhaseResult,
    WyckoffSetup,
)
from src.indicators.market_structure import (
    MarketStructureAnalyzer,
    StructureDirection,
    StructureBreakType,
)
from src.indicators.trend import TrendIndicators


# =============================================================================
# FIXTURES
# =============================================================================

def make_ohlcv(closes, volumes=None, noise_seed=42):
    """Create OHLCV DataFrame from close prices."""
    rng = np.random.RandomState(noise_seed)
    n = len(closes)
    if volumes is None:
        volumes = [1000000 + rng.randint(-100000, 100000) for _ in range(n)]
    data = {'open': [], 'high': [], 'low': [], 'close': closes, 'volume': volumes}
    for c in closes:
        body = rng.uniform(-0.3, 0.3)
        o = c - body
        h = max(o, c) + abs(rng.normal(0, 0.2))
        l = min(o, c) - abs(rng.normal(0, 0.2))
        data['open'].append(round(o, 2))
        data['high'].append(round(h, 2))
        data['low'].append(round(l, 2))
    data['close'] = [round(c, 2) for c in closes]
    return pd.DataFrame(data)


def make_markup_data():
    """Clear uptrend with HH/HL, shallow pullbacks."""
    closes = []
    base = 100
    for leg in range(8):
        if leg % 2 == 0:
            target = base + 8 + leg * 2
            for i in range(10):
                closes.append(base + (target - base) * (i / 9))
            base = target
        else:
            target = base - 3
            for i in range(6):
                closes.append(base + (target - base) * (i / 5))
            base = target
    return make_ohlcv(closes)


def make_markdown_data():
    """Clear downtrend with LL/LH."""
    closes = []
    base = 200
    for leg in range(8):
        if leg % 2 == 0:
            target = base - 8 - leg * 2
            for i in range(10):
                closes.append(base + (target - base) * (i / 9))
            base = target
        else:
            target = base + 3
            for i in range(6):
                closes.append(base + (target - base) * (i / 5))
            base = target
    return make_ohlcv(closes)


def make_range_accumulation_data():
    """Range with sell-side sweeps followed by bullish CHOCH."""
    closes = []
    center = 150
    # First: oscillate in range
    for cycle in range(3):
        for i in range(10):
            closes.append(center + 4 * np.sin(i / 9 * 2 * np.pi))
    # Spring: dip below range low then recover
    closes.extend([146, 145.5, 145, 144, 143, 145, 147, 149, 151])
    # Break above range high (CHOCH/BOS bullish)
    closes.extend([153, 155, 156, 157, 158, 156, 155, 157, 159, 160])
    return make_ohlcv(closes)


def make_range_distribution_data():
    """Range with buy-side sweeps followed by bearish CHOCH."""
    closes = []
    center = 250
    for cycle in range(3):
        for i in range(10):
            closes.append(center + 4 * np.sin(i / 9 * 2 * np.pi))
    # UTAD: spike above range high then fail
    closes.extend([254, 255, 256, 257, 258, 256, 253, 251, 249])
    # Break below range (CHOCH/BOS bearish)
    closes.extend([247, 245, 244, 243, 242, 243, 244, 242, 240, 238])
    return make_ohlcv(closes)


# =============================================================================
# TESTS
# =============================================================================

class TestContextEngine:
    def test_markup_classification(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert result.context.context == MarketContext.MARKUP, \
            f"Expected MARKUP, got {result.context.context}"

    def test_markdown_classification(self):
        data = make_markdown_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert result.context.context == MarketContext.MARKDOWN, \
            f"Expected MARKDOWN, got {result.context.context}"

    def test_context_confidence_range(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert 0.0 <= result.context.confidence <= 1.0

    def test_context_has_reasoning(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert len(result.context.reasoning) > 0


class TestWyckoffPhaseScoring:
    def test_markup_phase_score(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert result.phase.markup_score > 0, "Markup data should have positive markup score"

    def test_markdown_phase_score(self):
        data = make_markdown_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert result.phase.markdown_score > 0, "Markdown data should have positive markdown score"

    def test_scores_are_bounded(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        for score in [result.phase.accumulation_score, result.phase.distribution_score,
                      result.phase.markup_score, result.phase.markdown_score]:
            assert 0.0 <= score <= 1.0, f"Score {score} out of range"

    def test_dominant_score_is_max(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        max_score = max(
            result.phase.accumulation_score,
            result.phase.distribution_score,
            result.phase.markup_score,
            result.phase.markdown_score,
        )
        assert result.phase.dominant_score == max_score


class TestWyckoffSetup:
    def test_setup_has_direction(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert result.setup.direction in (1, -1, 0)

    def test_setup_confidence_range(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert 0.0 <= result.setup.confidence <= 1.0

    def test_setup_rr_nonnegative(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert result.setup.risk_reward >= 0.0

    def test_invalid_setup_has_reasoning(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert len(result.setup.reasoning) > 0


class TestWyckoffModelIntegration:
    def test_analyze_returns_complete_result(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)

        assert isinstance(result, WyckoffAnalysis)
        assert isinstance(result.context, ContextResult)
        assert isinstance(result.phase, WyckoffPhaseResult)
        assert isinstance(result.setup, WyckoffSetup)
        assert result.liquidity_map is not None
        assert result.structure is not None

    def test_wyckoff_score_range(self):
        data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert 0.0 <= result.wyckoff_score <= 1.0

    def test_empty_data_graceful(self):
        data = pd.DataFrame()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(data)
        assert result.context.context == MarketContext.UNDEFINED
        assert result.wyckoff_score >= 0.0

    def test_short_data_graceful(self):
        data = make_ohlcv([100, 101, 102, 103, 104])
        model = WyckoffModel(swing_lookback=2)
        result = model.analyze(data)
        assert isinstance(result, WyckoffAnalysis)

    def test_multi_timeframe_context(self):
        ltf_data = make_range_accumulation_data()
        htf_data = make_markup_data()
        model = WyckoffModel(swing_lookback=3)
        result = model.analyze(ltf_data, htf_data=htf_data)
        assert isinstance(result, WyckoffAnalysis)
        # HTF context should influence setup

    def test_different_data_produces_different_results(self):
        markup_data = make_markup_data()
        markdown_data = make_markdown_data()
        model = WyckoffModel(swing_lookback=3)

        markup_result = model.analyze(markup_data)
        markdown_result = model.analyze(markdown_data)

        assert markup_result.context.context != markdown_result.context.context or \
               markup_result.phase.phase != markdown_result.phase.phase, \
               "Different market conditions should produce different analysis"
