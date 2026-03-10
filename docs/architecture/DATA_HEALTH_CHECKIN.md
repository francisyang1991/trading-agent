# Data Health Check-in — Weekly/Monthly Job

This document describes the data foundation health audit and how to run it as a recurring job.

## Purpose

- **Monitor data coverage**: Price (stock_daily) and fundamentals (parquet, snapshots)
- **Identify gaps**: What's missing, what percentage, sample symbols
- **Optional blacklist**: Add symbols that fail Yahoo fetch so we don't re-scan them

## Tools

| Tool | Purpose |
|------|---------|
| `tools/data_health_audit.py` | Full scan of DB + fundamentals, produces markdown report |
| `tools/sync_blacklist_from_nasdaq.py` | Add Test Issue, ETF, deficient (D), bankrupt (Q) symbols to blacklist |
| `scripts/data_health_checkin.sh` | Wrapper for cron; runs audit and logs output |

## Usage

### Manual run

```bash
# Audit only (report to results/picker/data_health_report.md)
python tools/data_health_audit.py

# Sync blacklist from Nasdaq (Test Issue, ETF, deficient, bankrupt)
python tools/sync_blacklist_from_nasdaq.py
python tools/sync_blacklist_from_nasdaq.py --dry-run  # Preview

# Audit + add missing symbols to blacklist (use after full picker run)
python tools/data_health_audit.py --add-to-blacklist
```

### Weekly cron (Sunday 2 AM)

```bash
0 2 * * 0 /path/to/trading_agent/scripts/data_health_checkin.sh
```

### Monthly cron (1st of month 2 AM)

```bash
0 2 1 * * /path/to/trading_agent/scripts/data_health_checkin.sh
```

## Report contents

1. **Price data**: Listed vs in-DB, missing count/%, bar distribution (≥260, 60–259, <60)
2. **Fundamentals**: Quarterly parquet coverage, snapshot cache dates, field-level coverage
3. **Blacklist**: Current count, optionally symbols added this run

## When to use `--add-to-blacklist`

Run **after** a full price fetch (e.g. `run_three_layer_picker.py --fundamental-only`). Symbols still missing from the DB after that run are likely Yahoo failures (delisted, illiquid, etc.). Adding them to the blacklist avoids re-scanning on future runs.

## Related config

- `config/picker_config.yaml` → `technical.min_bars` (260), `min_bars_recent_ipo` (130)
- Recent IPOs with 130+ bars are allowed; 260 is not enforced for them.
