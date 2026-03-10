# Deployment Lessons & Production Checklist

**Critical knowledge gained from real deployment troubleshooting. Reference this document BEFORE every cloud deployment.**

This document merges the former DEPLOYMENT_LESSONS.md (cloud/Docker) and DEBUG_LESSONS.md (production incidents).

---

## Part A: Docker & Cloud Deployment Gotchas

### Executive Summary

| Issue | Symptom | Fix |
|-------|---------|-----|
| 1. Env Variable Substitution | `"IB_USERNAME" not set` warning | Create `.env` file alongside `docker-compose.yaml` |
| 2. TrustedIPs Rejection | `TimeoutError()` connecting to IB Gateway | Use port 4004 (socat proxy), not 4002 |
| 3. Broken Healthcheck | Container stuck at `health: starting` | Force-start dependent containers or override healthcheck |
| 4. Docker Compose v2 | `docker-compose: command not found` | Use `docker compose` (space, not hyphen) |
| 5. Container Name Conflicts | `container name already in use` | `docker stop && docker rm` first |
| 6. IB Gateway Login Time | Agent starts before gateway ready | Wait for "Login has completed" in logs |
| 7. GCP Firewall Rules | IAP tunnel fails | Update firewall to include all needed ports |

### Lesson 1: Docker Compose Environment Variable Substitution

Docker Compose processes `${VAR}` substitutions **before** starting containers, reading from the host shell or `.env` file. The `env_file` directive loads variables **into** the container, but **after** substitution.

**Fix:** Always create BOTH `env.list` (for container) and `.env` (for compose substitution).

### Lesson 2: IB Gateway TrustedIPs Blocks Docker Network

`jts.ini` has `TrustedIPs=127.0.0.1`, blocking containers on `172.x.x.x`. Use **port 4004** (socat proxy on 0.0.0.0) instead of 4002.

### Lesson 3: Broken Healthcheck

The `gnzsnz/ib-gateway` image's `nc`-based healthcheck fails because `nc` is not installed. Override with:
```yaml
healthcheck:
  test: ["CMD-SHELL", "cat /proc/net/tcp | grep -q 0FA2"]
  interval: 30s
  timeout: 10s
  retries: 5
  start_period: 120s
```

### Lesson 4: Docker Compose V2 Syntax

Use `docker compose` (space) not `docker-compose` (hyphen) on newer systems.

### Lesson 5: Container Name Conflicts

Before redeployment: `docker ps -a` to check for existing containers. Remove old ones first.

### Lesson 6: IB Gateway Login Takes 60-120 Seconds

Wait for "Login has completed" in gateway logs before starting dependent services.

### Lesson 7: GCP Firewall Rules for IAP Tunnel

Update firewall rules to include all ports: SSH (22), GUI (8080), VNC (5900), IB (4002/4004).

---

## Part B: Production Debug Lessons

### Lesson 8: Always Import What You Use

`auto_executor.py` used `sys.path` but never imported `sys`, crashing `interactive_bot.py` on startup.

**Prevention:** After editing ANY Python file, run `python -c "import <module>; print('OK')"`.

### Lesson 9: API Field Names Must Match Exactly

The GCloud Trading API expects `"ticker"` and `"limit_price"`, not `"symbol"` and `"price"`.

**Prevention:** Before changing API-facing code, grep `trade_executor.py` for actual field names.

### Lesson 10: Settings Files Override Environment Variables

`~/.claude/settings.json` had a stale API key that overrode the systemd env var.

**Prevention:** When changing any API key, update ALL locations (systemd, settings.json, .env).

### Lesson 11: Signal Patterns Need to Match Real Message Formats

Ticker extraction regex was too narrow (`$TICKER` only). Added multi-pattern extraction for `Long/Short: TICKER TICKER` format.

### Lesson 12: Systemd Environment Must Export ALL Required Vars

Use `set -a` / `set +a` in shell scripts to auto-export all vars, or use systemd's `EnvironmentFile=` directive.

### Lesson 13: Subprocess Cleanup Requires Process Group Kill

Start subprocesses with `start_new_session=True` and kill the entire process group via `os.killpg()`.

---

## Pre-Deployment Checklist

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
- [ ] VM is running
- [ ] Firewall rules include all needed ports
- [ ] IAP API is enabled

### Post-Deployment Verification
- [ ] IB Gateway shows "Login has completed" in logs
- [ ] Port 4004 is listening inside gateway container
- [ ] Trading agent shows "Connected to IB Gateway"
- [ ] GUI responds at http://localhost:8080 (via tunnel)

### Production Deploy Steps
```bash
# PRE-DEPLOY
python3 -m py_compile workspace/scripts/discord_bot/interactive_bot.py
cd workspace/scripts/discord_bot && python3 -c "import interactive_bot; print('OK')"
cd ~/trading-agent && pytest -m unit

# DEPLOY
./scripts/sync_to_aws.sh

# POST-DEPLOY (on AWS)
cd ~/trading-agent/workspace/scripts/discord_bot
~/trading-agent/venv/bin/python -c "import interactive_bot; print('OK')"
sudo systemctl restart trading-bot.service claude-discord.service
sleep 10 && systemctl is-active trading-bot.service claude-discord.service
journalctl -u trading-bot.service -n 20 --no-pager | grep -i error
bash ~/trading-agent/scripts/bot_health_monitor.sh
```

---

## Quick Diagnostic Commands

```bash
docker ps -a                                                    # Container status
docker inspect <container> --format='{{json .State.Health}}'    # Healthcheck
docker exec <container> cat /proc/net/tcp                       # Ports
docker exec trading-agent ping -c1 ib-gateway                  # Network
docker logs <container> --tail 50                               # Logs
docker compose down --remove-orphans && docker compose up -d    # Clean restart
```

---

## Key Files Reference

| File | Location | Purpose |
|------|----------|---------|
| `interactive_bot.py` | `workspace/scripts/discord_bot/` | Main trading bot |
| `signal_pipeline.py` | `workspace/scripts/discord_bot/` | Pipeline, order generation |
| `trade_executor.py` | `workspace/scripts/discord_bot/` | API contract (field names) |
| `auto_executor.py` | `workspace/scripts/discord_bot/` | Market-open auto-execution |

---

## Document History

| Date | Issue | Resolution |
|------|-------|------------|
| 2026-02-04 | Env variable substitution warnings | Created `.env` from `env.list` |
| 2026-02-04 | TimeoutError connecting to IB Gateway | Changed port from 4002 to 4004 |
| 2026-02-04 | Container stuck at "health: starting" | Force-started dependent container |
| 2026-02-04 | VNC IAP tunnel failed | Updated GCP firewall |
| 2026-02-12 | Import crash in auto_executor.py | Added sys import |
| 2026-02-12 | API field name mismatch | Fixed to use ticker/limit_price |
