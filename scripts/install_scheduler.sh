#!/bin/bash
# =============================================================================
# Install/Uninstall Daily Scanner Scheduler (macOS launchd)
# =============================================================================
# Usage:
#   ./scripts/install_scheduler.sh install    # Install and start the scheduler
#   ./scripts/install_scheduler.sh uninstall  # Stop and remove the scheduler
#   ./scripts/install_scheduler.sh status     # Check if scheduler is running
#   ./scripts/install_scheduler.sh run        # Run scan manually (test)
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.saiyan.dailyscanner"
PLIST_SOURCE="${SCRIPT_DIR}/${PLIST_NAME}.plist"
PLIST_DEST="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

install_scheduler() {
    echo "========================================="
    echo "Installing SAIYAN Daily Scanner"
    echo "========================================="
    echo ""
    
    # Create necessary directories
    mkdir -p "$PROJECT_DIR/logs"
    mkdir -p "$PROJECT_DIR/results"
    mkdir -p "${HOME}/Library/LaunchAgents"
    print_status "Created logs and results directories"
    
    # Make scripts executable
    chmod +x "${SCRIPT_DIR}/daily_scan.sh"
    print_status "Made daily_scan.sh executable"
    
    # Check if already installed
    if [ -f "$PLIST_DEST" ]; then
        print_warning "Scheduler already installed. Unloading first..."
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
    fi
    
    # Copy plist to LaunchAgents
    cp "$PLIST_SOURCE" "$PLIST_DEST"
    print_status "Copied plist to ~/Library/LaunchAgents/"
    
    # Load the scheduler
    launchctl load "$PLIST_DEST"
    print_status "Loaded scheduler into launchd"
    
    echo ""
    echo "========================================="
    echo -e "${GREEN}Installation complete!${NC}"
    echo "========================================="
    echo ""
    echo "Schedule: Daily at 12:30 PM (12:30)"
    echo "Reports:  ${PROJECT_DIR}/results/"
    echo "Logs:     ${PROJECT_DIR}/logs/"
    echo ""
    echo "Commands:"
    echo "  Check status:  $0 status"
    echo "  Run manually:  $0 run"
    echo "  Uninstall:     $0 uninstall"
    echo ""
}

uninstall_scheduler() {
    echo "========================================="
    echo "Uninstalling SAIYAN Daily Scanner"
    echo "========================================="
    echo ""
    
    if [ -f "$PLIST_DEST" ]; then
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
        print_status "Unloaded scheduler from launchd"
        
        rm "$PLIST_DEST"
        print_status "Removed plist from ~/Library/LaunchAgents/"
    else
        print_warning "Scheduler not installed"
    fi
    
    echo ""
    echo -e "${GREEN}Uninstallation complete!${NC}"
    echo ""
}

check_status() {
    echo "========================================="
    echo "SAIYAN Daily Scanner Status"
    echo "========================================="
    echo ""
    
    if [ -f "$PLIST_DEST" ]; then
        print_status "Plist installed: $PLIST_DEST"
        
        # Check if loaded
        if launchctl list | grep -q "$PLIST_NAME"; then
            print_status "Scheduler is ACTIVE"
            echo ""
            echo "Scheduled: Daily at 12:30 PM (12:30)"
            
            # Show last run info from logs
            LATEST_LOG=$(ls -t "$PROJECT_DIR/logs"/scan_*.log 2>/dev/null | head -1)
            if [ -n "$LATEST_LOG" ]; then
                echo ""
                echo "Last log: $LATEST_LOG"
                echo "Last run: $(stat -f '%Sm' "$LATEST_LOG" 2>/dev/null || stat -c '%y' "$LATEST_LOG" 2>/dev/null || echo 'Unknown')"
            fi
            
            # Show latest result
            LATEST_RESULT=$(ls -t "$PROJECT_DIR/results"/scan_*.txt 2>/dev/null | head -1)
            if [ -n "$LATEST_RESULT" ]; then
                echo "Last report: $LATEST_RESULT"
            fi
        else
            print_warning "Scheduler is installed but NOT loaded"
            echo ""
            echo "To load: launchctl load $PLIST_DEST"
        fi
    else
        print_error "Scheduler is NOT installed"
        echo ""
        echo "To install: $0 install"
    fi
    echo ""
}

run_manual() {
    echo "========================================="
    echo "Running Scanner Manually"
    echo "========================================="
    echo ""
    
    "${SCRIPT_DIR}/daily_scan.sh"
}

# Main
case "${1:-}" in
    install)
        install_scheduler
        ;;
    uninstall)
        uninstall_scheduler
        ;;
    status)
        check_status
        ;;
    run)
        run_manual
        ;;
    *)
        echo "SAIYAN Daily Scanner Scheduler"
        echo ""
        echo "Usage: $0 {install|uninstall|status|run}"
        echo ""
        echo "Commands:"
        echo "  install    - Install and start the daily scanner (12:30 PM)"
        echo "  uninstall  - Stop and remove the scheduler"
        echo "  status     - Check scheduler status"
        echo "  run        - Run scanner manually (for testing)"
        echo ""
        exit 1
        ;;
esac
