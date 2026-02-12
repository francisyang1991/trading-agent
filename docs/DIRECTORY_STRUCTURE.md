# Directory Structure - Best Practices

This document describes the improved directory structure optimized for token efficiency and maintainability.

## Root Directory Organization

```
trading_agent/
├── .cursor/                    # Cursor IDE configuration
│   ├── rules/                  # Project rules (always loaded by Cursor)
│   │   ├── candid-planner.mdc  # Development principles
│   │   └── trading-agent.md    # Project-specific rules
│   └── .cursorignore          # Files excluded from AI context (token efficiency)
│
├── config/                     # Configuration files
│   ├── settings.yaml          # System settings
│   ├── stock_universe.yaml     # Stock lists and themes
│   └── symbols.yaml           # Legacy symbols
│
├── src/                        # Core library code (importable modules)
│   ├── core/                   # Base types, events, config
│   ├── data/                   # Data fetching and caching
│   ├── indicators/            # Technical indicators (RSI, ATR, VPES)
│   ├── signals/                # Entry/exit signal generation
│   ├── classifier/             # Stock regime classification
│   ├── portfolio/              # Portfolio tracking and management
│   ├── journal/                # Trade journaling and learning
│   ├── sizing/                 # Position sizing (Kelly, vol-target)
│   ├── risk/                   # Risk management
│   ├── backtest/               # Backtesting engine
│   ├── execution/              # Order execution
│   └── utils/                  # Shared utilities
│
├── tools/                      # CLI tools (thin wrappers around src/)
│   ├── scanner.py             # Daily stock scanner
│   ├── portfolio.py            # Portfolio optimization
│   ├── daily_review.py         # Daily lessons learned generator
│   └── ...
│
├── scripts/                    # System scripts (cron, setup)
│   ├── daily_scan.sh           # Shell wrapper for scheduler
│   ├── install_scheduler.sh    # Setup scripts
│   └── sync_to_*.sh            # Deployment scripts
│
├── tests/                      # Test suite (organized by type)
│   ├── unit/                   # Unit tests
│   │   ├── legacy/             # Legacy test files (moved from root)
│   │   └── ...                 # Current unit tests
│   ├── integration/            # Integration tests
│   ├── e2e/                    # End-to-end tests
│   └── fixtures/               # Test fixtures
│
├── data/                       # Runtime data (gitignored, cursorignored)
│   ├── stock_cache.db          # Price data cache (LARGE - excluded)
│   ├── portfolio.json          # Current positions
│   ├── signal_journal.json     # Signal history
│   ├── cache/                  # Cached data (excluded)
│   ├── historical/             # Historical data (excluded)
│   └── lessons/                # Daily lessons learned
│
├── results/                    # Generated reports (gitignored, cursorignored)
│   ├── temp/                   # Temporary output files
│   └── scan_YYYYMMDD_HHMMSS.txt
│
├── logs/                       # Log files (gitignored, cursorignored)
│
├── workspace/                  # Discord bot and analysis workspace
│   ├── scripts/                # Analysis scripts (Python/JS)
│   ├── config/                 # Workspace configuration
│   └── data/                   # Workspace data (excluded - 313MB!)
│
├── docs/                       # Documentation
│   ├── PROJECT_SOP.md          # Standard Operating Procedures
│   ├── DIRECTORY_STRUCTURE.md   # This file
│   ├── TOKEN_EFFICIENCY.md     # Token efficiency guide
│   └── ...
│
├── examples/                   # Example scripts
│   └── ...
│
├── .github/                    # GitHub configuration
│   └── workflows/
│       └── ci.yml              # CI/CD pipeline
│
├── README.md                   # Project overview
├── requirements.txt            # Python dependencies
├── pytest.ini                  # Pytest configuration
└── docker-compose.yaml         # Docker configuration
```

## Token Efficiency Strategy

### Files Excluded from Cursor Context (`.cursorignore`)

1. **Large Data Files** (200MB+):
   - `data/*.db` - SQLite databases
   - `data/cache/` - Cached data
   - `data/historical/` - Historical data
   - `workspace/data/` - Workspace data (313MB)

2. **Generated Outputs**:
   - `results/` - All generated reports
   - `logs/` - Log files
   - `scan_results_*.txt` - Temporary scan results

3. **Build Artifacts**:
   - Virtual environments (`.venv/`, `venv/`)
   - Python cache (`__pycache__/`)
   - Build directories (`build/`, `dist/`)

4. **Test Artifacts**:
   - `.pytest_cache/`
   - `.coverage`
   - `htmlcov/`

### Why This Structure?

1. **Clean Root**: No scattered test files or temporary outputs
2. **Organized Tests**: All tests in `tests/` with clear organization
3. **Clear Separation**: Source code (`src/`), tools (`tools/`), scripts (`scripts/`)
4. **Token Efficient**: Large files excluded via `.cursorignore`
5. **Maintainable**: Clear organization makes it easy to find files

## File Organization Rules

### `src/` - Core Library
- **Purpose**: Reusable, importable Python modules
- **Rules**:
  - No CLI argument parsing
  - No direct print statements (use logging)
  - Pure functions where possible
  - Well-documented classes and methods
  - Unit testable

### `tools/` - CLI Wrappers
- **Purpose**: Command-line interfaces for users
- **Rules**:
  - Import from `src/`
  - Handle argument parsing
  - Format output for humans
  - Minimal business logic (delegate to `src/`)

### `scripts/` - System Scripts
- **Purpose**: Shell scripts for automation
- **Rules**:
  - Cron jobs, schedulers
  - Setup/installation scripts
  - No Python logic (call Python tools instead)

### `tests/` - Test Suite
- **Purpose**: All test files organized by type
- **Structure**:
  - `unit/` - Fast, isolated unit tests
  - `integration/` - Tests with mocked external dependencies
  - `e2e/` - End-to-end tests with live connections
  - `fixtures/` - Shared test fixtures
  - `unit/legacy/` - Legacy test files moved from root

## Migration Notes

### Files Moved
- `test_*.py` → `tests/unit/legacy/` (from root)
- `scan_results_*.txt` → `results/temp/` (from root)
- `test_report.txt` → `results/temp/` (from root)

### Files Excluded
- All files in `.cursorignore` are excluded from Cursor's AI context
- This reduces token usage by excluding 500MB+ of data files
- Cursor still uses semantic search to find relevant code when needed

## Best Practices

1. **Keep Root Clean**: Only essential files in root directory
2. **Organize Tests**: All tests belong in `tests/` directory
3. **Exclude Large Files**: Use `.cursorignore` for data/results/logs
4. **Clear Naming**: Use descriptive names that help semantic search
5. **Modular Code**: Well-organized code helps Cursor find only what's needed
