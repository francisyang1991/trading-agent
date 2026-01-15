# Trading System V2 - Complete Ecocycle Architecture

## Overview

This document describes the complete trading system architecture, including stock picking,
entry/exit signals, position management, risk control, and testing strategy.

## System Components

```
trading_agent/
├── src/
│   ├── core/                    # Core system components
│   │   ├── __init__.py
│   │   ├── types.py             # Shared types and enums
│   │   ├── events.py            # Event system for component communication
│   │   └── config.py            # Configuration management
│   │
│   ├── universe/                # Stock Universe Management
│   │   ├── __init__.py
│   │   ├── universe_manager.py  # Universe management and themes
│   │   ├── screener.py          # Multi-factor screening
│   │   └── filters.py           # Filter functions
│   │
│   ├── picker/                  # Stock Picking
│   │   ├── __init__.py
│   │   ├── stock_picker.py      # Main picker orchestrator
│   │   ├── ranking.py           # Ranking and scoring system
│   │   ├── factors/             # Individual factors
│   │   │   ├── __init__.py
│   │   │   ├── momentum.py      # Momentum factors
│   │   │   ├── quality.py       # Quality factors
│   │   │   ├── value.py         # Value factors
│   │   │   └── technical.py     # Technical factors
│   │   └── models/              # ML models for picking
│   │       ├── __init__.py
│   │       └── ensemble.py
│   │
│   ├── regime/                  # Market Regime Detection
│   │   ├── __init__.py
│   │   ├── market_regime.py     # Market-wide regime
│   │   ├── stock_regime.py      # Per-stock regime
│   │   └── volatility.py        # Volatility regime
│   │
│   ├── signals/                 # Signal Generation
│   │   ├── __init__.py
│   │   ├── entry/               # Entry signals
│   │   │   ├── __init__.py
│   │   │   ├── trend_entry.py
│   │   │   ├── pullback_entry.py
│   │   │   ├── breakout_entry.py
│   │   │   └── mean_reversion_entry.py
│   │   ├── exit/                # Exit signals
│   │   │   ├── __init__.py
│   │   │   ├── profit_target.py
│   │   │   ├── trend_exit.py
│   │   │   └── time_exit.py
│   │   └── signal_aggregator.py # Combine signals
│   │
│   ├── sizing/                  # Position Sizing
│   │   ├── __init__.py
│   │   ├── position_sizer.py    # Main sizing logic
│   │   ├── kelly.py             # Kelly criterion
│   │   ├── volatility_target.py # Vol-targeting
│   │   └── risk_parity.py       # Risk parity
│   │
│   ├── execution/               # Trade Execution
│   │   ├── __init__.py
│   │   ├── executor.py          # Execution orchestrator
│   │   ├── order_manager.py     # Order lifecycle
│   │   ├── slippage.py          # Slippage models
│   │   └── adapters/            # Broker adapters
│   │       ├── __init__.py
│   │       ├── ibkr_adapter.py
│   │       └── paper_adapter.py
│   │
│   ├── portfolio/               # Portfolio Management
│   │   ├── __init__.py
│   │   ├── portfolio.py         # Portfolio state
│   │   ├── position.py          # Position management
│   │   ├── allocation.py        # Allocation logic
│   │   └── rebalance.py         # Rebalancing
│   │
│   ├── risk/                    # Risk Management
│   │   ├── __init__.py
│   │   ├── risk_manager.py      # Main risk orchestrator
│   │   ├── position_limits.py   # Position-level limits
│   │   ├── portfolio_limits.py  # Portfolio-level limits
│   │   ├── drawdown.py          # Drawdown monitoring
│   │   └── circuit_breaker.py   # Emergency stops
│   │
│   ├── performance/             # Performance Tracking
│   │   ├── __init__.py
│   │   ├── tracker.py           # Performance tracking
│   │   ├── metrics.py           # Performance metrics
│   │   ├── attribution.py       # Return attribution
│   │   └── journal.py           # Trade journal
│   │
│   └── backtest/                # Backtesting
│       ├── __init__.py
│       ├── engine.py            # Backtest engine
│       ├── data_provider.py     # Historical data
│       ├── simulator.py         # Trade simulation
│       └── optimizer.py         # Parameter optimization
│
├── tests/                       # Test Suite
│   ├── conftest.py              # Shared fixtures
│   ├── fixtures/                # Test fixtures
│   │   ├── __init__.py
│   │   ├── market_data.py       # Mock market data
│   │   └── scenarios.py         # Test scenarios
│   ├── unit/                    # Unit tests
│   │   ├── test_universe/
│   │   ├── test_picker/
│   │   ├── test_signals/
│   │   ├── test_sizing/
│   │   ├── test_risk/
│   │   └── test_performance/
│   ├── integration/             # Integration tests
│   │   ├── test_picker_pipeline.py
│   │   ├── test_trading_pipeline.py
│   │   └── test_backtest.py
│   └── e2e/                     # End-to-end tests
│       ├── test_full_cycle.py
│       └── test_paper_trading.py
│
└── tools/                       # CLI Tools
    ├── scan.py                  # Stock scanner
    ├── pick.py                  # Stock picker
    ├── backtest.py              # Backtester
    └── monitor.py               # Live monitoring
```

