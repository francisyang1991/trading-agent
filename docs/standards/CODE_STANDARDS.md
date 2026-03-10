# Code Standards & Project Structure

This document is the single source of truth for project organization, coding conventions, and directory layout. It merges the former CODE_STANDARDS.md, PROJECT_SOP.md, and DIRECTORY_STRUCTURE.md.

---

## 1. Directory Structure

```
trading_agent/
├── .cursor/                    # Cursor IDE configuration
│   └── rules/                  # Project rules (always loaded by Cursor)
│
├── config/                     # Configuration files
│   ├── settings.yaml           # System settings
│   ├── picker_config.yaml      # Fundamental picker config
│   ├── stock_universe.yaml     # Stock lists and themes
│   └── symbols.yaml            # Legacy symbols
│
├── src/                        # Core library code (importable modules)
│   ├── core/                   # Base types, events, config
│   ├── data/                   # Data fetching and caching (IBKR, providers)
│   ├── indicators/             # Technical indicators (RSI, ATR, EMA, VPES)
│   ├── signals/                # Entry/exit signal generation
│   ├── classifier/             # Stock regime classification
│   ├── picker/                 # Fundamental stock picker (3-layer)
│   ├── portfolio/              # Portfolio tracking and management
│   ├── journal/                # Trade journaling and learning
│   ├── sizing/                 # Position sizing (Kelly, vol-target)
│   ├── risk/                   # Risk management
│   ├── backtest/               # Backtesting engine
│   ├── execution/              # Order execution
│   ├── universe/               # Universe management and screening
│   ├── strategy/               # Strategy matrix, EV calculator
│   ├── regime/                 # Regime and volatility classification
│   └── utils/                  # Shared utilities, config, logging
│
├── tools/                      # CLI tools (thin wrappers around src/)
│   ├── scanner.py              # Daily stock scanner
│   ├── run_three_layer_picker.py # Fundamental picker runner
│   ├── validate_three_layer_walkforward_tpsl.py # Walk-forward validation
│   └── ...
│
├── scripts/                    # System scripts (cron, setup, deploy)
│   ├── daily_scan.sh
│   ├── sync_to_aws.sh
│   └── services/               # Systemd service files
│
├── tests/                      # Test suite
│   ├── unit/                   # Fast, isolated unit tests
│   ├── e2e/                    # End-to-end tests with live connections
│   └── fixtures/               # Shared test fixtures
│
├── data/                       # Runtime data (gitignored, cursorignored)
│   ├── stock_cache.db          # Price data cache (SQLite)
│   ├── daily/                  # Daily OHLCV CSVs
│   ├── fundamental/            # Per-symbol fundamental CSVs
│   ├── picker/                 # Picker snapshots
│   └── cache/                  # Cached data
│
├── results/                    # Generated reports (gitignored)
│   └── picker/                 # Picker intermediate outputs
│
├── workspace/                  # Discord bot and analysis workspace
│   ├── scripts/                # Bot scripts, analysis tools
│   └── data/                   # Workspace data (excluded - large)
│
├── docs/                       # Documentation (classified subdirectories)
│   ├── standards/              # Code standards, CI, token efficiency
│   ├── testing/                # Test protocols, backtest recommendations
│   ├── deployment/             # Cloud deployment, lessons, migration
│   ├── status/                 # Worklog, project status, changelog
│   └── architecture/           # Pipeline design, system design
│
└── logs/                       # Log files (gitignored, cursorignored)
```

---

## 2. Module Organization

### `src/` - Core Library
- No CLI argument parsing
- No direct `print()` statements (use logging)
- Pure functions where possible
- Well-documented, unit testable

### `tools/` - CLI Wrappers
- **Import only from `src/`**; never `from tools.X import`
- Handle argument parsing and format output for humans
- Minimal business logic (delegate to `src/`)

