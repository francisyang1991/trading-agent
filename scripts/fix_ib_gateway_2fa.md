# Fix IB Gateway "Second Factor Authentication" — Manual Step Required

## What's happening

IB Gateway is **stuck on 2FA**. It showed:
```
Second Factor Authentication initiated
```

IBKR requires you to approve the login on your phone. **No automation can do this** — you must approve it manually.

## Steps to fix

### 1. Approve 2FA on your phone

- Open the **IBKR Mobile** app (or IBKR GlobalTrader)
- You should see a login request / 2FA prompt
- **Approve it** (tap "Approve" or enter the code)

### 2. Wait 1–2 minutes

IB Gateway needs time to finish the login and open the API port.

### 3. Restart the trading agent

After the gateway is logged in, restart the trading agent so it reconnects:

```bash
gcloud compute ssh trading-vm --zone=us-east1-b --command="sudo docker restart saiyan-agent"
```

### 4. Verify

```bash
curl -s -H "X-API-Key: saiyan-trade-2026" "http://34.75.9.166:8080/api/health"
```

If `"connected": true`, you're done.

### 5. Optional: Use VNC to log in manually

If 2FA doesn't appear on your phone, you can log in via VNC:

1. Create an IAP tunnel for VNC (port 5900):
   ```bash
   gcloud compute start-iap-tunnel trading-vm 5900 --local-host-port=localhost:5900 --zone=us-east1-b
   ```

2. Connect VNC client to `localhost:5900` (password: set in docker-compose, often `changeme`)

3. Complete the IB Gateway login in the VNC session

## Why this happens

- IBKR enforces 2FA for security
- After a restart or session expiry, IB Gateway needs a fresh 2FA approval
- The Gateway container cannot automate 2FA approval

## Frequency

- Typically needed after: VM reboot, container restart, or session expiry (often ~24h)
- Consider approving 2FA soon after a restart so the trading system stays connected
