# Intraday 1m Replay Report - AMD

## Output Files
- `price_position_overlay.png`: 单图展示价格/买卖点/仓位
- `price_signals.png`: 日内价格 + VWAP + 买卖点
- `sizing_timeline.png`: 核心仓/总仓/卫星仓数量变化
- `equity_curve.png`: 回放权益曲线
- `trades.csv`: 逐笔交易动作
- `metrics_summary.csv`: 指标汇总

## Quick Stats
- Trade actions: 45
- Final equity: 100360.90

## vs Buy & Hold
- Buy & hold return: 0.139%
- T0 strategy total return: 0.361%
- Alpha (T0 gain over B&H): +0.222%
- Costs model: commission=1.00bps, slippage=1.50bps
- Replay controls: cooldown=10 bars, min_hold=6 bars, max_trades_day=8
