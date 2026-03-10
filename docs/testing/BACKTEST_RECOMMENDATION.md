# Backtest: Open Source vs Existing — Recommendation

## Decision: **Use Our Existing Tools**

### Why Not Open Source (Backtrader, Zipline, VectorBT)?

| Library | Pros | Cons for Our Use Case |
|---------|------|------------------------|
| **Backtrader** | Mature, event-driven, indicators | Different paradigm; our picker is date-anchored, not bar-by-bar. Would need to wrap picker as a "strategy" that emits signals at anchor dates. |
| **Zipline** | Quantopian heritage, pipeline-based | Deprecated (Quantopian shut down). Pipeline model doesn't match our three-layer picker flow. |
| **VectorBT** | Fast vectorized backtest | Optimized for indicator-based strategies. Our flow: picker → point-in-time picks → TP/SL simulation. Not a natural fit. |
| **bt (flexible backtest)** | Lightweight | Would require rewriting our walk-forward logic. |

### Why Our Existing Tools Are Good Enough

1. **Point-in-time correctness**  
   `validate_three_layer_walkforward_tpsl.py` runs the picker at historical anchor dates with no lookahead. Fundamentals and prices are truncated correctly. Open-source engines are usually bar-by-bar and don’t natively support “run picker at date X, get list, simulate forward.”

2. **Integration**  
   Our validator uses:
   - `DataManager` (SQLite cache)
   - `FundamentalSnapshotService` (point-in-time snapshots)
   - `run_picker_at_date` (lookback)
   - Config YAML  
   All of this is already wired. A new library would need adapters for each.

3. **Fit for purpose**  
   We need:
   - Walk-forward validation at 3m/6m/9m/12m anchors
   - TP/SL simulation (intra-bar High/Low checks)
   - RSI entry filter
   - Win rate, drawdown, trade list  
   The validator already does this.

4. **Existing backtest tools**  
   - `src/backtest/engine.py` — SignalEngine, PositionManager; built for volume-pullback / trend strategies.  
   - `tools/backtest.py` — EMA/RSI strategies, single-symbol or portfolio.  
   - `tools/portfolio_backtest.py` — Takes performance CSV (entry/exit), simulates portfolio.  
   These target different strategies. The walk-forward validator is the right tool for the three-layer picker.

### When to Reconsider Open Source

- If we add many new strategy types (e.g. options, multi-asset, intraday) and need a common engine.
- If we need advanced features (e.g. realistic fills, slippage models, margin) that our validator doesn’t support.
- If we want to publish or share backtests in a standard format.

### Recommendation

**Keep using our existing walk-forward validator.** It is correct, integrated, and appropriate for the three-layer picker. Add a thin adapter only if we need to feed its output into `BacktestEngine` or `portfolio_backtest` for shared reporting.
