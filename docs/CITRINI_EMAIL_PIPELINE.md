# Citrini Email → Discord Pipeline

Fetches new emails from `citrini@substack.com`, extracts trade ideas via LLM, and posts to Discord.

## Prerequisites

- Gmail OAuth: `token.json` (authenticate as `citrinishared@gmail.com`)
- Google client secret: `secret/client_secret_*.json`
- LLM API key: `ANTHROPIC_API_KEY` or `ZAI_API_KEY`

## Deployment Options

**→ For a step-by-step handoff to another AI:** see [CITRINI_EMAIL_AWS_EXECUTION_PLAN.md](CITRINI_EMAIL_AWS_EXECUTION_PLAN.md).

### Option A: Discord Bot on AWS (recommended)

Use the interactive bot's built-in scheduler on the AWS instance.

**1. Sync code to AWS**

```bash
./scripts/sync_to_aws.sh
```

This syncs `workspace/`, `src/`, `tools/`, `config/`, `scripts/`, and `requirements.txt` to `ubuntu@35.90.4.89:/home/ubuntu/trading-agent`.

**2. Copy Gmail credentials to AWS**

```bash
scp -i ~/.ssh/openclaw-key.pem token.json ubuntu@35.90.4.89:~/trading-agent/
scp -i ~/.ssh/openclaw-key.pem -r secret/ ubuntu@35.90.4.89:~/trading-agent/
```

**3. Add env vars to `workspace/.env` on AWS**

```bash
CITRINI_EMAIL_ENABLED=true
CITRINI_EMAIL_INTERVAL_MIN=60   # optional, default 60
ANTHROPIC_API_KEY=your_key      # or ZAI_API_KEY
```

**4. Restart the bot**

```bash
ssh -i ~/.ssh/openclaw-key.pem ubuntu@35.90.4.89
cd ~/trading-agent
sudo systemctl restart interactive-bot
```

The scheduler runs every `CITRINI_EMAIL_INTERVAL_MIN` minutes. Manual trigger: `!citrini` in Discord.

### Option B: Standalone Cron Job

Run hourly without the Discord bot:

```bash
# crontab -e
5 * * * * cd /path/to/trading_agent && source .venv/bin/activate && ANTHROPIC_API_KEY=xxx python tools/run_citrini_email_check.py
```

**Note:** `run_citrini_email_check.py` currently prints to stdout only. To post to Discord from cron, add a Discord webhook POST in the script, or use Option A (bot scheduler).

## Manual Testing

```bash
# Test fetch + LLM (prints Discord-formatted output)
ANTHROPIC_API_KEY=xxx python tools/run_citrini_email_check.py

# Full analysis to files
python tools/analyze_citrini_emails.py --provider gmail --limit 10
```

## State

Processed email IDs are stored in `data/email/citrini_processed.json`. Delete this file to reprocess all emails (e.g. for testing).
