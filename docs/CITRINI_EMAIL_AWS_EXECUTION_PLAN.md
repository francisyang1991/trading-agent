# Citrini Email Pipeline — AWS Execution Plan

**Purpose:** Enable and verify the Citrini email → LLM → Discord pipeline on the AWS Discord bot host. This plan is for an AI agent or human to execute when the original implementer cannot SSH to AWS.

---

## 1. Context

- **AWS host:** `ubuntu@35.90.4.89`
- **SSH key:** `~/.ssh/openclaw-key.pem`
- **Remote dir:** `/home/ubuntu/trading-agent`
- **Sync script:** `scripts/sync_to_aws.sh` (run locally; syncs `workspace/`, `src/`, `tools/`, `config/`, `scripts/`, `requirements.txt`)
- **Bot service:** `interactive-bot` (systemd)
- **Env file:** `workspace/.env` (loaded by systemd)

---

## 2. Prerequisites (Before Starting)

- [ ] `sync_to_aws.sh` has been run recently (code is on AWS)
- [ ] Local `token.json` exists (Gmail OAuth for `citrinishared@gmail.com`)
- [ ] Local `secret/client_secret_*.json` exists
- [ ] `ANTHROPIC_API_KEY` or `ZAI_API_KEY` available
- [ ] SSH access to AWS works: `ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89`

---

## 3. Execution Steps

### Step A: Sync Code (Run Locally)

```bash
cd /path/to/trading_agent
./scripts/sync_to_aws.sh
```

### Step B: Copy Gmail Credentials to AWS

```bash
scp -i ~/.ssh/openclaw-key.pem token.json ubuntu@35.90.4.89:~/trading-agent/
scp -i ~/.ssh/openclaw-key.pem -r secret/ ubuntu@35.90.4.89:~/trading-agent/
```

### Step C: SSH to AWS and Add Env Vars

```bash
ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89
cd ~/trading-agent
```

Edit `workspace/.env` (or append):

```bash
# Citrini email pipeline
CITRINI_EMAIL_ENABLED=true
CITRINI_EMAIL_INTERVAL_MIN=60
ANTHROPIC_API_KEY=your_key_here
# or: ZAI_API_KEY=your_key_here
```

### Step D: Install Optional Dependencies (Z.AI Only)

If using Z.AI (GLM) instead of Anthropic:

```bash
~/trading-agent/venv/bin/pip install zai-sdk
```

### Step E: Restart the Bot

```bash
sudo systemctl restart interactive-bot
```

### Step F: Verify Service

```bash
sudo systemctl status interactive-bot
sudo journalctl -u interactive-bot -n 50 --no-pager
```

Look for: no import errors, bot online, no crash loop.

### Step G: Test Manually in Discord

1. In the Discord channel where the bot is present, type: `!citrini`
2. Expected: bot responds with either "Checking Citrini inbox..." then trade ideas, or "No new Citrini emails to process" if no unseen emails

### Step H: Verify Scheduler (Optional)

If `CITRINI_EMAIL_ENABLED=true`, the scheduler runs every 60 min. Wait up to 60 min or check logs:

```bash
sudo journalctl -u interactive-bot -f
```

Look for log lines like: `Citrini: sent N trade idea message(s) to Discord` or `Citrini email check failed: ...`

---

## 4. Verification Checklist

- [ ] `src/email_analysis/` exists on AWS at `~/trading-agent/src/email_analysis/`
- [ ] `token.json` exists at `~/trading-agent/token.json`
- [ ] `secret/client_secret_*.json` exists at `~/trading-agent/secret/`
- [ ] `CITRINI_EMAIL_ENABLED=true` in `workspace/.env`
- [ ] `ANTHROPIC_API_KEY` or `ZAI_API_KEY` in `workspace/.env`
- [ ] `interactive-bot` service is active/running
- [ ] `!citrini` command responds in Discord (no crash, no import error)

---

## 5. Troubleshooting

| Symptom | Action |
|---------|--------|
| `ModuleNotFoundError: No module named 'src.email_analysis'` | Ensure `src/` was synced. Re-run `sync_to_aws.sh`. Check `sys.path` in interactive_bot: `_TRADING_AGENT_ROOT` should resolve to `~/trading-agent`. |
| `Gmail client secret file not found` | Copy `secret/` to AWS: `scp -r secret/ ubuntu@35.90.4.89:~/trading-agent/` |
| `token.json not found` | Copy `token.json` to AWS: `scp token.json ubuntu@35.90.4.89:~/trading-agent/` |
| `ZAI_API_KEY is required` / `ANTHROPIC_API_KEY is required` | Add the key to `workspace/.env` on AWS. Restart: `sudo systemctl restart interactive-bot` |
| `!citrini` returns "Citrini module not available" | Import failed. Check `journalctl -u interactive-bot` for traceback. Likely missing `src/` or `token.json`/`secret/`. |
| Bot crashes on startup | Check `journalctl -u interactive-bot -n 100`. Common: missing env vars, wrong Python path, missing venv packages. |

---

## 6. Success Criteria

- Bot starts without error
- `!citrini` runs without import/crash
- Either: (a) trade ideas posted to Discord, or (b) "No new Citrini emails to process" (both are valid)
- No repeated errors in logs about Gmail, token, or API key

---

## 7. References

- `docs/CITRINI_EMAIL_PIPELINE.md` — full pipeline docs
- `docs/WORKLOG.md` — "Feb 15, 2026 — Citrini Email → Discord Pipeline" section
- `scripts/sync_to_aws.sh` — sync script and inline instructions
