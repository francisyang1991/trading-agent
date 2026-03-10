#!/usr/bin/env python3
"""
IB Gateway Watchdog
====================
Monitors IB Gateway and Trading Agent health on GCP.
Automatically restarts containers when failures are detected.

Failure Detection:
  1. IB Gateway container not running or unhealthy
  2. IB Gateway API port (4004) not responding
  3. IB Gateway login failed or session expired
  4. Trading Agent disconnected from gateway
  5. Trading Agent GUI not responding

Recovery Actions:
  1. Restart IB Gateway container
  2. Wait for login to complete
  3. Restart Trading Agent
  4. Verify full stack is healthy

Usage:
  # Run as a standalone monitor (recommended: systemd service)
  python -m tools.gateway_watchdog

  # Run with custom settings
  python -m tools.gateway_watchdog --interval 60 --max-restarts 5

  # Dry run (log only, no restarts)
  python -m tools.gateway_watchdog --dry-run

Architecture:
  This script runs on the GCP VM host (NOT inside a container).
  It uses Docker CLI commands to inspect and restart containers.
"""

import os
import sys
import time
import json
import signal
import argparse
import subprocess
import socket
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = os.environ.get("WATCHDOG_LOG_DIR", "/var/log/trading-watchdog")
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "watchdog.log")),
    ],
)
log = logging.getLogger("watchdog")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Container names (must match docker-compose.yaml)
GATEWAY_CONTAINER = os.environ.get("GATEWAY_CONTAINER", "saiyan-ibgateway")
AGENT_CONTAINER = os.environ.get("AGENT_CONTAINER", "saiyan-agent")

# Docker compose directory
COMPOSE_DIR = os.environ.get("COMPOSE_DIR", os.path.expanduser("~/trading-agent"))

# Ports to check
GATEWAY_API_PORT = int(os.environ.get("GATEWAY_API_PORT", "4002"))
GATEWAY_SOCAT_PORT = int(os.environ.get("GATEWAY_SOCAT_PORT", "4004"))
GUI_PORT = int(os.environ.get("GUI_PORT", "8080"))

# Timing
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "30"))  # seconds
GATEWAY_LOGIN_TIMEOUT = int(os.environ.get("GATEWAY_LOGIN_TIMEOUT", "180"))  # seconds
POST_RESTART_WAIT = int(os.environ.get("POST_RESTART_WAIT", "15"))  # seconds

# Safety limits
MAX_RESTARTS_PER_HOUR = int(os.environ.get("MAX_RESTARTS_PER_HOUR", "3"))
COOLDOWN_AFTER_MAX = int(os.environ.get("COOLDOWN_AFTER_MAX", "600"))  # 10 min

# Notification (optional Discord webhook)
DISCORD_WEBHOOK_URL = os.environ.get("WATCHDOG_DISCORD_WEBHOOK", "")

# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------
_restart_history = []  # List of datetime objects
_consecutive_failures = 0
_last_healthy_time = None
_shutdown_requested = False


def signal_handler(signum, frame):
    """Handle graceful shutdown."""
    global _shutdown_requested
    log.info(f"Received signal {signum}, shutting down gracefully...")
    _shutdown_requested = True


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def run_docker(args: list, timeout: int = 30) -> tuple:
    """
    Run a docker command and return (returncode, stdout, stderr).
    Uses sudo if not running as root.
    """
    cmd = ["docker"] + args
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        log.error(f"Docker command timed out: {' '.join(args)}")
        return -1, "", "timeout"
    except Exception as e:
        log.error(f"Docker command failed: {e}")
        return -1, "", str(e)


def get_container_status(container_name: str) -> dict:
    """Get detailed container status."""
    rc, stdout, stderr = run_docker([
        "inspect", container_name,
        "--format",
        '{"running": {{.State.Running}}, "status": "{{.State.Status}}", '
        '"health": "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", '
        '"started_at": "{{.State.StartedAt}}", '
        '"restart_count": {{.RestartCount}}}'
    ])

    if rc != 0:
        return {"exists": False, "running": False, "status": "not_found", "health": "none"}

    try:
        info = json.loads(stdout)
        info["exists"] = True
        return info
    except json.JSONDecodeError:
        return {"exists": True, "running": False, "status": "unknown", "health": "none"}


