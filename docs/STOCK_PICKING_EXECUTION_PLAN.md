# Stock Picking Execution Plan

## 1) Data Validity Policy (Fail Closed)

- Date keys are non-negotiable:
  - Price cache: `Date`
  - Quarterly fundamentals: `report_date`, `disclosure_date`
- `NaN/NaT` in required date keys is a bug signal. Rows are dropped; never date-prefilled.
- `None` is allowed only for optional numeric fundamentals when provider genuinely has no value.
- No zero/placeholder prefills for missing growth metrics.

## 2) Embedded Daily Workflow (No Manual Verification Required)

### Step A: Preflight Gate (auto)

- Triggered inside `tools/run_three_layer_picker.py` before picker stages.
- Actions:
  - purge invalid OHLCV rows
  - run cache health checks on the current run universe
  - enforce strict thresholds from `config/picker_config.yaml` (`preflight` block)
- Result:
  - PASS => continue
  - FAIL => abort picker (unless explicitly configured `on_fail: warn`)

### Step B: Stock Picker

- Runs 3 layers:
  1. Technical (`RS`, `>200MA`, near 52w high)
  2. Fundamental (EPS/revenue growth)
  3. Quality (ROE, margin rank, debt)
- Writes stage outputs to `results/picker/`.

### Step C: Incremental Earnings Refresh (scheduled or pre-open)

- `tools/incremental_earnings_refresh.py`
- Detect new/revised earnings rows, ticker-scoped snapshot invalidation, targeted revalidation/scanner reruns, audit tables.

### Step D: Earnings Session Calendar (for execution timing)

- `tools/earnings_calendar.py`
- Labels each event as `pre-market` / `post-market` / `in-market` / `unknown`.

## 3) Current Fundamental Picker Constraints

- Config source: `config/picker_config.yaml`
- Layer 1 (Technical):
  - `rs_threshold >= 80`
  - `price >= 0.80 * 52w_high`
  - `min_bars >= 260`
- Layer 2 (Fundamental):
  - `eps_yoy >= 0.25`
  - `revenue_growth >= 0.10`
  - `revenue_acceleration`: disabled (`null`)
  - `require_surprise_non_negative`: `false`
- Layer 3 (Quality):
  - `roe >= 0.10`
  - `gm_rank >= 60`
  - `debt_to_equity <= 1.2`
- Post process:
  - share-class dedupe enabled
  - Remove etfs
  - max 4 picks per industry/concept

## 4) What To Do After Stock Picking

- Generate execution-ready trade list with:
  - entry/stop/targets from current setup
  - risk-per-trade caps and position sizes
  - earnings-session proximity flags (avoid new entries near binary events)
- Run walk-forward spot checks on impacted windows if earnings changed.
- Produce end-of-run artifacts:
  - picks CSV
  - risk budget table
  - execution queue (priority + liquidity + catalyst notes)

## 5) Refactor Plan (aligned with `.cursor/rules/trading-agent.md`)

### Phase 1: Move heavy tool logic into `src/` (1 sprint)

- Move cache-health core from `tools/cache_health_check.py` -> `src/picker/cache_health.py`
- Move incremental earnings refresh core from `tools/incremental_earnings_refresh.py` -> `src/picker/incremental_refresh.py`
- Keep `tools/` scripts as thin CLI shims.

### Phase 2: Shared data contracts + audit model (1 sprint)

- Define typed result contracts for:
  - cache health report
  - earnings refresh run/release/snapshot/validation deltas
- Centralize CSV read/write helpers and schema checks.

### Phase 3: Orchestrated production pipeline (1 sprint)

- Add one orchestrator command (preflight -> refresh -> picker -> post-pick risk pack).
- Add deterministic exit codes and machine-readable run manifest.

### Phase 4: Test hardening (continuous)

- Unit tests for NaN-date handling and strict no-prefill rules.
- Integration tests for end-to-end preflight gating and incremental ticker-scoped refresh.

## 6) Immediate Execution Checklist (for Monday Open)

1. Run embedded picker (preflight auto-runs):
  - `python3 tools/run_three_layer_picker.py`
2. If pre-open earnings updates expected, run incremental refresh first:
  - `python3 tools/incremental_earnings_refresh.py --months-back 3,6,9,12`
3. Generate earnings session timing for final candidate set:
  - `python3 tools/earnings_calendar.py --symbols-file results/picker/three_layer_picks.csv --days-ahead 14`
4. Execute only picks passing risk budget and event-timing constraints.

