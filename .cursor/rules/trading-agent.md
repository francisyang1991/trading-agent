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
- Do not present a symbol as an "opportunity" unless it is **tradeable by our defined criteria**.
- If a pattern is detected but not tradeable yet, **show it as WAIT with explicit blockers/reasons** (e.g., late entry, weak trigger volume, weak breakout, poor R:R).

---

## Infrastructure & Deployment (persistent)

### AWS OpenClaw Instance
- **Region**: us-west-2 (Oregon)
- **IP**: 35.90.4.89
- **SSH Key**: ~/.ssh/openclaw-key.pem
- **User**: ubuntu
- **Path**: /home/ubuntu/trading-agent

**SSH Command:**
```bash
ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89
```

### GCloud Trading VM
- **Zone**: us-east1-b
- **VM Name**: trading-vm
- **Path**: /home/ubuntu/trading-agent

**SSH Command:**
```bash
gcloud compute ssh trading-vm --zone=us-east1-b
```

### Discord Configuration
- **Rich or Die Channel**: 1345123472019423284
- **Bot Token**: Set in environment variable `DISCORD_BOT_TOKEN`
- **User Token**: Set in environment variable `DISCORD_USER_TOKEN` (for scraping)

### Signal Sources (Discord Servers)
- **Goku Server** (Technical Analysis):
  - Channel IDs: 1277321989874385029, 1411717415565393970, 1227315745352847461
- **Wilson Server** (Fundamental Analysis):
  - Channel ID: 1211549165629476924

### API Keys (Environment Variables)
- `DISCORD_BOT_TOKEN` - Discord bot for posting to Rich or Die
- `DISCORD_USER_TOKEN` - Discord user token for scraping Goku/Wilson
- `MINIMAX_API_KEY` - MiniMax LLM for analysis

### Daily Bot Schedule
- **Time**: 11:50 AM PST (19:50 UTC) Monday-Friday
- **Cron**: `50 19 * * 1-5`
- **Script**: workspace/scripts/report_generation/discord_daily_bot.py

### Sync Commands
```bash
# Sync to AWS
./scripts/sync_to_aws.sh

# Sync to GCloud
./scripts/sync_to_gcloud.sh
```
