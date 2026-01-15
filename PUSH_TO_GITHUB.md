# Push to GitHub - Quick Guide

## Current Status

✅ **All code committed locally**
- Branch: `feature/trading-system-v2`
- Latest commit: `6d77e8c` - Migration guide added
- Ready to push

## Steps to Push

### 1. Create GitHub Repository (if not exists)

Go to GitHub and create a new repository:
- Name: `trading-agent` (or your preferred name)
- Visibility: Private (recommended for trading code)
- Don't initialize with README (we already have one)

### 2. Add Remote and Push

```bash
cd /Users/bytedance/Documents/legacy_fromtt/SAIYANCapital25/trading_agent

# Add remote (replace with your GitHub username/repo)
git remote add origin https://github.com/YOUR_USERNAME/trading-agent.git

# Push branch
git push -u origin feature/trading-system-v2
```

### 3. Alternative: Using SSH

```bash
# Add SSH remote
git remote add origin git@github.com:YOUR_USERNAME/trading-agent.git

# Push
git push -u origin feature/trading-system-v2
```

## What Gets Pushed

✅ **Included:**
- All source code (`src/`)
- All tools (`tools/`)
- Documentation (`docs/`)
- Scripts (`scripts/`)
- Configuration templates (`config/`)

❌ **Excluded (via .gitignore):**
- `data/portfolio.json` - Your personal positions
- `data/signal_journal.json` - Your signal history
- `data/*.db` - Database files
- `results/` - Generated scan reports
- `logs/` - Log files
- `.env` - Environment variables

## Backup Your Data Before Migration

Before migrating to new laptop, backup these files:

```bash
# Create backup directory
mkdir -p ~/trading_agent_backup

# Copy critical data
cp data/portfolio.json ~/trading_agent_backup/
cp data/trade_history.json ~/trading_agent_backup/ 2>/dev/null || true
cp data/signal_journal.json ~/trading_agent_backup/ 2>/dev/null || true
cp -r data/lessons ~/trading_agent_backup/ 2>/dev/null || true

# Or use tar
tar -czf ~/trading_agent_backup.tar.gz data/portfolio.json data/trade_history.json data/signal_journal.json data/lessons/
```

## After Pushing

On your new laptop:

```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/trading-agent.git
cd trading-agent
git checkout feature/trading-system-v2

# Restore your data
# Copy portfolio.json, trade_history.json, signal_journal.json from backup

# Follow MIGRATION_GUIDE.md for complete setup
```