## Ecocycle Flow

### 1. Stock Universe Management
- Define themes and categories
- Filter by market cap, volume, exchange
- Exclude based on criteria (news, earnings, etc.)

### 2. Stock Picking
- Multi-factor scoring
- Momentum: 6M return, RS vs SPY
- Quality: ROE, margins, debt
- Technical: Base patterns, volume accumulation
- Ranking and selection

### 3. Market Regime Detection
- Macro regime: Bull/Bear/Sideways
- Volatility regime: Low/Normal/High/Extreme
- Sector rotation detection
- Per-stock regime classification

### 4. Entry Signal Generation
- Trend following entries
- Pullback/retracement entries
- Breakout entries
- Mean reversion entries
- Signal confidence scoring

### 5. Position Sizing
- Kelly criterion (fractional)
- Volatility targeting
- Maximum position limits
- Correlation adjustment

### 6. Trade Execution
- Order type selection
- Slippage estimation
- Fill tracking
- Commission calculation

### 7. Position Management
- Track entry, adds, partials
- Monitor unrealized P&L
- Update stops and targets
- Trail stops when profitable

### 8. Exit Signal Generation
- Profit targets
- Trend reversal
- Stop loss triggers
- Time-based exits
- Trailing stop hits

### 9. Risk Management
- Position-level limits
- Portfolio-level exposure
- Drawdown monitoring
- Circuit breakers
- Correlation risk

### 10. Performance Tracking
- Real-time P&L
- Daily/weekly/monthly returns
- Risk-adjusted metrics
- Return attribution
- Trade journal

## Testing Strategy

### Unit Tests
- Test each component in isolation
- Mock dependencies
- Property-based testing for numeric functions
- 100% coverage for critical paths

### Integration Tests
- Test component interactions
- Use realistic mock data
- Verify data flow through pipeline
- Test error handling

### Backtest Validation
- Walk-forward testing
- Out-of-sample validation
- Regime-specific performance
- Parameter sensitivity

### Paper Trading
- Shadow live market
- Compare to backtest
- Monitor execution quality
- Validate alerts and logging

## Configuration

### Risk Parameters
```yaml
risk:
  max_position_pct: 0.15          # Max 15% per position
  max_portfolio_exposure: 1.0     # Max 100% invested
  max_sector_exposure: 0.30       # Max 30% per sector
  max_correlated_exposure: 0.50   # Max 50% correlated
  max_daily_loss: 0.03            # 3% daily stop
  max_drawdown: 0.15              # 15% drawdown halt
```

### Position Sizing
```yaml
sizing:
  base_size_pct: 0.05             # 5% base position
  kelly_fraction: 0.25            # 25% of Kelly
  vol_target: 0.15                # 15% vol target
  min_position: 1000              # $1,000 minimum
  max_positions: 20               # Max 20 positions
```

### Entry Criteria
```yaml
entry:
  min_score: 70                   # Minimum pick score
  min_rr_ratio: 2.0              # Minimum risk/reward
  max_dist_from_entry: 0.03      # Max 3% from entry zone
  require_regime_confirm: true   # Need regime alignment
```

## Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Core types and configuration
- [ ] Testing infrastructure
- [ ] Data layer validation
- [ ] Basic indicator tests

### Phase 2: Stock Picking (Week 2)
- [ ] Universe manager
- [ ] Multi-factor screener
- [ ] Ranking system
- [ ] Pick generator

### Phase 3: Signals (Week 3)
- [ ] Regime detector
- [ ] Entry signals
- [ ] Exit signals
- [ ] Signal aggregator

### Phase 4: Execution (Week 4)
- [ ] Position sizing
- [ ] Order management
- [ ] Portfolio tracking
- [ ] Risk limits

### Phase 5: Integration (Week 5)
- [ ] Full backtest engine
- [ ] Performance tracking
- [ ] Paper trading mode
- [ ] CLI tools

### Phase 6: Optimization (Week 6)
- [ ] Parameter tuning
- [ ] Walk-forward validation
- [ ] Production hardening
- [ ] Documentation
