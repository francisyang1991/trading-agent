# Trading Pipeline Skill

You have access to a trading signal pipeline that scans stocks, ranks them by conviction, and executes trades.

## Available Commands

Run these from `~/trading-agent/workspace/scripts/discord_bot/`:

```bash
# Full scan: Discord signals + 80 universe tickers → ranked candidates
python pipeline_skill.py scan

# Scan with custom universe size (50-100 recommended)
python pipeline_skill.py scan 100

# Show current ranked candidates (after a scan)
python pipeline_skill.py candidates

# Deep analyze a specific ticker
python pipeline_skill.py analyze NVDA

# Approve tickers for execution (places limit orders)
python pipeline_skill.py approve PBR HOOD NVDA

# Show current open positions
python pipeline_skill.py positions

# Portfolio health check + management suggestions
python pipeline_skill.py portfolio
```

## When Asked "What stocks can I enter?"

Follow this workflow:

1. **Run a scan** to get fresh candidates:
   ```bash
   cd ~/trading-agent/workspace/scripts/discord_bot && python pipeline_skill.py scan 80
   ```

2. **Review candidates** and filter for BUY actions with score >= 5.0:
   ```bash
   python pipeline_skill.py candidates
   ```

3. **Deep analyze** the top 3-5 candidates for confirmation:
   ```bash
   python pipeline_skill.py analyze TICKER
   ```

4. **Present ranked recommendations** to the user with:
   - Ticker, direction (Long/Short), conviction score
   - Entry price / buy zone
   - Stop loss and targets
   - EV and risk/reward ratio
   - Why (Discord sentiment + scanner analysis)

## Scoring Guide

- **Score 7-10**: Strong BUY — high conviction, multiple signals aligned
- **Score 5-7**: Moderate BUY — worth entering with smaller position
- **Score 4-5**: WAIT — watch for better entry or confirmation
- **Score < 4**: NO_TRADE — skip

## Data Sources

- **Discord Signals**: Messages from Goku/Wilson trading channels (last 3 days)
- **GCloud Scanner**: Technical analysis via IBKR (regime, EMA, RSI, EV, R:R)
- **Stock Universe**: ~300 tickers from `config/stock_universe.yaml`
- **Trader Grades**: Historical performance of Discord signal authors
- **Pattern Library**: Win rates of chart patterns (Cup & Handle, Bull Flag, etc.)

## Candidate History

Each scan saves dated candidates to:
`~/trading-agent/workspace/data/candidates_history/candidates_YYYYMMDD_HHMM.json`

Use this for auditing and replay.

## Important Notes

- Scans call the GCloud API (~1 sec per ticker). A 80-ticker scan takes ~2 minutes.
- Always run `scan` before giving stock recommendations — stale candidates are unreliable.
- The scanner requires the GCloud Trading API at http://34.75.9.166:8080 to be running.
- Positions and portfolio commands also require the API.
