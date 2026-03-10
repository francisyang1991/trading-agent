#!/bin/bash
# =============================================================================
# Setup Data Health Check-in Cron Job
# =============================================================================
# Adds a weekly cron entry to run data health audit at 2 AM Sunday.
# Run this script once to install the cron job.
#
# Usage:
#   ./scripts/setup_data_health_cron.sh
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CRON_SCRIPT="${PROJECT_DIR}/scripts/data_health_checkin.sh"
CRON_LINE="0 2 * * 0 ${CRON_SCRIPT}"

echo "=== Setting up Data Health Check-in Cron ==="
echo "Project: $PROJECT_DIR"
echo "Script:  $CRON_SCRIPT"
echo "Schedule: 2:00 AM, Sunday (weekly)"
echo ""

# Ensure cron script is executable
chmod +x "$CRON_SCRIPT"

# Remove any existing data_health_checkin entry, then add new one
(crontab -l 2>/dev/null | grep -v "data_health_checkin"; echo "$CRON_LINE") | crontab -

echo "Cron job added:"
crontab -l | grep data_health

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To test manually:"
echo "  $CRON_SCRIPT"
echo ""
echo "Report: $PROJECT_DIR/results/picker/data_health_report.md"
echo "Logs: $PROJECT_DIR/logs/data_health_*.log"
echo ""
