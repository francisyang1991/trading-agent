#!/bin/bash
# =============================================================================
# Setup Earnings Refresh Cron Job
# =============================================================================
# Adds a weekday cron entry to run earnings refresh at 6 AM local time.
# Run this script once to install the cron job.
#
# Usage:
#   ./scripts/setup_earnings_refresh_cron.sh
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CRON_SCRIPT="${PROJECT_DIR}/scripts/earnings_refresh_cron.sh"
CRON_LINE="0 6 * * 1-5 ${CRON_SCRIPT}"

echo "=== Setting up Earnings Refresh Cron ==="
echo "Project: $PROJECT_DIR"
echo "Script:  $CRON_SCRIPT"
echo "Schedule: 6:00 AM, Monday–Friday"
echo ""

# Ensure cron script is executable
chmod +x "$CRON_SCRIPT"

# Remove any existing earnings_refresh_cron entry, then add new one
(crontab -l 2>/dev/null | grep -v "earnings_refresh_cron"; echo "$CRON_LINE") | crontab -

echo "Cron job added:"
crontab -l | grep earnings_refresh

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To test manually:"
echo "  $CRON_SCRIPT"
echo ""
echo "Logs: $PROJECT_DIR/logs/earnings_refresh_*.log"
echo ""
