# Testing Plan for >65% Win Rate Strategy

## Objective
The primary goal is to find a winning strategy that achieves a win rate strictly greater than 65% with a minimum drawdown. We will test and iterate on the Three-Layer Stock Picker using historical walk-forward validation.

## Rationale
The default parameters in `picker_config.yaml` are extremely strict:
- `min_eps_yoy: 0.25`
- `min_revenue_growth: 0.10`
- `min_roe: 0.10`
- `min_gm_rank: 60.0`
This strictness drops ~90% of the universe. In historical testing, it produced only 3 active picks across the last year. A sample size of 3 trades is insufficient to validate a >65% win rate statistically.

## Testing Strategy
1. **Relax Constraints**: Lower the fundamental constraint thresholds to increase the number of candidate stocks and increase the number of trades evaluated in the walk-forward periods.
   - `min_eps_yoy: 0.10`
   - `min_revenue_growth: 0.05`
   - `min_roe: 0.05`
   - `min_gm_rank: 40.0`
2. **Execute Walk-Forward Validation**: Run `validate_three_layer_walkforward.py` over 3, 6, 9, 12-month historical anchors using the relaxed parameters.
3. **Analyze Results**: Measure the aggregate win rate (hit rate) and drawdowns.
4. **Implement Stop Loss / Take Profit (if needed)**: If holding the picks passively until today fails to achieve >65% WR with minimum drawdown, implement a trailing stop or fixed take-profit target in the strategy performance calculation to lock in gains and cut losses.

## Success Criteria
- Sample Size: >20 trades across the 12-month period.
- Hit Rate (Win Rate): > 65%.
- Drawdown: Minimal.
