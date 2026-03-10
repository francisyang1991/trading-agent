# T0 Rule Picker: Daily vs Hourly Timeframe Comparison

## Setup
- **Rule**: Liquidity > $500M, 8-bar trend ∈ [-3%, +3%], avg range > 0.10%, score = range / max(|trend|, 1)
- **Daily picker**: 1d bars, last 8 days trend, daily dollar vol
- **Hourly picker**: 60m bars, last ~52 bars (~8 RTH days) trend, daily dollar vol (resampled)
- **Replay**: 8 days 1m, low-turnover config, core_qty=50, initial_equity=100k

## Picks (differentiator)
| Timeframe | Symbols |
|-----------|---------|
| Daily     | AMD, TSLA, NVDA, PLTR, **LLY** |
| Hourly    | AMD, TSLA, NVDA, PLTR, **CAT** |

## Per-Symbol Alpha (8 days)
| Symbol | Daily α | Hourly α | B&H return |
|--------|---------|----------|------------|
| AMD    | -0.73%  | -0.73%   | 1.07%      |
| TSLA   | +0.69%  | +0.69%   | -0.19%     |
| NVDA   | -1.67%  | -1.67%   | 1.95%      |
| PLTR   | -5.78%  | -5.78%   | 6.39%      |
| LLY    | +1.34%  | —        | -2.30%     |
| CAT    | —       | **+2.34%** | -2.34%   |

## Group Average
| Group        | Avg Alpha | Avg Trades | Avg Sharpe | Avg Max DD |
|--------------|-----------|------------|------------|------------|
| Daily picked | **-1.23%** | 50.4     | 2.02       | 1.26%      |
| Hourly picked| **-1.03%** | 50.8     | 2.32       | 1.06%      |

## Conclusion
**Hourly picker** edges out daily: -1.03% vs -1.23% avg alpha. The difference comes from **CAT** (hourly pick, +2.34% α) vs **LLY** (daily pick, +1.34% α). Both groups share AMD, TSLA, NVDA, PLTR; PLTR’s strong trend (-5.78% α) hurts both. Hourly’s finer resolution may better capture mean-reversion setups (e.g. CAT).
