# Strategy A/B Test Protocol

Use this protocol for any strategy changes that affect signals, targets, exits,
or filtering. The goal is consistent, comparable results before merging updates.

## Benchmark Dataset
- Universe file: `config/test_universe.yaml`
- Snapshot: `data/universe_snapshots/test_universe_20260120.yaml`
- Period: `2y`
- Minimum days to outcome: `1`
- Include WAIT signals: as specified in the change notes

## Preload Cache (Recommended)
```
python3 tools/preload_test_universe.py --period 2y
```

## Required A/B Matrix
Run the 2x2 matrix below for any **target** or **exit** changes:

1) Old target logic + no partial exit  
2) Old target logic + partial exit  
3) Capped target logic + no partial exit  
4) Capped target logic + partial exit  

## Command
```
python3 tools/ab_test_runner.py \
  --universe-file config/test_universe.yaml \
  --period 2y \
  --min-confidence 0.5 \
  --include-wait \
  --min-days-to-outcome 1 \
  --score-full 85 --score-half 70 --score-quarter 55 --score-min 55 \
  --earnings-window-days 5 \
  --export results/ab_test_summary.csv
```

## Output
- Summary CSV: `results/ab_test_summary.csv`
- Markdown report: `results/ab_test_summary.md`

## Stop Loss A/B
For stop loss changes, run the stop-loss A/B sweep:
```
python3 tools/ab_test_stoploss.py \
  --universe-file config/test_universe.yaml \
  --period 2y \
  --min-confidence 0.5 \
  --include-wait \
  --min-days-to-outcome 1 \
  --earnings-window-days 5 \
  --ema9-pcts 0.95,0.96,0.97,0.98 \
  --atr-mults 0.2,0.3,0.4 \
  --export results/ab_test_stoploss.csv \
  --report-md results/ab_test_stoploss.md
```

## Range-Bound Filter A/B
```

## Target Method A/B
```
python3 tools/ab_test_targets.py \
  --universe-file config/test_universe.yaml \
  --period 2y \
  --min-confidence 0.5 \
  --include-wait \
  --min-days-to-outcome 1 \
  --earnings-window-days 5 \
  --export results/ab_test_targets.csv \
  --report-md results/ab_test_targets.md
```
python3 tools/ab_test_range_filter.py \
  --universe-file config/test_universe.yaml \
  --period 2y \
  --min-confidence 0.5 \
  --include-wait \
  --min-days-to-outcome 1 \
  --earnings-window-days 5 \
  --export results/ab_test_range_filter.csv \
  --report-md results/ab_test_range_filter.md
```
- Report changes with win rate and weighted EV deltas.
