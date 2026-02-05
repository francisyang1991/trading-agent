# Cloud Deployment Lessons Learned

**Critical knowledge gained from real deployment troubleshooting. Reference this document BEFORE every cloud deployment.**

---

## Executive Summary: The 7 Deployment Gotchas

| Issue | Symptom | Root Cause | Fix |
|-------|---------|------------|-----|
| 1. Env Variable Substitution | `"IB_USERNAME" not set` warning | `${VAR}` reads from host, not `env_file` | Create `.env` file |
| 2. TrustedIPs Rejection | `TimeoutError()` connecting to IB Gateway | `TrustedIPs=127.0.0.1` blocks Docker network | Use port 4004 (socat proxy) |
| 3. Broken Healthcheck | Container stuck at `health: starting` | `nc` not installed in image | Force-start dependent containers |
| 4. Docker Compose v2 | `docker-compose: command not found` | Old syntax on new systems | Use `docker compose` (space, not hyphen) |
| 5. Container Name Conflicts | `container name already in use` | Old container still exists | `docker stop && docker rm` first |
| 6. IB Gateway Login Time | Agent starts before gateway ready | Login takes 30-60 seconds | Wait for "Login has completed" in logs |
| 7. GCP Firewall Rules | IAP tunnel fails for VNC/other ports | Only port 22 and 8080 allowed | Update firewall to include all needed ports |

---

## Lesson 1: Docker Compose Environment Variable Substitution

### The Problem

```yaml
# docker-compose.yaml
services:
  ib-gateway:
    env_file:
      - env.list          # Loads INTO container environment
    environment:
      - TWS_USERID=${IB_USERNAME}   # Substituted from HOST environment!
      - TWS_PASSWORD=${IB_PASSWORD}
```

**Warning message:**
```
WARN[0000] The "IB_USERNAME" variable is not set. Defaulting to a blank string.
WARN[0000] The "IB_PASSWORD" variable is not set. Defaulting to a blank string.
```

### Why This Happens

Docker Compose processes `${VAR}` substitutions **before** starting containers, reading from:
1. Host shell environment
2. `.env` file in the same directory as `docker-compose.yaml`

The `env_file` directive loads variables **into** the container's runtime environment, but this happens **after** substitution occurs.

### The Fix

**Always create BOTH files:**

```bash
# env.list - Variables loaded INTO container
IB_USERNAME=your_username
IB_PASSWORD=your_password
IB_TRADING_MODE=paper

# .env - Variables for docker-compose substitution (same content)
cp env.list .env
```

### Deployment Checklist Item
- [ ] `.env` file exists alongside `docker-compose.yaml`
- [ ] `.env` contains all variables used with `${VAR}` syntax
- [ ] Both `.env` and credential files have `chmod 600`

---

## Lesson 2: IB Gateway TrustedIPs Blocks Docker Network

### The Problem

```
API connection failed: TimeoutError()
```

Even when IB Gateway logs show "Login has completed" and ports are listening, the trading agent cannot connect.

### Why This Happens

Inside the IB Gateway container, `jts.ini` has:

```ini
[IBGateway]
TrustedIPs=127.0.0.1
```

This means **only localhost (127.0.0.1) can connect**. Your trading agent in another Docker container has an IP like `172.18.0.3`, which gets rejected.

### The Fix

Use **port 4004** instead of 4002. The `gnzsnz/ib-gateway` image runs a `socat` proxy on port 4004 that:
1. Listens on all interfaces (0.0.0.0:4004)
2. Forwards to localhost:4002
3. Bypasses the TrustedIPs restriction

```bash
# In .env and env.list
IB_PORT=4004  # NOT 4002!
```

### How to Verify

```bash
# Check ports inside the container
docker exec saiyan-ibgateway cat /proc/net/tcp | head -10

# Decode hex ports: 0x0FA4 = 4004, 0x0FA2 = 4002
# Both should show 0A (LISTEN) state
```