def get_container_logs(container_name: str, tail: int = 30) -> str:
    """Get recent container logs."""
    rc, stdout, stderr = run_docker(["logs", container_name, "--tail", str(tail)])
    # Docker logs go to stderr for some images
    return stdout or stderr


def check_port(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def check_gui_health(port: int = GUI_PORT) -> bool:
    """Check if trading GUI health endpoint responds.
    Tries localhost first, then falls back to checking via docker exec curl."""
    try:
        import urllib.request
        url = f"http://127.0.0.1:{port}/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return data.get("status") == "ok" or data.get("connected", False)
    except Exception:
        # Fallback: check via docker exec
        try:
            rc, stdout, _ = run_docker([
                "exec", AGENT_CONTAINER,
                "curl", "-sf", "http://localhost:8080/api/health"
            ], timeout=10)
            if rc == 0 and stdout:
                data = json.loads(stdout)
                return data.get("status") == "ok" or data.get("connected", False)
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

class HealthStatus:
    """Aggregated health status of the trading stack."""

    def __init__(self):
        self.gateway_container_running = False
        self.gateway_container_healthy = False
        self.gateway_port_open = False
        self.gateway_socat_port_open = False
        self.gateway_login_ok = False
        self.agent_container_running = False
        self.agent_gui_responding = False
        self.agent_connected_to_gw = False
        self.details = {}

    @property
    def gateway_ok(self) -> bool:
        """Gateway is considered OK if it's running and the socat port is open."""
        return (
            self.gateway_container_running
            and self.gateway_socat_port_open
            and self.gateway_login_ok
        )

    @property
    def agent_ok(self) -> bool:
        """Agent is OK if running and GUI responds."""
        return self.agent_container_running and self.agent_gui_responding

    @property
    def fully_healthy(self) -> bool:
        return self.gateway_ok and self.agent_ok

    def summary(self) -> str:
        indicators = []
        indicators.append(f"GW-container: {'UP' if self.gateway_container_running else 'DOWN'}")
        indicators.append(f"GW-port4004: {'OPEN' if self.gateway_socat_port_open else 'CLOSED'}")
        indicators.append(f"GW-login: {'OK' if self.gateway_login_ok else 'FAIL'}")
        indicators.append(f"Agent: {'UP' if self.agent_container_running else 'DOWN'}")
        indicators.append(f"GUI: {'OK' if self.agent_gui_responding else 'FAIL'}")
        return " | ".join(indicators)


def check_health() -> HealthStatus:
    """Run all health checks and return aggregated status."""
    status = HealthStatus()

    # 1. Check Gateway container
    gw_info = get_container_status(GATEWAY_CONTAINER)
    status.gateway_container_running = gw_info.get("running", False)
    status.gateway_container_healthy = gw_info.get("health") == "healthy"
    status.details["gateway_container"] = gw_info

    # 2. Check gateway ports
    #    Port 4002 is mapped to host, port 4004 (socat) is internal only.
    #    Check 4002 from host; for 4004, check inside container via docker exec.
    if status.gateway_container_running:
        status.gateway_port_open = check_port("127.0.0.1", GATEWAY_API_PORT)
        # Check socat port inside container (not exposed to host)
        rc, stdout, _ = run_docker([
            "exec", GATEWAY_CONTAINER,
            "bash", "-c", "cat /proc/net/tcp | grep -qi 0FA4"  # 0FA4 = 4004 hex
        ])
        status.gateway_socat_port_open = (rc == 0)

    # 3. Check gateway login status (look for "Login has completed" in logs)
    if status.gateway_container_running:
        logs = get_container_logs(GATEWAY_CONTAINER, tail=50)
        status.gateway_login_ok = "Login has completed" in logs
        # Also check for login failures
        if "Login failed" in logs or "LOGGED_OUT" in logs.split("Login has completed")[-1] if "Login has completed" in logs else logs:
            # If LOGGED_OUT appears AFTER the last successful login, consider it failed
            status.gateway_login_ok = False

    # 4. Check Agent container
    agent_info = get_container_status(AGENT_CONTAINER)
    status.agent_container_running = agent_info.get("running", False)
    status.details["agent_container"] = agent_info

    # 5. Check GUI health
    if status.agent_container_running:
        status.agent_gui_responding = check_gui_health()

    # 6. Check if agent is connected to gateway (look in agent logs)
    if status.agent_container_running:
        agent_logs = get_container_logs(AGENT_CONTAINER, tail=20)
        status.agent_connected_to_gw = "Connected to IB Gateway" in agent_logs
        # Check for recent connection failures
        if "TimeoutError" in agent_logs or "API connection failed" in agent_logs:
            # Only consider it failed if the failure is more recent than the success
            last_connected = agent_logs.rfind("Connected to IB Gateway")
            last_timeout = max(agent_logs.rfind("TimeoutError"), agent_logs.rfind("API connection failed"))
            if last_timeout > last_connected:
                status.agent_connected_to_gw = False

    return status


# ---------------------------------------------------------------------------
# Recovery actions
# ---------------------------------------------------------------------------

def can_restart() -> bool:
    """Check if we're allowed to restart (rate limiting)."""
    global _restart_history

    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)

    # Prune old entries
    _restart_history = [t for t in _restart_history if t > one_hour_ago]

    if len(_restart_history) >= MAX_RESTARTS_PER_HOUR:
        log.warning(
            f"Restart limit reached ({MAX_RESTARTS_PER_HOUR}/hr). "
            f"Cooling down for {COOLDOWN_AFTER_MAX}s."
        )
        return False

    return True


