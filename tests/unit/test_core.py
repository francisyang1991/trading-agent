"""
Unit tests for core module.

Tests for types, events, and configuration.
"""

import pytest
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

from src.core.types import (
    OHLCV, Signal, SignalType, SignalStrength, Order, OrderSide, OrderType,
    OrderStatus, Position, PositionStatus, Trade, PickScore,
    Regime, VolatilityLevel, Strategy,
    get_regime_from_momentum, get_volatility_level, calculate_grade,
    dataframe_to_ohlcv_list
)
from src.core.events import (
    EventBus, Event, EventType,
    create_signal_event, create_order_event, create_position_event, create_risk_event
)
from src.core.config import (
    SystemConfig, RiskConfig, SizingConfig, load_config
)


class TestOHLCV:
    """Tests for OHLCV data class."""
    
    def test_create_ohlcv(self):
        """Test OHLCV creation."""
        ohlcv = OHLCV(
            timestamp=datetime.now(),
            open=100.0,
            high=105.0,
            low=98.0,
            close=103.0,
            volume=1000000
        )
        
        assert ohlcv.open == 100.0
        assert ohlcv.high == 105.0
        assert ohlcv.low == 98.0
        assert ohlcv.close == 103.0
        assert ohlcv.volume == 1000000
    
    def test_typical_price(self):
        """Test typical price calculation."""
        ohlcv = OHLCV(
            timestamp=datetime.now(),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000000
        )
        
        # (H + L + C) / 3 = (105 + 95 + 102) / 3 = 100.67
        assert abs(ohlcv.typical_price - 100.67) < 0.01
    
    def test_range(self):
        """Test range calculation."""
        ohlcv = OHLCV(
            timestamp=datetime.now(),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000000
        )
        
        assert ohlcv.range == 10.0  # 105 - 95
    
    def test_body(self):
        """Test body calculation."""
        ohlcv = OHLCV(
            timestamp=datetime.now(),
            open=100.0,
            high=105.0,
            low=95.0,
            close=103.0,
            volume=1000000
        )
        
        assert ohlcv.body == 3.0  # 103 - 100
    
    def test_is_bullish(self):
        """Test bullish candle detection."""
        bullish = OHLCV(datetime.now(), 100.0, 105.0, 98.0, 103.0, 1000000)
        bearish = OHLCV(datetime.now(), 103.0, 105.0, 98.0, 100.0, 1000000)
        
        assert bullish.is_bullish == True
        assert bearish.is_bullish == False


class TestSignal:
    """Tests for Signal data class."""
    
    def test_create_signal(self, entry_signal):
        """Test signal creation."""
        assert entry_signal.symbol == "NVDA"
        assert entry_signal.signal_type == SignalType.ENTRY_LONG
        assert entry_signal.strength == SignalStrength.STRONG
        assert entry_signal.score == 85.0
    
    def test_risk_reward_calculation(self):
        """Test risk/reward calculation."""
        signal = Signal(
            symbol="TEST",
            signal_type=SignalType.ENTRY_LONG,
            strength=SignalStrength.STRONG,
            timestamp=datetime.now(),
            entry_price=100.0,
            stop_price=95.0,
            target_price=115.0
        )
        
        # Risk: 100 - 95 = 5
        # Reward: 115 - 100 = 15
        # R:R = 15/5 = 3.0
        assert signal.risk_reward == 3.0
    
    def test_risk_reward_missing_prices(self):
        """Test R:R when prices are missing."""
        signal = Signal(
            symbol="TEST",
            signal_type=SignalType.ENTRY_LONG,
            strength=SignalStrength.MODERATE,
            timestamp=datetime.now()
        )
        
        assert signal.risk_reward == 0.0


class TestPosition:
    """Tests for Position data class."""
    
    def test_create_position(self, open_position):
        """Test position creation."""
        assert open_position.symbol == "NVDA"
        assert open_position.quantity == 100
        assert open_position.avg_entry_price == 500.0
    
    def test_market_value(self, open_position):
        """Test market value calculation."""
        open_position.current_price = 510.0
        assert open_position.market_value == 51000.0  # 100 * 510
    
    def test_cost_basis(self, open_position):
        """Test cost basis calculation."""
        assert open_position.cost_basis == 50000.0  # 100 * 500
    
    def test_update_price(self, open_position):
        """Test price update."""
        open_position.update_price(520.0)
        
        assert open_position.current_price == 520.0
        assert open_position.unrealized_pnl == 2000.0  # (520-500) * 100
        assert abs(open_position.unrealized_pnl_pct - 4.0) < 0.01  # 4%


