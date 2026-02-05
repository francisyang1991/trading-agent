#!/bin/bash
# Sync workspace to OpenClaw AWS instance
# Usage: ./sync_to_aws.sh

AWS_HOST="ubuntu@35.90.4.89"
AWS_KEY="~/.ssh/openclaw-key.pem"
REMOTE_DIR="/home/ubuntu/trading-agent"

echo "=== Syncing to OpenClaw AWS ==="
echo "Target: $AWS_HOST:$REMOTE_DIR"

# Sync workspace folder
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

echo "=== Sync complete ==="
echo ""
echo "Next steps on AWS:"
echo "  ssh -i $AWS_KEY $AWS_HOST"
echo "  cd $REMOTE_DIR"
echo "  pip install -r requirements.txt"
echo "  npm install (if needed)"