### Deployment Checklist Item
- [ ] `IB_PORT=4004` (not 4002) in configuration
- [ ] Verify port 4004 is listening: `docker exec <container> ss -tln | grep 4004`

---

## Lesson 3: Broken Healthcheck Blocks Dependent Containers

### The Problem

```bash
$ docker ps
CONTAINER ID   STATUS
abc123         Up 5 min (health: starting)   # Never becomes healthy!
def456         Created                        # Never starts!
```

The `trading-agent` has `depends_on: ibgateway: condition: service_healthy`, but ibgateway's healthcheck never passes.

### Why This Happens

The `gnzsnz/ib-gateway` image defines a healthcheck using `nc` (netcat):

```yaml
healthcheck:
  test: ["CMD", "nc", "-z", "localhost", "4002"]
```

But **`nc` is not installed** in the container:

```
OCI runtime exec failed: exec: "nc": executable file not found in $PATH
```

### The Fix

**Force-start the dependent container** after verifying the gateway has logged in:

```bash
# 1. Check IB Gateway login status
docker logs saiyan-ibgateway | grep "Login has completed"

# 2. If logged in, force-start the trading agent
docker start saiyan-agent

# 3. Verify it's running
docker logs saiyan-agent --tail 20
```

### Alternative: Override Healthcheck

In your `docker-compose.yaml`, override with a working command:

```yaml
services:
  ib-gateway:
    image: ghcr.io/gnzsnz/ib-gateway:stable
    healthcheck:
      test: ["CMD-SHELL", "cat /proc/net/tcp | grep -q 0FA2"]  # 0FA2 = 4002 in hex
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 120s  # Give IB Gateway 2 min to login
```

### Deployment Checklist Item
- [ ] After `docker compose up`, check healthcheck status: `docker inspect <container> --format='{{.State.Health.Status}}'`
- [ ] If stuck at `starting`, check logs for "Login has completed" and force-start dependent containers

---

## Lesson 4: Docker Compose V2 Syntax

### The Problem

```bash
$ sudo docker-compose up -d
sudo: docker-compose: command not found
```

### Why This Happens

- **Docker Compose V1**: Standalone binary, invoked as `docker-compose` (with hyphen)
- **Docker Compose V2**: Built into Docker CLI, invoked as `docker compose` (with space)

Newer Ubuntu/Debian systems have V2, which doesn't include the `docker-compose` binary.

### The Fix

```bash
# Use V2 syntax
sudo docker compose up -d

# NOT
sudo docker-compose up -d
```

### Deployment Checklist Item
- [ ] Use `docker compose` (space) not `docker-compose` (hyphen)
- [ ] Verify version: `docker compose version`

---

## Lesson 5: Container Name Conflicts

### The Problem

```
Error response from daemon: Conflict. The container name "/saiyan-agent" is already in use by container "abc123..."
```

### Why This Happens

When you run `docker compose up` after a failed deployment, old containers may still exist (even if stopped).

### The Fix

```bash
# Stop and remove conflicting containers
sudo docker stop saiyan-agent saiyan-ibgateway 2>/dev/null
sudo docker rm saiyan-agent saiyan-ibgateway 2>/dev/null

# Then deploy
sudo docker compose up -d
```

Or use the nuclear option:

```bash
# Remove ALL containers (careful in production!)
sudo docker compose down --remove-orphans
sudo docker compose up -d
```

### Deployment Checklist Item
- [ ] Before redeployment: `docker ps -a` to check for existing containers
- [ ] Remove old containers before creating new ones with same name

---

## Lesson 6: IB Gateway Login Takes Time

### The Problem

Trading agent starts immediately and fails to connect because IB Gateway is still logging in.

```
TimeoutError()  # Gateway not ready yet
```

### Why This Happens

