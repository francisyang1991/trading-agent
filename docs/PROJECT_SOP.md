# SAIYAN Trading Agent - Standard Operating Procedures

## Project Overview

A systematic trading system that scans stocks, generates signals, tracks portfolios, and learns from outcomes to continuously improve.

---

## Directory Structure

```
trading_agent/
├── config/                 # Configuration files
│   ├── settings.yaml       # System settings
│   ├── stock_universe.yaml # Stock lists and themes
│   └── symbols.yaml        # Legacy symbols
│
├── src/                    # Core library code (importable modules)
│   ├── core/               # Base types, events, config
│   ├── data/               # Data fetching and caching
│   ├── indicators/         # Technical indicators (RSI, ATR, VPES)
│   ├── signals/            # Entry/exit signal generation
│   ├── classifier/         # Stock regime classification
│   ├── portfolio/          # Portfolio tracking and management
│   ├── journal/            # Trade journaling and learning
│   ├── sizing/             # Position sizing (Kelly, vol-target)
│   ├── risk/               # Risk management
│   ├── backtest/           # Backtesting engine
│   ├── execution/          # Order execution
│   └── utils/              # Shared utilities
│
├── tools/                  # CLI tools (thin wrappers around src/)
│   ├── scanner.py          # Daily stock scanner
│   ├── portfolio.py        # Portfolio optimization
│   ├── daily_review.py     # Daily lessons learned generator
│   └── ...
│
├── scripts/                # System scripts (cron, setup)
│   ├── daily_scan.sh       # Shell wrapper for scheduler
│   └── install_scheduler.sh
│
├── data/                   # Runtime data (gitignored)
│   ├── stock_cache.db      # Price data cache
│   ├── portfolio.json      # Current positions
│   ├── signal_journal.json # Signal history
│   └── lessons/            # Daily lessons learned
│
├── results/                # Generated reports (gitignored)
│   └── scan_YYYYMMDD_HHMMSS.txt
│
├── logs/                   # Log files (gitignored)
│
├── tests/                  # Unit and integration tests
│
└── docs/                   # Documentation
    ├── PROJECT_SOP.md      # This file
    ├── SCANNER_CHANGELOG.md # Scanner version history
    └── LESSONS_LEARNED.md  # Accumulated trading insights
```

---

## Code Organization Rules

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

---

## Daily Workflow

### Pre-Market (Before 9:30 AM ET)
1. Review overnight news for portfolio holdings
2. Check pre-market prices for gaps
3. Review any pending alerts

### Mid-Day Scan (12:30 PM ET - Automated)
1. Scanner runs on full universe (241 stocks)
2. Report generated in `results/`
3. Signals logged to journal
4. AI reviews and sends summary

### Post-Market Review (After 4:00 PM ET)
1. Run `tools/daily_review.py`
2. Review open positions
3. Check signal outcomes
4. Generate lessons learned
5. Update scanner if needed (version controlled)

---

## Portfolio Management

### Adding Positions
Tell the AI: "I bought [SYMBOL] at $[PRICE], [SHARES] shares"
- AI logs to portfolio
- AI logs to signal journal
- AI provides key levels (stop, targets)

### Adjusting Positions
Tell the AI: "I added to [SYMBOL] at $[PRICE], [SHARES] more shares"
- AI updates average cost
- AI recalculates position size

### Closing Positions
Tell the AI: "I sold [SYMBOL] at $[PRICE]" or "I sold [X] shares of [SYMBOL] at $[PRICE]"
- AI logs exit
- AI calculates P&L
- AI updates signal journal with outcome
- AI extracts lessons learned

---

## Scanner Versioning

### Version Format
`vX.Y.Z` where:
- **X**: Major change (new strategy, breaking changes)
- **Y**: Minor improvement (new filters, thresholds)
- **Z**: Bug fix or parameter tweak

### Changelog Format
```markdown
## v1.2.0 (2026-01-15)
### Changes
- Increased R:R threshold from 1.5x to 2.0x
- Added RSI divergence filter

### Reasoning
- Backtest showed 1.5x R:R had only 48% win rate
- RSI divergence improved signal quality by 12%

### Results
- Win rate improved from 52% to 58%
- EV increased from +0.8% to +1.2%
```

---

## Self-Improvement Cycle

### Daily Review Process
1. **Collect Data**: Get all signals from past 30 days with outcomes
2. **Analyze**: Calculate win rates by signal type, regime, EV bucket
3. **Identify Issues**: Find underperforming patterns
4. **Propose Changes**: Suggest scanner parameter adjustments
5. **Test**: Backtest proposed changes
6. **Implement**: If improvement confirmed, update scanner
7. **Version**: Commit with changelog entry

### Key Metrics to Track
- Overall win rate (target: >55%)
- Average EV per signal (target: >1%)
- Profit factor (target: >1.5)
- Max drawdown (target: <15%)
- Signal quality by regime
- Signal quality by RSI range
- Signal quality by EV bucket

---

## Git Workflow

### Branches
- `main`: Stable, production-ready code
- `dev`: Development and testing
- `scanner/vX.Y.Z`: Scanner version branches

### Commit Messages
```
[scanner] v1.2.0: Increase R:R threshold to 2.0x

- Win rate analysis showed 1.5x threshold underperforming
- Backtest on 6 months data confirms improvement
- See SCANNER_CHANGELOG.md for details
```

---

## Emergency Procedures

### If Scanner Generates Bad Signals
1. Check recent changes in SCANNER_CHANGELOG.md
2. Revert to last known good version
3. Investigate root cause
4. Fix and re-version

### If Portfolio Data Corrupted
1. Check `data/portfolio.json` for syntax errors
2. Restore from backup (if available)
3. Manually reconstruct from trade history

---

## Maintenance Schedule

### Weekly
- Review accumulated lessons
- Check scanner performance metrics
- Consider parameter adjustments

### Monthly
- Full backtest of current scanner
- Compare to benchmark (SPY)
- Major version review if needed

### Quarterly
- Review overall strategy
- Assess if themes/universe need updating
- Performance attribution analysis
