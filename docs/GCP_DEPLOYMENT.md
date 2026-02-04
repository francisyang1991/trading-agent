# Google Cloud Deployment Guide

This guide covers deploying the trading agent system on Google Cloud Platform for 24/7 operation.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Google Cloud Platform                        │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              Compute Engine VM (e2-medium)                │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │  │
│  │  │ IB Gateway  │  │   Trading   │  │   Trading GUI   │  │  │
│  │  │   (Docker)  │◄─┤    Agent    │◄─┤   (Flask:8080)  │  │  │
│  │  │  Port 4001  │  │  (Python)   │  │                 │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                    Cloud IAP Tunnel                              │
│                              │                                   │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                         Your Browser
```

## Prerequisites

1. **Google Cloud Account** with billing enabled
2. **IBKR Account** with API access enabled
3. **gcloud CLI** installed locally
4. **Docker** installed (for building images)

---

## Step 1: Create GCP Project

```bash
# Create new project
gcloud projects create trading-agent-prod --name="Trading Agent"

# Set as default
gcloud config set project trading-agent-prod

# Enable required APIs
gcloud services enable compute.googleapis.com
gcloud services enable iap.googleapis.com
```

## Step 2: Create Compute Engine VM

```bash
# Create VM instance
gcloud compute instances create trading-vm \
    --zone=us-east1-b \
    --machine-type=e2-medium \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=50GB \
    --boot-disk-type=pd-ssd \
    --tags=trading-agent \
    --metadata=startup-script='#!/bin/bash
apt-get update
apt-get install -y docker.io docker-compose python3-pip
systemctl enable docker
systemctl start docker'
```

### VM Sizing Recommendations

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 30 GB SSD | 50 GB SSD |
| **Monthly Cost** | ~$25 | ~$50 |

## Step 3: Configure Firewall

```bash
# Allow IAP tunneling (secure access without public IP)
gcloud compute firewall-rules create allow-iap-tunnel \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:22,tcp:8080 \
    --source-ranges=35.235.240.0/20 \
    --target-tags=trading-agent

# IMPORTANT: Do NOT expose 4001/4002 (IB Gateway) to internet!
```

## Step 4: Deploy IB Gateway with Docker

### Create docker-compose.yml on VM

```bash
# SSH into VM
gcloud compute ssh trading-vm --zone=us-east1-b

# Create directory structure
mkdir -p ~/trading-agent/{config,logs,data}
cd ~/trading-agent
```

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  ibgateway:
    image: ghcr.io/gnzsnz/ib-gateway:stable
    container_name: ibgateway
    restart: unless-stopped
    environment:
      - TWS_USERID=${IB_USERNAME}
      - TWS_PASSWORD=${IB_PASSWORD}
      - TRADING_MODE=paper  # Change to 'live' for production
      - TWS_ACCEPT_INCOMING=yes
      - READ_ONLY_API=no
      - VNC_SERVER_PASSWORD=changeme  # For debugging
    ports:
      - "4001:4001"  # Live
      - "4002:4002"  # Paper
      - "5900:5900"  # VNC (optional)
    volumes:
      - ./config/ibc:/root/ibc
      - ./logs/gateway:/root/Jts
    healthcheck:
      test: ["CMD", "nc", "-z", "localhost", "4002"]
      interval: 30s
      timeout: 10s
      retries: 3

  trading-agent:
    build: .
    container_name: trading-agent
    restart: unless-stopped
    depends_on:
      ibgateway:
        condition: service_healthy
    environment:
      - IB_HOST=ibgateway
      - IB_PORT=4002  # Paper trading
      - IB_CLIENT_ID=1
      - IB_TRADING_MODE=paper
      - GUI_PORT=8080
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
      - ./logs/agent:/app/logs
    command: python -m tools.trading_gui

networks:
  default:
    driver: bridge
```

### Create Dockerfile for Trading Agent

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data directories
RUN mkdir -p /app/data /app/logs

# Expose GUI port
EXPOSE 8080

# Default command
CMD ["python", "-m", "tools.trading_gui"]
```

### Create Environment File

```bash
# Create .env file (NEVER commit this!)
cat > .env << 'EOF'
IB_USERNAME=your_ib_username
IB_PASSWORD=your_ib_password
EOF
chmod 600 .env
```

## Step 5: Deploy and Start Services

```bash
# Start services
docker-compose up -d

