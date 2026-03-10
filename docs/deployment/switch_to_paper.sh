#!/bin/bash
# Switch back to PAPER mode (env.list)
# Run ON THE GCP VM after: gcloud compute ssh trading-vm --zone=us-east1-b

set -e

DIR="${TRADING_AGENT_DIR:-/home/francisyang/trading-agent}"
cd "$DIR" || { echo "Error: $DIR not found"; exit 1; }

echo "=== Switching to PAPER mode (env.list) ==="

if docker compose version &>/dev/null; then
    DC="docker compose"
else
    DC="docker-compose"
fi

sudo $DC -f docker-compose.yaml down 2>/dev/null || true
sudo $DC -f docker-compose.yaml --env-file .env --profile full up -d

echo "Paper mode active. Verify: curl -s http://localhost:8080/api/health"
