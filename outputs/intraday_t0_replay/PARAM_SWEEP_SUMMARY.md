# T0 Strategy Parameter Sweep Summary

## Stock Picking
- **Timeframe**: Hourly (60m bars)
- **Symbols**: AMD, TSLA, NVDA, CAT (PLTR excluded as strong-trend outlier)

## Parameter Grid Tested
| Parameter | Values |
|-----------|--------|
| zscore_entry | 2.0, 2.4, 2.8, 3.2 |
| min_vwap_distance_bps | 35, 55, 80, 100 |
| satellite_max_pct_of_core | 30%, 40%, 50%, 60%, 70% |
| cooldown / hold / max_trades_day | (6,4,12), (10,6,8), (14,8,6) |

## Best Balanced Config (ex-PLTR)
| Param | Value |
|-------|-------|
| zscore_entry_buy/sell | -3.2 / 3.2 |
| zscore_exit_to_mean | 1.12 |
| min_vwap_distance_bps | 35 |
| satellite_max_pct_of_core | **70%** (30/70 core/sat) |
| min_bars_between_trades | 10 |
| min_hold_bars | 6 |
| max_trades_per_day | 8 |

**Performance (4 stocks, 8 days)**: avg alpha +0.38%, avg Sharpe 2.14, pct_positive_alpha 50%

## Position Size Comparison (Best Config, 4 stocks)
| Core/Sat | satellite_pct | Avg Alpha | Avg Trades |
|----------|---------------|-----------|------------|
| 70/30 | 0.30 | +2.45% | 50 |
| 60/40 | 0.40 | +2.51% | 53 |
| 50/50 | 0.50 | +2.58% | 58 |
| 40/60 | 0.60 | +2.65% | 60 |
| 30/70 | 0.70 | +2.70% | 62 |

Higher satellite % → more alpha and more trades. 70% satellite (30/70) is best for this sample.

## Outputs
- **Config**: `config/intraday_t0_config_best.yaml`
- **Sweep results**: `outputs/intraday_t0_replay/sweep_results.csv` (full), `sweep_results_ex_pltr.csv` (ex-PLTR)
- **Charts**: `outputs/intraday_t0_replay/best_sat030/`, `best_sat040/`, ..., `best_sat070/` per symbol
- **Daily PnL**: `daily_pnl.csv` in each symbol folder
- **Overlay**: Bottom panel = Daily PnL bars (T0 vs B&H)