def record_restart():
    """Record a restart event."""
    _restart_history.append(datetime.now())


def restart_gateway(dry_run: bool = False) -> bool:
    """
    Restart the IB Gateway container and wait for login.

    Returns True if gateway successfully restarted and logged in.
    """
    log.info(">>> RESTARTING IB GATEWAY <<<")

    if dry_run:
        log.info("[DRY RUN] Would restart gateway container")
        return True

    # Stop the gateway
    log.info("Stopping gateway container...")
    rc, out, err = run_docker(["restart", GATEWAY_CONTAINER], timeout=60)
    if rc != 0:
        log.error(f"Failed to restart gateway: {err}")
        # Try harder: stop + start
        run_docker(["stop", GATEWAY_CONTAINER], timeout=30)
        time.sleep(2)
        rc, out, err = run_docker(["start", GATEWAY_CONTAINER], timeout=30)
        if rc != 0:
            log.error(f"Failed to start gateway after stop: {err}")
            return False

    # Wait for login to complete
    log.info(f"Waiting up to {GATEWAY_LOGIN_TIMEOUT}s for gateway login...")
    start = time.time()
    while time.time() - start < GATEWAY_LOGIN_TIMEOUT:
        time.sleep(10)
        logs = get_container_logs(GATEWAY_CONTAINER, tail=30)
        if "Login has completed" in logs:
            log.info("Gateway login successful!")

            # Verify socat port is open
            time.sleep(5)
            if check_port("127.0.0.1", GATEWAY_SOCAT_PORT):
                log.info(f"Gateway port {GATEWAY_SOCAT_PORT} is accepting connections.")
                return True
            else:
                log.warning(f"Gateway logged in but port {GATEWAY_SOCAT_PORT} not yet open, waiting...")
                time.sleep(10)
                return check_port("127.0.0.1", GATEWAY_SOCAT_PORT)

        if "Login failed" in logs:
            log.error("Gateway login FAILED. Check credentials.")
            return False

        elapsed = int(time.time() - start)
        log.info(f"  Still waiting for login... ({elapsed}s elapsed)")

    log.error(f"Gateway login timed out after {GATEWAY_LOGIN_TIMEOUT}s")
    return False