class TestTrade:
    """Tests for Trade data class."""
    
    def test_winning_trade(self, winning_trade):
        """Test winning trade properties."""
        assert winning_trade.is_winner == True
        assert winning_trade.pnl > 0
        assert winning_trade.pnl_pct > 0
    
    def test_losing_trade(self, losing_trade):
        """Test losing trade properties."""
        assert losing_trade.is_winner == False
        assert losing_trade.pnl < 0
    
    def test_gross_pnl(self, winning_trade):
        """Test gross P&L calculation."""
        # (520 - 480) * 100 = 4000
        assert winning_trade.gross_pnl == 4000.0


class TestPickScore:
    """Tests for PickScore data class."""
    
    def test_strong_pick(self, strong_pick):
        """Test strong pick."""
        assert strong_pick.grade == "A"
        assert strong_pick.total_score >= 80
        assert strong_pick.expected_value > 0
    
    def test_weak_pick(self, weak_pick):
        """Test weak pick."""
        assert weak_pick.grade == "D"
        assert weak_pick.total_score < 50
        assert weak_pick.expected_value < 0


class TestRegimeClassification:
    """Tests for regime classification functions."""
    
    @pytest.mark.parametrize("momentum,expected", [
        (120.0, Regime.PARABOLIC),
        (75.0, Regime.STRONG_UP),
        (35.0, Regime.MODERATE_UP),
        (10.0, Regime.WEAK_UP),
        (-10.0, Regime.SIDEWAYS),
        (-30.0, Regime.DOWNTREND),
    ])
    def test_get_regime_from_momentum(self, momentum, expected):
        """Test regime classification from momentum."""
        assert get_regime_from_momentum(momentum) == expected
    
    @pytest.mark.parametrize("vol,expected", [
        (90.0, VolatilityLevel.ULTRA_HIGH),
        (65.0, VolatilityLevel.HIGH),
        (40.0, VolatilityLevel.MODERATE),
        (20.0, VolatilityLevel.LOW),
    ])
    def test_get_volatility_level(self, vol, expected):
        """Test volatility classification."""
        assert get_volatility_level(vol) == expected
    
    @pytest.mark.parametrize("score,expected", [
        (90, "A"),
        (75, "B"),
        (65, "C"),
        (55, "D"),
        (40, "F"),
    ])
    def test_calculate_grade(self, score, expected):
        """Test grade calculation."""
        assert calculate_grade(score) == expected


class TestDataFrameConversion:
    """Tests for DataFrame conversion."""
    
    def test_dataframe_to_ohlcv_list(self, trending_up_data):
        """Test converting DataFrame to OHLCV list."""
        ohlcv_list = dataframe_to_ohlcv_list(trending_up_data)
        
        assert len(ohlcv_list) == len(trending_up_data)
        assert all(isinstance(o, OHLCV) for o in ohlcv_list)
        
        # Check first and last
        first = ohlcv_list[0]
        assert first.close == trending_up_data['close'].iloc[0]


