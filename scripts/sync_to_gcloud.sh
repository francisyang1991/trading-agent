#!/bin/bash
# Sync workspace to GCloud trading-agent VM
# Usage: ./sync_to_gcloud.sh

GCP_VM="trading-vm"
GCP_ZONE="us-east1-b"
REMOTE_DIR="/home/ubuntu/trading-agent"

echo "=== Syncing to GCloud Trading Agent ==="
echo "Target: $GCP_VM ($GCP_ZONE)"

# Sync using gcloud compute scp
gcloud compute scp --recurse \
    --zone=$GCP_ZONE \
    --compress \
    --exclude='venv/*' \
    --exclude='__pycache__/*' \
    --exclude='*.pyc' \
    --exclude='.git/*' \
    --exclude='node_modules/*' \
    --exclude='legacy/*' \
    ./workspace \
    $GCP_VM:$REMOTE_DIR/

# Sync config
gcloud compute scp --recurse \
    --zone=$GCP_ZONE \
    ./config \
    $GCP_VM:$REMOTE_DIR/

# Sync requirements
gcloud compute scp \
    --zone=$GCP_ZONE \
    ./requirements.txt \
    $GCP_VM:$REMOTE_DIR/

echo "=== Sync complete ==="
echo ""
echo "Next steps on GCloud:"
echo "  gcloud compute ssh $GCP_VM --zone=$GCP_ZONE"
echo "  cd $REMOTE_DIR"
echo "  pip install -r requirements.txt"