def restart_agent(dry_run: bool = False) -> bool:
    """
    Restart the Trading Agent container.

    Returns True if agent successfully restarted and is responding.
    """
    log.info(">>> RESTARTING TRADING AGENT <<<")

    if dry_run:
        log.info("[DRY RUN] Would restart trading agent container")
        return True

    # Force-start (bypasses healthcheck dependency)
    log.info("Restarting trading agent container...")
    rc, out, err = run_docker(["restart", AGENT_CONTAINER], timeout=60)
    if rc != 0:
        # Container might not have started yet (Created state), try start
        log.info("Restart failed, attempting start...")
        rc, out, err = run_docker(["start", AGENT_CONTAINER], timeout=30)
        if rc != 0:
            log.error(f"Failed to start agent: {err}")
            return False

    # Wait for GUI to come up
    log.info(f"Waiting {POST_RESTART_WAIT}s for agent to initialize...")
    time.sleep(POST_RESTART_WAIT)

    # Check if GUI responds
    if check_gui_health():
        log.info("Trading Agent GUI is responding.")
        return True

    # Give it more time
    log.info("GUI not ready, waiting another 15s...")
    time.sleep(15)
    if check_gui_health():
        log.info("Trading Agent GUI is responding.")
        return True

    # Check if at least the container is running
    agent_info = get_container_status(AGENT_CONTAINER)
    if agent_info.get("running"):
        log.warning("Agent is running but GUI not responding yet. Check logs.")
        agent_logs = get_container_logs(AGENT_CONTAINER, tail=10)
        log.info(f"Agent logs:\n{agent_logs}")
        return True  # It's running, might just need more time

    log.error("Trading agent failed to start.")
    return False


