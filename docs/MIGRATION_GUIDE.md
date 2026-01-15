# Migration Guide - Trading Agent Setup

This guide helps you set up the trading agent on a new laptop.

---

## Prerequisites

### 1. Python Environment
```bash
# Check Python version (need 3.8+)
python3 --version

# Install dependencies
pip3 install -r requirements.txt
```

### 2. Required Python Packages
```bash
pip3 install yfinance pandas numpy scikit-learn pyyaml
```

### 3. Git Setup
```bash
# Clone the repository
git clone <your-github-repo-url> trading_agent
cd trading_agent

# Checkout the feature branch
git checkout feature/trading-system-v2
```

---

## Initial Setup

### 1. Create Data Directories
```bash
mkdir -p data/lessons logs results
```

### 2. Restore Your Portfolio (if migrating)
Copy your portfolio data from the old laptop:
```bash
# From old laptop, copy:
# - data/portfolio.json
# - data/trade_history.json
# - data/signal_journal.json

# To new laptop:
scp old_laptop:~/trading_agent/data/portfolio.json ./data/
scp old_laptop:~/trading_agent/data/trade_history.json ./data/
scp old_laptop:~/trading_agent/data/signal_journal.json ./data/
```

### 3. Configure Email (Optional)
If you want email reports, create `config/settings.local.yaml`:
```yaml
notifications:
  channels:
    email:
      smtp_host: "smtp.gmail.com"
      smtp_port: 587
      smtp_username: "your-email@gmail.com"
      smtp_password: "your-app-password"
      sender: "your-email@gmail.com"
      recipients:
        - "your-email@gmail.com"
```

---

## Install Daily Scanner Scheduler

### macOS (using launchd)
```bash
# Install the scheduler (runs daily at 12:30 PM)
./scripts/install_scheduler.sh install

# Check status
./scripts/install_scheduler.sh status

# Test run manually
./scripts/install_scheduler.sh run
```

### Linux (using cron)
```bash
# Add to crontab (runs daily at 12:30 PM)
crontab -e

# Add this line:
30 12 * * * cd /path/to/trading_agent && /usr/bin/python3 tools/scanner.py --all
```

---

## Verify Installation

### 1. Test Scanner
```bash
# Run a quick scan
python3 tools/scanner.py AAPL NVDA MSFT

# Full universe scan
python3 tools/scanner.py --all
```

### 2. Test Portfolio Tracker
```bash
# View portfolio (if you restored it)
python3 -c "from src.portfolio import get_tracker; print(get_tracker().get_portfolio_summary())"
```

### 3. Test Daily Review
```bash
python3 tools/daily_review.py
```

---

## Your Current Portfolio

After migration, verify your positions:

| Symbol | Shares | Avg Cost | Notes |
|--------|--------|----------|-------|
| AMD | 20 | $221.00 | Manual entry |
| IONQ | 50 | $48.40 | Scanner BUY signal |
| XPEV | 100 | $21.00 | Scanner BUY signal |

**Total Value:** ~$9,074

---

## Daily Workflow

### Morning (Before Market)
- Review overnight news for portfolio holdings
- Check pre-market prices

### Mid-Day (12:30 PM - Automated)
- Scanner runs automatically
- Report saved to `results/scan_YYYYMMDD_HHMMSS.txt`
- Signals logged to journal

### After Market Close
```bash
# Generate daily review and lessons learned
python3 tools/daily_review.py --full
```

---

## Common Commands

### Portfolio Management
```bash
# Add position (via AI assistant)
# Just tell AI: "I bought AAPL at $150, 50 shares"

# View portfolio
python3 -c "from src.portfolio import get_tracker; import json; print(json.dumps(get_tracker().get_portfolio_summary(), indent=2))"
```

### Scanner
```bash
# Quick scan
python3 tools/scanner.py AAPL NVDA

# Full universe
python3 tools/scanner.py --all

# Specific theme
python3 tools/scanner.py --theme crypto
```

### Signal Journal
```bash
# Log signals from scan
python3 -c "from src.journal import get_signal_tracker; from tools.scanner import scan_symbols; tracker = get_signal_tracker(); results = scan_symbols(['AAPL']); tracker.log_signals_from_scan([r.__dict__ for r in results])"

# Check pending outcomes
python3 -c "from src.journal import get_signal_tracker; tracker = get_signal_tracker(); print(f'Pending: {len(tracker.get_pending_signals())}')"
```

---

## Troubleshooting

### Scanner Not Running
```bash
# Check scheduler status
./scripts/install_scheduler.sh status

# Check logs
tail -f logs/launchd_stdout.log
tail -f logs/launchd_stderr.log
```

### Portfolio Data Missing
- Check `data/portfolio.json` exists
- If corrupted, restore from backup or rebuild from trade history

### Import Errors
```bash
# Ensure you're in the project root
cd /path/to/trading_agent

# Check Python path
python3 -c "import sys; print('\n'.join(sys.path))"
```

---

## GitHub Setup

### First Time Setup
```bash
# Add remote (if not already configured)
git remote add origin <your-github-repo-url>

# Push current branch
git push -u origin feature/trading-system-v2
```

### Regular Updates
```bash
# Pull latest changes
git pull origin feature/trading-system-v2

# Push your changes
git add .
git commit -m "Your commit message"
git push origin feature/trading-system-v2
```

---

## Files to Backup Before Migration

**Critical (contains your data):**
- `data/portfolio.json` - Your positions
- `data/trade_history.json` - Trade history
- `data/signal_journal.json` - Signal tracking
- `data/lessons/*.json` - Daily lessons

**Optional (can regenerate):**
- `data/stock_cache.db` - Price cache (will rebuild)
- `results/*.txt` - Scan reports (can regenerate)
- `logs/*.log` - Log files

---

## Post-Migration Checklist

- [ ] Python 3.8+ installed
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Repository cloned and checked out
- [ ] Data directories created (`data/lessons`, `logs`, `results`)
- [ ] Portfolio data restored (if migrating)
- [ ] Scanner scheduler installed
- [ ] Test scan successful
- [ ] Daily review runs successfully
- [ ] GitHub remote configured (if using)

---

## Need Help?

Check these docs:
- `docs/PROJECT_SOP.md` - Standard Operating Procedures
- `docs/SCANNER_CHANGELOG.md` - Scanner version history
- `docs/LESSONS_LEARNED.md` - Accumulated trading insights