### `scripts/` - System Scripts
- Cron jobs, schedulers, setup/installation scripts
- No Python logic (call Python via `python -m tools.X`)

### `tests/` - Test Suite
- `unit/` - Fast, isolated unit tests
- `e2e/` - End-to-end tests with live connections
- `fixtures/` - Shared test fixtures and data

---

## 3. Shared Utilities (No Reinventing Wheels)

| Capability | Canonical Location | Usage |
|------------|-------------------|-------|
| EMA, RSI, ATR | `src/indicators/trend.py` | All tools and signals |
| Regime/Volatility | `src/regime/` | Scanner, backtest |
| Strategy matrix, EV | `src/strategy/` | Scanner, backtest |
| Data access | `src/data_manager.py` (cached) | Scanner, backtest |

**Indicator usage**: Use `calculate_ema_series`, `calculate_rsi_series`, `calculate_atr_series` from `src.indicators.trend` for Series-based callers. Use `calculate_atr`, `calculate_rsi`, `calculate_emas` for DataFrame-based callers (lowercase keys: `close`, `high`, `low`).

---

## 4. Logging Standard

- Use `loguru` via `from loguru import logger`
- Tools may use `print()` for user-facing output; use `logger` for diagnostics and errors
- Migrate legacy `logging.getLogger(__name__)` and `print()` when touching files

---

## 5. Error Handling

- **Unexpected errors**: Re-raise after logging (`logger.exception(...); raise`)
- **Expected "not found"**: Return `None` or `Optional[T]`
- Document behavior in docstrings

---

## 6. Type Hints

- All public functions: parameter and return types
- Use `Optional[T]`, `List[T]` from `typing` where applicable

---

## 7. Naming Conventions

- **OHLCV columns**: Prefer lowercase (`close`, `high`, `low`) in `src/`; tools can adapt yfinance `Close`/`High`/`Low` at boundaries
- **Module names**: snake_case
- **Class names**: PascalCase

---

## 8. Data Access

- **Cache-first**: Use `CachedDataManager` from `src/data_manager.py` for scanning and backtesting
- **Validate freshness**: Before backtesting, confirm data covers the required period
- **No direct yfinance in backtests**: Prefer fixing the cache layer over bypassing it

---

## 9. Backtest Validity

- Include commission and slippage in backtest results
- Walk-forward or holdout validation before trusting any strategy
- Compare every backtest to B&H and/or SPY

---

## 10. Token Efficiency

- Exclude large data files from Cursor context via `.cursorignore`
- `data/*.db`, `data/cache/`, `data/historical/`, `workspace/data/` are excluded
- Generated outputs (`results/`, `logs/`) are excluded
- Build artifacts (`.venv/`, `__pycache__/`) are excluded

---

## 11. Daily Workflow

### Pre-Market (Before 9:30 AM ET)
1. Review overnight news for portfolio holdings
2. Check pre-market prices for gaps

### Mid-Day Scan (12:30 PM ET - Automated)
1. Scanner runs on full universe
2. Report generated in `results/`
3. Signals logged to journal

### Post-Market Review (After 4:00 PM ET)
1. Run `tools/daily_review.py`
2. Review open positions and signal outcomes
3. Generate lessons learned

---

## 12. Scanner Versioning

Format: `vX.Y.Z` where X=major, Y=minor improvement, Z=bug fix.

Changelog entries should include: Changes, Reasoning, Results (win rate, EV).

---

## 13. Git Workflow

- `main`/`expand-universe`: Stable, production-ready code
- Feature branches for development
- PR-based workflow enforced with CI checks (Lint, Unit, Integration, Bot, CI Gate)

---

## 14. Maintenance Schedule

| Cadence | Tasks |
|---------|-------|
| Weekly | Review lessons, check scanner metrics, consider parameter adjustments |
| Monthly | Full backtest of current scanner, compare to SPY |
| Quarterly | Review overall strategy, assess universe updates, performance attribution |