class TestEventBus:
    """Tests for EventBus."""
    
    def test_subscribe_and_publish(self, event_bus):
        """Test basic subscribe/publish."""
        received = []
        
        def handler(event):
            received.append(event)
        
        event_bus.subscribe(EventType.SIGNAL_GENERATED, handler)
        
        event = Event(
            event_type=EventType.SIGNAL_GENERATED,
            timestamp=datetime.now(),
            source="test",
            symbol="NVDA"
        )
        
        event_bus.publish(event)
        
        assert len(received) == 1
        assert received[0].symbol == "NVDA"
    
    def test_symbol_specific_subscription(self, event_bus):
        """Test symbol-specific subscription."""
        nvda_events = []
        aapl_events = []
        
        event_bus.subscribe(EventType.PRICE_UPDATE, lambda e: nvda_events.append(e), symbol="NVDA")
        event_bus.subscribe(EventType.PRICE_UPDATE, lambda e: aapl_events.append(e), symbol="AAPL")
        
        # Publish NVDA event
        event_bus.publish(Event(
            event_type=EventType.PRICE_UPDATE,
            timestamp=datetime.now(),
            source="test",
            symbol="NVDA"
        ))
        
        assert len(nvda_events) == 1
        assert len(aapl_events) == 0
    
    def test_unsubscribe(self, event_bus):
        """Test unsubscribe."""
        received = []
        
        def handler(event):
            received.append(event)
        
        event_bus.subscribe(EventType.ORDER_FILLED, handler)
        event_bus.unsubscribe(EventType.ORDER_FILLED, handler)
        
        event_bus.publish(Event(
            event_type=EventType.ORDER_FILLED,
            timestamp=datetime.now(),
            source="test"
        ))
        
        assert len(received) == 0
    
    def test_pause_resume(self, event_bus):
        """Test pause and resume."""
        received = []
        
        event_bus.subscribe(EventType.MARKET_DATA_UPDATE, lambda e: received.append(e))
        
        event_bus.pause()
        event_bus.publish(Event(
            event_type=EventType.MARKET_DATA_UPDATE,
            timestamp=datetime.now(),
            source="test"
        ))
        
        assert len(received) == 0
        
        event_bus.resume()
        event_bus.publish(Event(
            event_type=EventType.MARKET_DATA_UPDATE,
            timestamp=datetime.now(),
            source="test"
        ))
        
        assert len(received) == 1
    
    def test_event_history(self, event_bus):
        """Test event history."""
        for i in range(5):
            event_bus.publish(Event(
                event_type=EventType.PRICE_UPDATE,
                timestamp=datetime.now(),
                source="test",
                symbol=f"SYM_{i}"
            ))
        
        history = event_bus.get_history(EventType.PRICE_UPDATE, limit=3)
        assert len(history) == 3
        
        # Most recent first
        assert history[0].symbol == "SYM_4"


class TestEventHelpers:
    """Tests for event creation helpers."""
    
    def test_create_signal_event(self):
        """Test signal event creation."""
        event = create_signal_event(
            symbol="NVDA",
            signal_type="ENTRY_LONG",
            score=85.0,
            reasoning="Test"
        )
        
        assert event.event_type == EventType.SIGNAL_GENERATED
        assert event.symbol == "NVDA"
        assert event.data["signal_type"] == "ENTRY_LONG"
        assert event.data["score"] == 85.0
    
    def test_create_risk_event(self):
        """Test risk event creation."""
        event = create_risk_event(
            event_type=EventType.RISK_LIMIT_BREACH,
            level="CRITICAL",
            message="Drawdown exceeded",
            value=0.16,
            threshold=0.15
        )
        
        assert event.event_type == EventType.RISK_LIMIT_BREACH
        assert event.priority == 1  # High priority for breach


class TestSystemConfig:
    """Tests for SystemConfig."""
    
    def test_default_config(self):
        """Test default configuration."""
        config = SystemConfig()
        
        assert config.risk.max_position_pct == 0.15
        assert config.risk.max_drawdown == 0.15
        assert config.sizing.base_size_pct == 0.05
        assert config.backtest.initial_capital == 100000
    
    def test_config_from_dict(self):
        """Test config from dictionary."""
        data = {
            'risk': {'max_position_pct': 0.10},
            'sizing': {'base_size_pct': 0.03},
            'mode': 'paper'
        }
        
        config = SystemConfig.from_dict(data)
        
        assert config.risk.max_position_pct == 0.10
        assert config.sizing.base_size_pct == 0.03
        assert config.mode == 'paper'
    
    def test_config_validation(self):
        """Test config validation."""
        config = SystemConfig()
        config.risk.max_position_pct = 0.60  # Too high
        config.sizing.kelly_fraction = 0.70  # Too aggressive
        
        errors = config.validate()
        
        assert len(errors) > 0
        assert any("max_position_pct" in e for e in errors)
    
    def test_config_to_dict(self):
        """Test config serialization."""
        config = SystemConfig()
        data = config.to_dict()
        
        assert 'risk' in data
        assert 'sizing' in data
        assert data['risk']['max_position_pct'] == 0.15


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
