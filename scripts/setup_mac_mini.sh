#!/bin/bash
# =============================================================================
# Mac Mini Setup — Migrate Discord Bots from AWS
# =============================================================================
# Run this ON THE MAC MINI after AirDropping the trading_agent folder.
#
# What it does:
#   1. Detects repo location
#   2. Creates Python venv and installs dependencies
#   3. Validates .env file has required tokens
#   4. Patches launchd plists with correct paths
#   5. Installs and starts bot services via launchd
#   6. Shows status
#
# Usage:
#   cd /path/to/trading_agent
#   bash scripts/setup_mac_mini.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_DIR="$REPO_DIR/scripts/services"
ENV_FILE="$REPO_DIR/workspace/.env"
VENV_DIR="$REPO_DIR/venv"
LOG_DIR="$REPO_DIR/logs"

echo "=========================================="
echo "  Mac Mini Discord Bot Setup"
echo "=========================================="
echo "  Repo: $REPO_DIR"
echo ""

# --- Step 1: Check prerequisites ---
echo -e "${YELLOW}[1/6] Checking prerequisites...${NC}"

if ! command -v python3 &>/dev/null; then
    echo -e "${RED}ERROR: python3 not found. Install via: brew install python3${NC}"
    exit 1
fi
PYTHON_VERSION=$(python3 --version 2>&1)
echo "  Python: $PYTHON_VERSION"

if ! command -v pip3 &>/dev/null; then
    echo -e "${RED}ERROR: pip3 not found. Install via: brew install python3${NC}"
    exit 1
fi

if [ ! -f "$REPO_DIR/requirements.txt" ]; then
    echo -e "${RED}ERROR: requirements.txt not found in $REPO_DIR${NC}"
    exit 1
fi
echo -e "  ${GREEN}Prerequisites OK${NC}"

# --- Step 2: Create venv and install deps ---
echo ""
echo -e "${YELLOW}[2/6] Setting up Python virtual environment...${NC}"

if [ -d "$VENV_DIR" ]; then
    echo "  Existing venv found — reinstalling dependencies"
else
    echo "  Creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

echo "  Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt" -q
echo -e "  ${GREEN}Dependencies installed${NC}"

# Verify key imports
echo "  Verifying imports..."
"$VENV_DIR/bin/python" -c "
import discord, yaml, pandas
print('  discord.py:', discord.__version__)
print('  All core imports OK')
" || {
    echo -e "${RED}Import verification failed. Check requirements.txt${NC}"
    exit 1
}

# --- Step 3: Validate .env ---
echo ""
echo -e "${YELLOW}[3/6] Checking environment configuration...${NC}"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}ERROR: $ENV_FILE not found!${NC}"
    echo ""
    echo "Create it from the example:"
    echo "  cp $REPO_DIR/workspace/.env.example $ENV_FILE"
    echo ""
    echo "Required variables:"
    echo "  DISCORD_BOT_TOKEN=...     (from Discord Developer Portal)"
    echo "  DISCORD_USER_TOKEN=...    (your personal Discord token)"
    echo "  MINIMAX_API_KEY=...       (MiniMax LLM API key)"
    echo ""
    echo "Optional (enable features):"
    echo "  ANTHROPIC_API_KEY=...     (Claude API for analysis)"
    echo "  TRADE_API_URL=...         (GCloud trading API)"
    echo "  TRADE_API_KEY=...         (GCloud trading API key)"
    echo "  CITRINI_EMAIL_ENABLED=... (Citrini email pipeline)"
    echo "  WATCHDOG_DISCORD_WEBHOOK=... (health alert webhook)"
    exit 1
fi

MISSING_VARS=""
for var in DISCORD_BOT_TOKEN MINIMAX_API_KEY; do
    val=$(grep "^${var}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
    if [ -z "$val" ] || [ "$val" = "your_discord_bot_token_here" ] || [ "$val" = "your_minimax_api_key_here" ]; then
        MISSING_VARS="$MISSING_VARS $var"
    fi
done

if [ -n "$MISSING_VARS" ]; then
    echo -e "${RED}WARNING: These required env vars are missing/placeholder:${MISSING_VARS}${NC}"
    echo "  Edit $ENV_FILE before starting bots"
else
    echo -e "  ${GREEN}.env OK — required tokens present${NC}"
fi

# --- Step 4: Create log directory ---
echo ""
echo -e "${YELLOW}[4/6] Setting up directories...${NC}"
mkdir -p "$LOG_DIR"
mkdir -p "$LAUNCH_AGENTS"
echo "  Logs: $LOG_DIR"
echo "  LaunchAgents: $LAUNCH_AGENTS"

# --- Step 5: Patch and install launchd plists ---
echo ""
echo -e "${YELLOW}[5/6] Installing launchd services...${NC}"

# Source .env into the plists by injecting env vars
ENV_VARS=""
while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    value="${value%\"}"
    value="${value#\"}"
    ENV_VARS="$ENV_VARS        <key>$key</key>\n        <string>$value</string>\n"
done < "$ENV_FILE"

for plist_template in "$PLIST_DIR"/com.saiyan.*.plist; do
    plist_name=$(basename "$plist_template")
    target="$LAUNCH_AGENTS/$plist_name"

    # Replace __REPO_DIR__ placeholder with actual path
    sed "s|__REPO_DIR__|$REPO_DIR|g" "$plist_template" > "$target"

    # Inject .env variables into the EnvironmentVariables dict
    # Insert after the PYTHONUNBUFFERED line
    if [ -n "$ENV_VARS" ]; then
        # Use perl for multi-line insertion (more reliable than sed on macOS)
        perl -i -pe "
            if (/<string>1<\/string>/ && \$found_unbuffered == 0) {
                \$found_unbuffered = 1;
                \$_ .= \"$ENV_VARS\";
            }
        " "$target"
    fi

    echo "  Installed: $target"

    # Unload if already loaded, then load
    launchctl bootout "gui/$(id -u)/$plist_name" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$target"
    echo "    Loaded into launchd"
done

# --- Step 6: Verify ---
echo ""
echo -e "${YELLOW}[6/6] Verifying bot status...${NC}"
sleep 3

for plist_template in "$PLIST_DIR"/com.saiyan.*.plist; do
    label=$(basename "$plist_template" .plist)
    pid=$(launchctl print "gui/$(id -u)/$label" 2>/dev/null | grep "pid =" | awk '{print $3}' || echo "")
    state=$(launchctl print "gui/$(id -u)/$label" 2>/dev/null | grep "state =" | awk '{print $3}' || echo "unknown")

    if [ -n "$pid" ] && [ "$pid" != "0" ]; then
        echo -e "  ${GREEN}✓ $label${NC}: running (pid=$pid)"
    else
        echo -e "  ${RED}✗ $label${NC}: state=$state"
        echo "    Check logs: tail -50 $LOG_DIR/${label#com.saiyan.}-stderr.log"
    fi
done

echo ""
echo "=========================================="
echo "  Setup Complete"
echo "=========================================="
echo ""
echo "Useful commands:"
echo "  Status:    bash scripts/bot_health_mac.sh"
echo "  Logs:      tail -f $LOG_DIR/interactive-bot-stderr.log"
echo "             tail -f $LOG_DIR/claude-agent-stderr.log"
echo "  Stop bot:  launchctl bootout gui/\$(id -u)/com.saiyan.interactive-bot"
echo "  Start bot: launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.saiyan.interactive-bot.plist"
echo "  Stop all:  bash scripts/bot_health_mac.sh --stop"
echo "  Restart:   bash scripts/bot_health_mac.sh --restart"