# Check logs
docker-compose logs -f ibgateway
docker-compose logs -f trading-agent

# Verify IB Gateway is connected
docker exec ibgateway cat /root/Jts/*/log*.txt | tail -20
```

## Step 6: Secure Access with IAP Tunnel

Instead of exposing ports to the internet, use Identity-Aware Proxy:

```bash
# From your LOCAL machine, create tunnel
gcloud compute start-iap-tunnel trading-vm 8080 \
    --local-host-port=localhost:8080 \
    --zone=us-east1-b

# Now access GUI at http://localhost:8080 (tunneled securely)
```

### Create Persistent Tunnel Script

```bash
#!/bin/bash
# save as ~/scripts/trading-tunnel.sh

while true; do
    echo "Starting IAP tunnel..."
    gcloud compute start-iap-tunnel trading-vm 8080 \
        --local-host-port=localhost:8080 \
        --zone=us-east1-b
    echo "Tunnel closed, reconnecting in 5s..."
    sleep 5
done
```

---

## Monitoring & Alerts

### Cloud Monitoring Setup

```bash
# Install monitoring agent on VM
curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
sudo bash add-google-cloud-ops-agent-repo.sh --also-install

# Create uptime check
gcloud monitoring uptime-check-configs create trading-agent-check \
    --display-name="Trading Agent Health" \
    --http-check="host=localhost,path=/api/health,port=8080" \
    --period=60s
```

### Alert Policies

Create alerts for:
1. **VM Down**: CPU usage = 0 for 5 minutes
2. **Gateway Disconnected**: Health check fails 3 times
3. **High Latency**: API response > 5 seconds
4. **Daily Loss Limit**: P&L < -2% (custom metric)

---

## Backup & Recovery

### Automated Backups

```bash
# Add to crontab
0 0 * * * docker exec trading-agent sqlite3 /app/data/stock_cache.db ".backup /app/data/backup_$(date +\%Y\%m\%d).db"
0 1 * * * gsutil cp /home/user/trading-agent/data/backup_*.db gs://your-bucket/backups/
```

### Disaster Recovery Checklist

1. VM fails → Create new VM from snapshot
2. Gateway token expires → Re-authenticate via VNC
3. Data corruption → Restore from Cloud Storage backup

---

## Cost Estimate

| Service | Monthly Cost |
|---------|-------------|
| Compute Engine (e2-medium) | $25 |
| Persistent Disk (50GB SSD) | $8 |
| Cloud NAT (egress) | $5-10 |
| Cloud Logging (5GB) | Free |
| **Total** | **~$40-45/month** |

---

## Security Checklist

- [ ] Enable 2FA on GCP account
- [ ] Use IAP tunnel (never expose ports publicly)
- [ ] Store IB credentials in Secret Manager
- [ ] Enable VPC firewall logging
- [ ] Set up daily backup to Cloud Storage
- [ ] Use paper trading for first 2 weeks
- [ ] Review IB activity logs daily
- [ ] Enable Cloud Audit Logs

---

## Quick Commands Reference

```bash
# SSH to VM
gcloud compute ssh trading-vm --zone=us-east1-b

# Start tunnel to GUI
gcloud compute start-iap-tunnel trading-vm 8080 --local-host-port=localhost:8080 --zone=us-east1-b

# View logs
docker-compose logs -f

# Restart services
docker-compose restart

# Stop everything
docker-compose down

# Update trading agent
git pull && docker-compose build && docker-compose up -d

# Check IB Gateway connection
docker exec ibgateway netstat -an | grep 4002
```

---

## Troubleshooting

### IB Gateway Won't Connect

1. Check credentials in `.env`
2. Verify 2FA is handled (use IBKR mobile app)
3. Check if IP is whitelisted in IBKR settings
4. View VNC at port 5900 for visual debugging

### Trading Agent Can't Reach Gateway

1. Verify Docker network: `docker network inspect trading-agent_default`
2. Check gateway health: `docker exec ibgateway nc -z localhost 4002`
3. Ensure `IB_HOST=ibgateway` (not localhost)

### High Latency

1. Check VM region matches your broker's servers
2. Consider upgrading to e2-standard-4
3. Verify no other processes consuming resources
