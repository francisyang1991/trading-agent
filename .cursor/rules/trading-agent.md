## Trading Agent project rules (persistent)

### Code organization (no new wheels)
- **Core logic belongs in `src/`**. Treat `tools/` as thin CLI wrappers only.
- **Always reuse existing modules in `src/`** before writing new utilities. If a capability already exists (e.g., caching, indicators, signals, reporting), extend it instead of duplicating it.
- **If a CLI script in `tools/` grows beyond a thin wrapper**, move the implementation into `src/` and keep a small shim in `tools/` to preserve existing commands.
  - Example: `tools/fundamental_analyzer.py` should delegate to `src/` implementation.

### Backtesting & data policy (cache-first)
- **Backtests must read from the local SQLite cache first** (via `src/data_manager.py`).
- If the DB is missing required history (e.g., requesting `2y` but DB only has `1y`) or is stale, **download the missing data and store it to the DB**, then proceed.
- Avoid direct `yfinance` calls in backtests unless the cache layer is unavailable; prefer fixing the cache layer instead.

### Scanner output quality
- Do not present a symbol as an “opportunity” unless it is **tradeable by our defined criteria**.
- If a pattern is detected but not tradeable yet, **show it as WAIT with explicit blockers/reasons** (e.g., late entry, weak trigger volume, weak breakout, poor R:R).
