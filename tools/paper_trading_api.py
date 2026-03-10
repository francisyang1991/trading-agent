#!/usr/bin/env python3
"""
Paper Trading API Server — lightweight Flask backend for saiyan-dashboard.

Exposes REST endpoints for the T0 engine bridge:
  POST /api/paper/start    — start paper trading engine
  POST /api/paper/stop     — stop engine (flatten + disconnect)
  GET  /api/paper/status   — full engine state snapshot
  POST /api/paper/flatten  — emergency flatten all positions
  GET  /api/paper/health   — basic health check

Runs on port 8081 (separate from GCP gateway on 8080).
The saiyan-dashboard (Next.js) proxies to this server.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
from loguru import logger

try:
    from flask_cors import CORS
except ImportError:
    CORS = None  # type: ignore
    logger.warning("flask-cors not installed; CORS headers disabled")

# Ensure project root is on sys.path for src.* imports
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.trading.engine_bridge import engine_bridge

app = Flask(__name__)
if CORS is not None:
    CORS(app)  # Allow cross-origin from saiyan-dashboard (localhost:3000)


# ── Health ──────────────────────────────────────────────────────────────────

@app.route("/api/paper/health")
def api_health():
    """Basic health check."""
    return jsonify({
        "ok": True,
        "service": "paper-trading-api",
        "timestamp": datetime.now().isoformat(),
        "engine_running": engine_bridge.is_running(),
    })


# ── Engine lifecycle ────────────────────────────────────────────────────────

@app.route("/api/paper/start", methods=["POST"])
def api_start():
    """Start the T0 paper trading engine."""
    config_path = "config/intraday_t0_paper.yaml"
    try:
        body = request.get_json(silent=True) or {}
        config_path = body.get("config_path", config_path)
    except Exception:
        pass

    if engine_bridge.is_running():
        return jsonify({"success": False, "error": "Engine is already running"}), 409

    try:
        ok = engine_bridge.start(config_path=config_path)
    except Exception as exc:
        logger.exception(f"Engine start failed: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500

    if ok:
        logger.info(f"Paper trading engine started with config: {config_path}")
        return jsonify({"success": True, "message": "Engine starting"})
    else:
        return jsonify({"success": False, "error": "Failed to start engine"}), 500


@app.route("/api/paper/stop", methods=["POST"])
def api_stop():
    """Stop the engine (flatten all → disconnect)."""
    if not engine_bridge.is_running():
        return jsonify({"success": False, "error": "Engine is not running"}), 409

    ok = engine_bridge.stop()
    return jsonify({"success": ok, "message": "Stop signal sent" if ok else "Failed to stop"})


@app.route("/api/paper/flatten", methods=["POST"])
def api_flatten():
    """Emergency flatten all satellite positions."""
    if not engine_bridge.is_running():
        return jsonify({"success": False, "error": "Engine is not running"}), 409

    ok = engine_bridge.force_flatten()
    return jsonify({"success": ok, "message": "Flatten command sent" if ok else "Failed to flatten"})


# ── Engine state (single endpoint, polled by dashboard) ────────────────────

@app.route("/api/paper/status")
def api_status():
    """Full engine state snapshot — positions, signals, trades, metrics, account."""
    state = engine_bridge.get_state()
    return jsonify(state)


# ── Run server ──────────────────────────────────────────────────────────────

def main():
    port = int(os.getenv("PAPER_API_PORT", "8081"))
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           Paper Trading API Server v1.0                      ║
╠══════════════════════════════════════════════════════════════╣
║  API: http://localhost:{port}/api/paper/                       ║
║                                                              ║
║  Endpoints:                                                  ║
║    POST /api/paper/start    — Start T0 engine                ║
║    POST /api/paper/stop     — Stop + flatten                 ║
║    GET  /api/paper/status   — Full state snapshot            ║
║    POST /api/paper/flatten  — Emergency flatten              ║
║    GET  /api/paper/health   — Health check                   ║
║                                                              ║
║  Press Ctrl+C to stop                                        ║
╚══════════════════════════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
