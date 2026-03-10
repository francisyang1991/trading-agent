# Intraday T+0 Continuous Improvement Log

This log tracks gaps found during replay/live-paper runs and the follow-up actions.

## 2026-02-27 TSLA 1m replay review

### Findings vs initial MVP plan

1. **Cross-session visual gaps reduced readability**
   - Symptom: charts looked like 24h timeline with overnight blank spans.
   - Impact: makes intraday review harder and may hide session-level behavior.
   - Status: fixed by plotting on compressed bar index (session-only axis labels).

2. **EOD force-flatten loop caused repeated flips near 15:55-15:59**
   - Symptom: flatten then immediate re-entry/exit in the same close window.
   - Impact: unrealistic churn and inflated trade count.
   - Status: fixed by enforcing one-time daily flatten and blocking post-flatten entries.

3. **Cost model was missing (commission/slippage)**
   - Symptom: replay assumed frictionless fills.
   - Impact: PnL and turnover quality were overstated.
   - Status: fixed in replay tool with execution slippage and per-trade commission:
     - `execution.slippage_bps`
     - `execution.commission_bps`

4. **Trade telemetry lacked execution-level economics**
   - Symptom: `trades.csv` only had raw action rows.
   - Impact: weak post-trade diagnostics.
   - Status: improved with:
     - `exec_price`
     - `gross_notional`
     - `commission`

### Next improvements (priority queue)

1. **Round-trip PnL ledger for satellite legs**
   - Pair sell/buyback legs and output realized PnL per closed leg.
   - Add `realized_pnl`, `net_pnl_after_costs` in `trades.csv`.

2. **Time-window parity with MVP engine**
   - Replay currently uses signal thresholds and EOD flatten, but should also enforce:
     - 09:30-10:00 reduce-only
     - 10:00-11:30 + 13:30-15:00 main
     - 11:30-13:30 half-size

3. **Microstructure-aware fill model**
   - Replace constant bps slippage with dynamic spread/volatility/volume model.
   - Penalize aggressive orders in low-liquidity minutes.

4. **Capacity and turnover guardrails**
   - Add max trades/hour and min holding minutes to suppress micro-churn.
   - Add minimum expected edge > estimated cost before allowing trade.

5. **Plan-aligned reporting**
   - Extend `metrics_summary.csv` with:
     - turnover
     - avg_hold_minutes
     - cost_bps_paid
     - edge_after_cost

## 2026-02-27 v2 tuning cycle (cost-aware + anti-churn)

### Implemented changes

1. **Session-compressed plotting axis**
   - Charts now use bar-index axis with session timestamps as labels.
   - Removes overnight whitespace while preserving time context.

2. **One-time EOD flatten gate**
   - 15:55 force-flatten now executes once/day.
   - Blocks repeated close-window open/close loops.

3. **Cost-aware replay execution**
   - Added execution model:
     - `execution.commission_bps`
     - `execution.slippage_bps`
   - Costs now flow into cash/equity and trade rows.

4. **Anti-churn controls**
   - `replay.min_bars_between_trades`
   - `replay.min_hold_bars`
   - `replay.max_trades_per_day`
   - Entry quality filters (require reversal hint before extreme entries).

5. **Round-trip approximation fields**
   - Added `realized_pnl_approx` for satellite-leg close accounting.

### Current config (v2)

- `zscore_entry_buy = -2.4`
- `zscore_entry_sell = 2.4`
- `zscore_exit_to_mean = 0.8`
- `min_bars_between_trades = 6`
- `min_hold_bars = 4`
- `max_trades_per_day = 12`

### Result snapshot (5d, 1m replay)

- TSLA: trades/day **23.2 -> 14.0**, return **0.56% -> 0.65%**, Sharpe **1.17 -> 1.33**
- NVDA: trades/day **19.8 -> 12.6**, return **-0.79% -> -0.52%**, Sharpe **-1.15 -> -0.71**
- AAPL (v2): return **4.53%**, maxDD **1.44%**
- MSFT (v2): return **1.46%**, maxDD **3.54%**

### Next cycle candidates

1. Add per-symbol regime switch (trend day vs mean-reversion day) before enabling T+0 loop.
2. Replace fixed bps slippage with spread/volatility-dependent model.
3. Add no-trade band around VWAP to prevent noise-region churn.
4. Promote `realized_pnl_approx` to strict FIFO closed-lot accounting with full cost attribution.

