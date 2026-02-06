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

### Interactive Bot (responds to @mentions)
- **Script**: workspace/scripts/discord_bot/interactive_bot.py
- **Service**: trading-bot.service (systemd on AWS)
- **Commands**:
  - `@bot $TICKER` - Deep analysis of a stock
  - `!analyze TICKER` - Same as above
  - `!signals TICKER` - List recent signals
  - `!help_trading` - Show help

### Discord Bot Setup (Required)
1. Go to https://discord.com/developers/applications
2. Select your bot application
3. Go to **Bot** section
4. Enable **MESSAGE CONTENT INTENT** under Privileged Gateway Intents
5. Save changes

### Sync Commands
```bash
# Sync to AWS
./scripts/sync_to_aws.sh

# Sync to GCloud
./scripts/sync_to_gcloud.sh
```

---

## CI / Multi-Agent Workflow (persistent)

### ⚠️ ENFORCEMENT: Branch Protection Enabled
**The `expand-universe` branch is PROTECTED by GitHub branch protection rules.**
- **Direct pushes are BLOCKED** - all changes MUST go through Pull Requests.
- **CI checks are REQUIRED** - the following checks MUST pass before merge:
  - ✅ Lint (ruff)
  - ✅ Unit Tests (Tier 1)
  - ✅ Integration Tests (Tier 2)
  - ✅ Bot Tests (Tier 3)
  - ✅ CI Gate (All Checks Must Pass)
- **No bypassing** - even admins cannot merge without passing CI.
- **No force pushes** - branch history is protected.

**This enforcement applies to ALL agents and ALL changes. There are no exceptions.**

### Golden Rules
1. **Always pull from mainline** before starting any new feature:
   ```bash
   git checkout expand-universe && git pull origin expand-universe
   git checkout -b feature/<your-feature>
   ```
2. **All changes go through PRs** - never push directly to `main` or `expand-universe` (enforced by branch protection).
3. **CI must pass** before any PR can be merged (enforced by branch protection - merge button disabled until all checks pass).
4. **Each agent creates its own feature branch** from the latest mainline.
5. **Run local tests before pushing**:
   ```bash
   pytest -m unit       # Tier 1 - fast, pure logic
   pytest -m integration  # Tier 2 - mocked external deps
   pytest -m bot        # Tier 3 - Discord mocks
   ```

### CI Pipeline (GitHub Actions)
- **Trigger**: On every PR to `main` / `expand-universe`, and on push to those branches.
- **Jobs**: Lint (ruff) -> Unit Tests -> Integration Tests -> Bot Tests -> Coverage.
- **Gate**: All jobs must pass before merge is allowed (enforced by branch protection).
- **Config**: `.github/workflows/ci.yml`

### Test Markers
| Marker | Description | Speed |
|--------|------------|-------|
| `unit` | Pure logic, no external deps | Fast (<1 min) |
| `integration` | Mocked external deps | Moderate |
| `bot` | Discord bot utilities (mocked) | Fast |
| `e2e` | Live connections (manual only) | Slow |

### Test Files
- `pytest.ini` - Markers, settings, timeouts
- `tests/test_ev_calculator.py` - EV, Kelly, risk/reward, position sizing
- `tests/test_regime.py` - Regime classification, momentum
- `tests/test_risk_manager.py` - Circuit breaker, drawdown, position/portfolio limits
- `tests/test_sizing.py` - Position sizer (fixed, Kelly, vol-target)
- `tests/test_signals.py` - Entry engine, strategy matrix
- `tests/test_llm_analyzer.py` - LLM response parsing, local sentiment analysis
- `tests/test_data_manager.py` - SQLite cache layer
- `tests/test_discord_utils.py` - Ticker extraction, message splitting

### Claude Code Discord Agent (@claudecode on AWS)
- **Agent Script**: workspace/scripts/discord_bot/claude_discord_agent.py (runs on AWS)
- **Local Helper**: workspace/scripts/discord_bot/claude_code_bot.py (for manual sends from Cursor)
- **App ID**: 1469202811207290911
- **Service**: claude-discord.service (systemd on AWS)
- **How it works**: User @mentions @claudecode on Discord -> piped to `claude -p` CLI -> response sent back