IB Gateway login sequence:
1. Start container (~5 sec)
2. Load IBC automation (~10 sec)
3. Open login dialog (~5 sec)
4. Enter credentials (~5 sec)
5. 2FA if required (~30+ sec)
6. Connect to IBKR servers (~10 sec)
7. Configuration dialogs (~5 sec)

**Total: 60-120 seconds** before API is ready

### The Fix

**Wait for login confirmation** before starting dependent services:

```bash
# Watch logs for successful login
docker logs -f saiyan-ibgateway 2>&1 | grep -m1 "Login has completed"
echo "Gateway ready!"

# Now start trading agent
docker start saiyan-agent
```

### Deployment Checklist Item
- [ ] Wait for "Login has completed" in gateway logs before expecting connections
- [ ] Set `start_period: 120s` in healthcheck to allow login time

---

## Lesson 7: GCP Firewall Rules for IAP Tunnel

### The Problem

```bash
$ gcloud compute start-iap-tunnel trading-vm 5900 --local-host-port=localhost:5900 --zone=us-east1-b
ERROR: Error while connecting [4003: 'failed to connect to backend'].
```

### Why This Happens

GCP firewall rules must explicitly allow each port for IAP tunneling. The default rule might only include SSH (22) and HTTP (8080).

### The Fix

```bash
# Update firewall rule to include all needed ports
gcloud compute firewall-rules update allow-iap-tunnel \
    --rules=tcp:22,tcp:8080,tcp:5900,tcp:4001,tcp:4002

# Or create a new rule
gcloud compute firewall-rules create allow-iap-vnc \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:5900 \
    --source-ranges=35.235.240.0/20 \
    --target-tags=trading-agent
```

### Deployment Checklist Item
- [ ] List required ports: SSH (22), GUI (8080), VNC (5900), etc.
- [ ] Verify firewall rules include all ports: `gcloud compute firewall-rules describe allow-iap-tunnel`

---

## Pre-Deployment Checklist

Run through this checklist **EVERY TIME** before deploying to cloud:

### Environment Configuration
- [ ] `.env` file exists (for docker compose substitution)
- [ ] `env.list` file exists (for container environment)
- [ ] `IB_PORT=4004` (not 4002) for Docker deployments
- [ ] All credential files have `chmod 600`
- [ ] Credentials are NOT committed to git

### Docker Setup
- [ ] Use `docker compose` (V2 syntax with space)
- [ ] Healthcheck has adequate `start_period` (120s minimum)
- [ ] No existing containers with conflicting names

### GCP/Cloud Setup
- [ ] VM is running: `gcloud compute instances list`
- [ ] Firewall rules include all needed ports
- [ ] IAP API is enabled

### Post-Deployment Verification
- [ ] IB Gateway shows "Login has completed" in logs
- [ ] Port 4004 is listening inside gateway container
- [ ] Trading agent shows "Connected to IB Gateway"
- [ ] GUI responds at http://localhost:8080 (via tunnel)

---

## Quick Diagnostic Commands

```bash
# Check all container status
docker ps -a

# Check healthcheck status
docker inspect <container> --format='{{json .State.Health}}' | jq

# Verify ports are listening inside container
docker exec <container> cat /proc/net/tcp

# Check network connectivity between containers
docker exec trading-agent ping -c1 ib-gateway

# View recent logs
docker logs <container> --tail 50

# Check environment variables in container
docker exec <container> env | grep IB_

# Force restart with clean slate
docker compose down --remove-orphans && docker compose up -d
```

---

## Document History

| Date | Issue | Resolution |
|------|-------|------------|
| 2026-02-04 | Environment variable substitution warnings | Created `.env` file from `env.list` |
| 2026-02-04 | TimeoutError connecting to IB Gateway | Changed port from 4002 to 4004 (socat proxy) |
| 2026-02-04 | Container stuck at "health: starting" | Force-started dependent container |
| 2026-02-04 | VNC IAP tunnel failed | Updated GCP firewall to include port 5900 |

---

**Remember: Every "quick" deployment takes 3x longer due to these issues. Use this document!**
