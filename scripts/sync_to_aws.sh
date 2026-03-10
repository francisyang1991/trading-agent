#!/bin/bash
# Sync trading-agent to AWS (OpenClaw Discord bot host)
# Usage: ./sync_to_aws.sh
#
# Target: ubuntu@35.90.4.89:/home/ubuntu/trading-agent
# See docs/OPENCLAW_AWS_DEPLOYMENT.md and docs/CITRINI_EMAIL_PIPELINE.md

AWS_HOST="ubuntu@35.90.4.89"
AWS_KEY="~/.ssh/openclaw-key.pem"
REMOTE_DIR="/home/ubuntu/trading-agent"

echo "=== Syncing to AWS ==="
echo "Target: $AWS_HOST:$REMOTE_DIR"

# Sync workspace folder (Discord bots, core_analysis)
rsync -avz --progress \
    -e "ssh -i $AWS_KEY" \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.git/' \
    --exclude 'node_modules/' \
    --exclude 'legacy/' \
    ./workspace/ \
    $AWS_HOST:$REMOTE_DIR/workspace/

# Sync src/ (required for Citrini email pipeline - interactive_bot imports src.email_analysis)
rsync -avz --progress \
    -e "ssh -i $AWS_KEY" \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    ./src/ \
    $AWS_HOST:$REMOTE_DIR/src/

# Sync tools/ (run_citrini_email_check.py, analyze_citrini_emails.py)
rsync -avz --progress \
    -e "ssh -i $AWS_KEY" \
    --exclude '__pycache__/' \
    ./tools/ \
    $AWS_HOST:$REMOTE_DIR/tools/

# Sync config folder
rsync -avz --progress \
    -e "ssh -i $AWS_KEY" \
    ./config/ \
    $AWS_HOST:$REMOTE_DIR/config/

# Sync requirements
rsync -avz --progress \
    -e "ssh -i $AWS_KEY" \
    ./requirements.txt \
    $AWS_HOST:$REMOTE_DIR/

# Sync scripts (services, health monitor, deploy)
rsync -avz --progress \
    -e "ssh -i $AWS_KEY" \
    ./scripts/ \
    $AWS_HOST:$REMOTE_DIR/scripts/

echo "=== Sync complete ==="
echo ""
echo "Next steps on AWS:"
echo "  ssh -i $AWS_KEY $AWS_HOST"
echo "  cd $REMOTE_DIR"
echo "  pip install -r requirements.txt"
echo ""
echo "To install/restart bot services:"
echo "  bash scripts/deploy_bots_aws.sh"
echo ""
echo "For Citrini email pipeline (optional):"
echo "  1. Copy token.json and secret/ to AWS (Gmail OAuth + client secret)"
echo "  2. Set CITRINI_EMAIL_ENABLED=true and ANTHROPIC_API_KEY in workspace/.env"
echo "  3. Restart interactive-bot: sudo systemctl restart interactive-bot"
echo ""
echo "To check bot health:"
echo "  bash scripts/bot_health_monitor.sh"
