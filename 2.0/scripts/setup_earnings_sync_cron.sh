#!/bin/bash
#
# Setup automated earnings calendar synchronization via cron
#
# This script helps configure a daily cron job to automatically sync earnings calendar
#
# Usage:
#   ./scripts/setup_earnings_sync_cron.sh
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SYNC_SCRIPT="$SCRIPT_DIR/sync_earnings_calendar.py"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python3"
LOG_DIR="$PROJECT_DIR/logs"

# Check if sync script exists
if [ ! -f "$SYNC_SCRIPT" ]; then
    echo "❌ Error: Sync script not found at $SYNC_SCRIPT"
    exit 1
fi

# Check if venv exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "❌ Error: Virtual environment not found at $VENV_PYTHON"
    exit 1
fi

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

echo "════════════════════════════════════════════════════════════════"
echo "  Earnings Calendar Sync - Cron Setup"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "This will create a daily cron job to automatically sync earnings calendar."
echo ""
echo "Configuration:"
echo "  Script:   $SYNC_SCRIPT"
echo "  Python:   $VENV_PYTHON"
echo "  Log dir:  $LOG_DIR"
echo ""
echo "Recommended schedule:"
echo "  • 8:00 PM ET (20:00) - After market close"
echo "  • Daily (every day)"
echo ""

# Prompt for schedule
read -p "Enter cron time (hour, 0-23) [default: 20]: " CRON_HOUR
CRON_HOUR=${CRON_HOUR:-20}

read -p "Enter cron minute (0-59) [default: 0]: " CRON_MINUTE
CRON_MINUTE=${CRON_MINUTE:-0}

# Validate hour and minute
if ! [[ "$CRON_HOUR" =~ ^[0-9]+$ ]] || [ "$CRON_HOUR" -lt 0 ] || [ "$CRON_HOUR" -gt 23 ]; then
    echo "❌ Error: Hour must be between 0 and 23"
    exit 1
fi

if ! [[ "$CRON_MINUTE" =~ ^[0-9]+$ ]] || [ "$CRON_MINUTE" -lt 0 ] || [ "$CRON_MINUTE" -gt 59 ]; then
    echo "❌ Error: Minute must be between 0 and 59"
    exit 1
fi

# Load environment variables
ENV_VARS=""
if [ -f "$PROJECT_DIR/.env" ]; then
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ "$key" =~ ^#.*$ ]] && continue
        [[ -z "$key" ]] && continue

        # Remove quotes from value
        value="${value%\"}"
        value="${value#\"}"

        ENV_VARS="$ENV_VARS $key=\"$value\""
    done < "$PROJECT_DIR/.env"
fi

# Build cron command
LOG_FILE="$LOG_DIR/earnings_sync_\$(date +\%Y\%m\%d).log"
CRON_CMD="cd \"$PROJECT_DIR\" && $ENV_VARS \"$VENV_PYTHON\" \"$SYNC_SCRIPT\" >> \"$LOG_FILE\" 2>&1"

# Build cron entry
CRON_ENTRY="$CRON_MINUTE $CRON_HOUR * * * $CRON_CMD"

echo ""
echo "────────────────────────────────────────────────────────────────"
echo "Cron Entry:"
echo "────────────────────────────────────────────────────────────────"
echo "$CRON_ENTRY"
echo "────────────────────────────────────────────────────────────────"
echo ""
echo "This will run daily at $CRON_HOUR:$(printf "%02d" $CRON_MINUTE)"
echo ""

read -p "Add this cron job? (y/N): " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "❌ Cancelled"
    exit 0
fi

# Check if cron entry already exists
if crontab -l 2>/dev/null | grep -q "sync_earnings_calendar.py"; then
    echo ""
    echo "⚠️  WARNING: Existing earnings sync cron job found!"
    echo ""
    crontab -l 2>/dev/null | grep "sync_earnings_calendar.py"
    echo ""
    read -p "Replace existing job? (y/N): " REPLACE
    if [[ ! "$REPLACE" =~ ^[Yy]$ ]]; then
        echo "❌ Cancelled"
        exit 0
    fi

    # Remove existing job
    crontab -l 2>/dev/null | grep -v "sync_earnings_calendar.py" | crontab -
    echo "✓ Removed existing job"
fi

# Add new cron job
(crontab -l 2>/dev/null; echo "$CRON_ENTRY") | crontab -

echo ""
echo "✓ Cron job added successfully!"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Setup Complete"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "1. Test the sync script manually:"
echo "   cd \"$PROJECT_DIR\""
echo "   ./venv/bin/python3 scripts/sync_earnings_calendar.py --dry-run"
echo ""
echo "2. View cron jobs:"
echo "   crontab -l"
echo ""
echo "3. Monitor logs:"
echo "   tail -f \"$LOG_DIR/earnings_sync_*.log\""
echo ""
echo "4. Check for stale data anytime:"
echo "   ./venv/bin/python3 scripts/sync_earnings_calendar.py --check-staleness"
echo ""
echo "5. Remove cron job (if needed):"
echo "   crontab -e"
echo "   # Delete the line containing 'sync_earnings_calendar.py'"
echo ""