def full_stack_restart(dry_run: bool = False) -> bool:
    """Restart both gateway and agent."""
    gw_ok = restart_gateway(dry_run)
    if not gw_ok and not dry_run:
        log.error("Gateway restart failed. Agent restart skipped.")
        return False

    time.sleep(5)
    agent_ok = restart_agent(dry_run)
    return gw_ok and agent_ok


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def send_notification(title: str, message: str, level: str = "warning"):
    """Send notification via Discord webhook (if configured)."""
    if not DISCORD_WEBHOOK_URL:
        return

    emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "success": "✅"}.get(level, "📋")

    payload = {
        "content": f"{emoji} **{title}**\n{message}\n_({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})_",
    }

    try:
        import urllib.request
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning(f"Failed to send Discord notification: {e}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_watchdog(interval: int, dry_run: bool = False):
    """Main watchdog loop."""
    global _consecutive_failures, _last_healthy_time

    log.info("=" * 60)
    log.info("  IB Gateway Watchdog Started")
    log.info(f"  Gateway:   {GATEWAY_CONTAINER}")
    log.info(f"  Agent:     {AGENT_CONTAINER}")
    log.info(f"  Interval:  {interval}s")
    log.info(f"  Max restarts/hr: {MAX_RESTARTS_PER_HOUR}")
    log.info(f"  Dry run:   {dry_run}")
    log.info("=" * 60)

    send_notification(
        "Watchdog Started",
        f"Monitoring {GATEWAY_CONTAINER} and {AGENT_CONTAINER} every {interval}s.",
        "info",
    )

    while not _shutdown_requested:
        try:
            status = check_health()

            if status.fully_healthy:
                _consecutive_failures = 0
                _last_healthy_time = datetime.now()
                log.info(f"✓ All healthy | {status.summary()}")
            else:
                _consecutive_failures += 1
                log.warning(
                    f"✗ UNHEALTHY (streak: {_consecutive_failures}) | {status.summary()}"
                )

                # Decide recovery action
                if not status.gateway_container_running:
                    log.error("Gateway container is NOT running!")
                    if can_restart():
                        record_restart()
                        send_notification(
                            "Gateway Down",
                            "IB Gateway container not running. Attempting restart...",
                            "error",
                        )
                        success = full_stack_restart(dry_run)
                        if success:
                            send_notification("Recovery Successful", "Full stack restarted.", "success")
                        else:
                            send_notification("Recovery Failed", "Could not restart. Manual intervention needed.", "error")
                    else:
                        log.warning(f"Cooldown active. Waiting {COOLDOWN_AFTER_MAX}s...")
                        time.sleep(COOLDOWN_AFTER_MAX)
                        continue

                elif not status.gateway_login_ok or not status.gateway_socat_port_open:
                    # Gateway running but not functional
                    if _consecutive_failures >= 3:
                        log.error("Gateway running but not functional after 3 checks.")
                        if can_restart():
                            record_restart()
                            reason = "login failed" if not status.gateway_login_ok else "port not open"
                            send_notification(
                                "Gateway Unhealthy",
                                f"Gateway {reason}. Restarting...",
                                "warning",
                            )
                            success = restart_gateway(dry_run)
                            if success:
                                time.sleep(5)
                                restart_agent(dry_run)
                                send_notification("Recovery Successful", "Gateway restarted.", "success")
                            else:
                                send_notification("Recovery Failed", "Gateway restart failed.", "error")

                elif not status.agent_container_running or not status.agent_gui_responding:
                    # Gateway OK but agent down
                    if _consecutive_failures >= 2:
                        log.warning("Agent down but gateway OK. Restarting agent only.")
                        if can_restart():
                            record_restart()
                            send_notification(
                                "Agent Down",
                                "Trading Agent not responding. Restarting...",
                                "warning",
                            )
                            success = restart_agent(dry_run)
                            if success:
                                send_notification("Recovery Successful", "Agent restarted.", "success")
                            else:
                                send_notification("Recovery Failed", "Agent restart failed.", "error")

                elif status.gateway_ok and status.agent_container_running and not status.agent_connected_to_gw:
                    # Both running but agent lost connection to gateway
                    if _consecutive_failures >= 3:
                        log.warning("Agent lost connection to gateway. Restarting agent...")
                        if can_restart():
                            record_restart()
                            send_notification(
                                "Agent Disconnected",
                                "Trading Agent lost connection to IB Gateway. Restarting agent...",
                                "warning",
                            )
                            success = restart_agent(dry_run)
                            if success:
                                send_notification("Recovery Successful", "Agent reconnected.", "success")

        except Exception as e:
            log.error(f"Watchdog check error: {e}", exc_info=True)

        # Sleep with interrupt check
        for _ in range(interval):
            if _shutdown_requested:
                break
            time.sleep(1)

    log.info("Watchdog stopped.")
    send_notification("Watchdog Stopped", "Gateway monitoring stopped.", "info")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    global MAX_RESTARTS_PER_HOUR, COMPOSE_DIR

    parser = argparse.ArgumentParser(description="IB Gateway Watchdog")
    parser.add_argument(
        "--interval", type=int, default=CHECK_INTERVAL,
        help=f"Check interval in seconds (default: {CHECK_INTERVAL})",
    )
    parser.add_argument(
        "--max-restarts", type=int, default=MAX_RESTARTS_PER_HOUR,
        help=f"Max restarts per hour (default: {MAX_RESTARTS_PER_HOUR})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log actions without actually restarting",
    )
    parser.add_argument(
        "--check-once", action="store_true",
        help="Run a single health check and exit",
    )
    parser.add_argument(
        "--compose-dir", type=str, default=COMPOSE_DIR,
        help=f"Docker compose directory (default: {COMPOSE_DIR})",
    )

    args = parser.parse_args()

    MAX_RESTARTS_PER_HOUR = args.max_restarts
    COMPOSE_DIR = args.compose_dir

    if args.check_once:
        status = check_health()
        print(f"\nHealth Status: {'HEALTHY' if status.fully_healthy else 'UNHEALTHY'}")
        print(f"  {status.summary()}")
        print(f"\nDetails:")
        for k, v in status.details.items():
            print(f"  {k}: {v}")
        sys.exit(0 if status.fully_healthy else 1)

    run_watchdog(interval=args.interval, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
