# 🤖 SAIYAN Trading Agent

**Automated Quantitative Trading System for Interactive Brokers**

---

## 📋 Overview

A sophisticated portfolio management system built on IBKR API with:
- **Multi-timeframe analysis** (5min → Weekly)
- **Volume-Price Expansion Score (VPES)** custom indicator
- **Stock classification system** (Trend/Range/Reversal)
- **Intelligent position management** with pullback entry
- **Comprehensive backtesting** with parameter optimization
- **Real-time risk control** and circuit breakers

---

## 🏗️ Architecture

```
trading_agent/
├── config/                 # Configuration files
│   ├── settings.yaml       # Main settings
│   └── symbols.yaml        # Watchlist & symbol config
├── src/
│   ├── data/              # Data acquisition layer
│   │   ├── ibkr_client.py # IBKR API wrapper
│   │   ├── data_manager.py # Multi-timeframe data management
│   │   └── cache.py       # Local data caching
│   ├── indicators/        # Technical indicators
│   │   ├── vpes.py        # Volume-Price Expansion Score
│   │   ├── trend.py       # Trend indicators (EMA, structure)
│   │   └── volume.py      # Volume analysis
│   ├── classifier/        # Stock classification
│   │   └── stock_classifier.py
│   ├── signals/           # Signal generation
│   │   ├── trend_signal.py
│   │   ├── entry_signal.py
│   │   └── exit_signal.py
│   ├── position/          # Position management
│   │   ├── position_manager.py
│   │   └── order_executor.py
│   ├── backtest/          # Backtesting engine
│   │   ├── engine.py
│   │   └── optimizer.py
│   ├── risk/              # Risk management
│   │   └── risk_manager.py
│   └── utils/             # Utilities
│       ├── logger.py
│       └── helpers.py
├── logs/                   # Trade logs
├── data/                   # Cached market data
├── tests/                  # Unit tests
├── main.py                # Entry point
└── backtest_runner.py     # Backtest entry
```

---

## 🚀 Quick Start

### 1. Installation

```bash
cd trading_agent
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configuration

Copy and edit the configuration:
```bash
cp config/settings.example.yaml config/settings.yaml
```

### 3. IBKR Setup

#### Option A: TWS/Gateway (Native API via `ib_async`)

1. Install TWS or IB Gateway
2. Enable API connections in Global Configuration
3. Set API port (default: 7497 for TWS Paper, 4001 for Gateway)

#### Option B: Client Portal Web API (via IBeam)

[IBeam](https://github.com/Voyz/ibeam) provides automated authentication to IBKR's REST API.

```bash
# 1. Create credentials file
cp env.example env.list
# Edit env.list with your IBKR credentials

# 2. Start IBeam Gateway
docker compose up -d

# 3. Verify authentication
curl -k https://localhost:5000/v1/api/iserver/auth/status
```

**IBeam Features:**
- Headless authentication (no display required)
- Automatic session maintenance
- Docker containerized
- TLS certificate support

#### Option C: Client Portal Web API (via IBind)

[IBind](https://github.com/Voyz/ibind) is a comprehensive Python client library for IBKR's REST and WebSocket APIs.

```bash
# Install IBind
pip install ibind

# Run IBind examples
python examples/ibind_basic.py          # Basic REST API usage
python examples/ibind_websocket.py      # Real-time data streaming
python examples/ibind_trading.py        # Complete trading integration
```

**IBind Features:**
- REST API client with automatic question/answer handling
- WebSocket client for real-time data streaming
- Parallel requests and rate limiting
- OAuth 1.0a authentication support
- Conid unpacking and data formatting
- Thread-safe queue-based data streaming

### 4. Run Backtest

```bash
python backtest_runner.py --symbol AAPL --start 2023-01-01 --end 2024-01-01
```

### 5. Run Live (Paper Trading)

```bash
python main.py --mode paper
```

---

## 📨 Daily Scanner Email Report

The scanner already writes a text report to `results/scan_YYYYMMDD_HHMMSS.txt`. You can run it daily and email yourself the report:

```bash
# Run once (cron-friendly)
python tools/daily_scanner_email.py --once --high-conviction

# Or run as a long-running process (runs every day at 07:00 local time)
python tools/daily_scanner_email.py --daily --time 07:00 --high-conviction
```

Configure SMTP via environment variables (see `env.example` for a template):
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`
- `EMAIL_SENDER`, `EMAIL_RECIPIENTS`, `EMAIL_SUBJECT_PREFIX`

---

## 📊 Core Strategy Logic

### Stock Classification

| Type | Characteristics | Trading Mode |
|------|-----------------|--------------|
| **A (Trend)** | Clear uptrend, pullback consolidation | Full trend trading |
| **B (Range)** | Sideways, support/resistance bound | Small speculation only |
| **C (Reversal)** | Breaking long-term downtrend | Observation, tiny position |

### Entry Model

```
Big Rally → Pullback → Consolidation → Volume Breakout → EMA Retest → Continuation
```

### VPES (Volume-Price Expansion Score)

```python
VPES = (Close - Open) / Open * (Volume / MA(Volume, N))
```

- High VPES + Trend alignment → Strong signal
- Low VPES → No action / Wait

---

## ⚙️ Configuration Options

```yaml
# config/settings.yaml
trading:
  initial_position_pct: 0.05  # 5% initial position
  max_position_pct: 0.25      # 25% max per symbol
  
risk:
  max_portfolio_drawdown: 0.15  # 15% portfolio drawdown limit
  daily_loss_limit: 0.03        # 3% daily loss circuit breaker
  
timeframes:
  - 5min
  - 15min
  - 30min
  - 1hour
  - 4hour
  - 1day
  - 1week
```

---

## 📈 Backtest Metrics

- Cumulative Return
- Annualized Return
- Maximum Drawdown
- Win Rate
- Profit Factor
- Sharpe Ratio

---

## ⚠️ Risk Warnings

- This system is for educational and research purposes
- Past performance does not guarantee future results
- Always start with paper trading
- Never risk more than you can afford to lose

---

## 📝 License

MIT License - See LICENSE file for details.
