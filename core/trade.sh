#!/bin/bash
# IV Crush 2.0 - Fire-and-Forget Trading Script
# Strategy: Balanced (Sharpe 8.07, Win Rate 100% on 8 trades Q2-Q4 2024)
#
# Usage:
#   ./trade.sh TSLA 2025-11-25                    # Single ticker
#   ./trade.sh list TSLA,NVDA,META 2025-11-27       # Multiple tickers
#   ./trade.sh scan 2025-11-25                     # Scan all earnings for date
#   ./trade.sh whisper [YYYY-MM-DD]                # Most anticipated earnings (current or specific week)
#   ./trade.sh sync [--dry-run]                    # Sync earnings calendar (discover new dates)
#   ./trade.sh health                              # Health check

set -euo pipefail  # Exit on error, unset vars, pipeline failures

# Colors
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
BLUE=$'\033[0;34m'
YELLOW=$'\033[1;33m'
BOLD=$'\033[1m'
NC=$'\033[0m'

# Change to script directory
cd "$(dirname "$0")"

# Detect OS and set date command type (optimization - detect once)
if date --version &>/dev/null 2>&1; then
    DATE_CMD="gnu"
else
    DATE_CMD="bsd"
fi

# Activate venv
if [ ! -d "venv" ]; then
    echo -e "${RED}Error: venv not found${NC}"
    echo "Setup: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

source venv/bin/activate

# Utility functions
validate_ticker() {
    local ticker=$1
    if [[ ! "$ticker" =~ ^[A-Z]{1,5}$ ]]; then
        echo -e "${RED}Error: Invalid ticker format '$ticker'${NC}"
        echo "Ticker must be 1-5 uppercase letters (e.g., AAPL, NVDA)"
        return 1
    fi
    return 0
}

validate_date() {
    local date_str=$1
    if [[ ! "$date_str" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        echo -e "${RED}Error: Invalid date format '$date_str'${NC}"
        echo "Date must be YYYY-MM-DD (e.g., 2025-11-20)"
        return 1
    fi
    return 0
}

calculate_expiration() {
    local earnings_date=$1
    local expiration=""

    # Use detected date command type (optimization - no failed subprocess)
    if [ "$DATE_CMD" = "gnu" ]; then
        expiration=$(date -d "$earnings_date + 1 day" +%Y-%m-%d 2>/dev/null)
    else
        expiration=$(date -v+1d -j -f "%Y-%m-%d" "$earnings_date" +%Y-%m-%d 2>/dev/null)
    fi

    # Error handling if date calculation fails
    if [ -z "$expiration" ]; then
        echo -e "${RED}Error: Could not calculate expiration date${NC}"
        echo "Please provide expiration date manually:"
        echo "  $0 TICKER EARNINGS_DATE EXPIRATION_DATE"
        exit 1
    fi

    echo "$expiration"
}

# Functions
health_check() {
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}    IV Crush 2.0 - System Health${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    python scripts/health_check.py 2>&1 | grep -A 10 "System Health\|UP\|HEALTHY" || {
        echo -e "${RED}Health check failed${NC}"
        return 1
    }
    echo ""
}

backup_database() {
    # Auto-backup database to local folder (synced by Google Drive)
    # Only backup if last backup is >6 hours old to avoid spam
    #
    # Security improvements:
    # - Atomic file locking to prevent race conditions
    # - Absolute paths to prevent directory traversal
    # - UTC timestamps to avoid DST issues
    # - WAL checkpoint with retry logic
    # - Backup verification before committing
    # - Validated retention_days parameter

    # Get absolute script directory
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Use absolute paths
    local backup_dir="${script_dir}/backups"
    local db_file="${script_dir}/data/ivcrush.db"
    local retention_days=30
    local backup_interval_hours=6

    # Validate retention_days (prevent accidental deletion of all backups)
    if ! [[ "$retention_days" =~ ^[0-9]+$ ]] || [ "$retention_days" -lt 7 ]; then
        retention_days=30  # Failsafe default
    fi

    # Check if database exists
    if [ ! -f "$db_file" ]; then
        return 0  # Silently skip if no database yet
    fi

    # Ensure backup directory is not a symlink (security)
    if [ -L "$backup_dir" ]; then
        echo -e "${YELLOW}⚠️  Backup directory is a symlink (security risk)${NC}" >&2
        return 0
    fi

    # Create backup directory if it doesn't exist
    mkdir -p "$backup_dir"

    # File-based locking using flock to prevent race conditions
    local lock_file="${backup_dir}/.backup.lock"

    # Open lock file on file descriptor 9
    exec 9>"$lock_file"

    # Try to acquire exclusive lock (non-blocking)
    if ! flock -n 9; then
        # Another backup is in progress, skip silently
        exec 9>&-
        return 0
    fi

    # Ensure lock fd is closed on exit/error (releases flock automatically)
    trap 'exec 9>&-' RETURN

    # Check if we need to backup (skip if last backup <6 hours old)
    local backup_interval_days
    backup_interval_days=$(echo "scale=4; $backup_interval_hours / 24" | bc)

    local last_backup
    last_backup=$(find "$backup_dir" -maxdepth 1 -name "ivcrush_*.db" -type f -mtime "-${backup_interval_days}" -print -quit 2>/dev/null)

    if [ -n "$last_backup" ]; then
        exec 9>&-
        trap - RETURN
        return 0  # Recent backup exists, skip
    fi

    # Create UTC timestamp to avoid DST issues
    local timestamp
    timestamp=$(TZ=UTC date +%Y%m%d_%H%M%S_UTC)
    local backup_file="${backup_dir}/ivcrush_${timestamp}.db"
    local temp_backup="${backup_file}.tmp.$$"

    # Ensure temp file is cleaned up on error and lock fd is closed
    trap 'rm -f "$temp_backup" 2>/dev/null; exec 9>&-' RETURN

    # Perform WAL checkpoint with retry logic
    local checkpoint_success=false
    local retries=3
    local delay=1

    for ((i=1; i<=retries; i++)); do
        if sqlite3 "$db_file" "PRAGMA wal_checkpoint(FULL);" 2>/dev/null; then
            checkpoint_success=true
            break
        fi

        if [ $i -lt $retries ]; then
            sleep $delay
            delay=$((delay * 2))
        fi
    done

    # Warn if checkpoint failed in WAL mode
    if [ "$checkpoint_success" = false ]; then
        local journal_mode
        journal_mode=$(sqlite3 "$db_file" "PRAGMA journal_mode;" 2>/dev/null || echo "unknown")

        if [ "$journal_mode" = "wal" ]; then
            echo -e "${YELLOW}⚠️  WAL checkpoint failed - backup may be inconsistent${NC}" >&2
        fi
    fi

    # Copy to temporary file first (atomic operation)
    if ! cp "$db_file" "$temp_backup" 2>/dev/null; then
        echo -e "${YELLOW}⚠️  Backup copy failed${NC}" >&2
        rm -f "$temp_backup" 2>/dev/null
        exec 9>&-
        trap - RETURN
        return 1
    fi

    # Verify backup before committing
    # 1. Check file sizes match
    local orig_size temp_size
    if stat -f%z "$db_file" >/dev/null 2>&1; then
        # macOS
        orig_size=$(stat -f%z "$db_file")
        temp_size=$(stat -f%z "$temp_backup")
    else
        # Linux
        orig_size=$(stat -c%s "$db_file")
        temp_size=$(stat -c%s "$temp_backup")
    fi

    if [ "$orig_size" != "$temp_size" ]; then
        echo -e "${YELLOW}⚠️  Backup size mismatch (original: ${orig_size}, backup: ${temp_size})${NC}" >&2
        rm -f "$temp_backup"
        exec 9>&-
        trap - RETURN
        return 1
    fi

    # 2. Verify database can be opened
    if ! sqlite3 "$temp_backup" "SELECT 1;" >/dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  Backup verification failed (database corrupted)${NC}" >&2
        rm -f "$temp_backup"
        exec 9>&-
        trap - RETURN
        return 1
    fi

    # Atomic rename to commit backup
    if ! mv "$temp_backup" "$backup_file" 2>/dev/null; then
        echo -e "${YELLOW}⚠️  Backup rename failed${NC}" >&2
        rm -f "$temp_backup"
        exec 9>&-
        trap - RETURN
        return 1
    fi

    # Sync to Google Drive (async, non-blocking)
    # Set GDRIVE_BACKUP_PATH environment variable to your Google Drive backup directory
    local gdrive_backup_dir="${GDRIVE_BACKUP_PATH:-$HOME/Library/CloudStorage/GoogleDrive/My Drive/Backups/trading-desk}"
    if [ -d "$gdrive_backup_dir" ]; then
        # Copy with verification
        local gdrive_dest="$gdrive_backup_dir/$(basename "$backup_file")"
        (
            if cp "$backup_file" "$gdrive_backup_dir/" 2>/dev/null; then
                # Verify destination file exists and has non-zero size
                if [ ! -s "$gdrive_dest" ]; then
                    echo -e "${YELLOW}Warning: Google Drive backup file is empty or missing${NC}" >&2
                fi
            else
                echo -e "${YELLOW}Warning: Google Drive backup copy failed${NC}" >&2
            fi
        ) &
        GDRIVE_PID=$!
        wait $GDRIVE_PID || echo -e "${YELLOW}Warning: Google Drive backup may not have completed${NC}" >&2
    fi

    # Cleanup old backups (keep last N days)
    find "$backup_dir" -maxdepth 1 -name "ivcrush_*.db" -type f -mtime "+${retention_days}" -delete 2>/dev/null || true

    # Cleanup old Google Drive backups (keep last N days)
    if [ -d "$gdrive_backup_dir" ]; then
        find "$gdrive_backup_dir" -maxdepth 1 -name "ivcrush_*.db" -type f -mtime "+${retention_days}" -delete 2>/dev/null || true
    fi

    # Release lock and clear trap
    exec 9>&-
    trap - RETURN
}

sync_earnings_calendar() {
    # Sync earnings calendar with latest data from Alpha Vantage + Yahoo Finance
    #
    # This discovers new earnings announcements and validates dates using
    # cross-reference validation (Yahoo Finance priority).
    #
    # Args:
    #   $1: Optional flags (--dry-run, --check-staleness)

    local dry_run_flag=""
    local extra_args=""

    # Parse arguments
    for arg in "$@"; do
        case "$arg" in
            --dry-run|-n)
                dry_run_flag="--dry-run"
                ;;
            --check-staleness|-s)
                extra_args="--check-staleness"
                ;;
            --threshold*)
                extra_args="$extra_args $arg"
                ;;
        esac
    done

    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}  Earnings Calendar Sync${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"

    if [ -n "$dry_run_flag" ]; then
        echo -e "${YELLOW}Mode: DRY RUN (preview only)${NC}"
    fi

    python scripts/sync_earnings_calendar.py $dry_run_flag $extra_args
    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}✓ Earnings calendar synced${NC}\n"
    else
        echo -e "${RED}✗ Sync failed or stale data detected${NC}\n"
        if [ "$extra_args" != *"--check-staleness"* ]; then
            return $exit_code
        fi
    fi

    return $exit_code
}

show_usage() {
    cat << EOF
${BLUE}${BOLD}IV Crush 2.0 - Earnings IV Crush Trading${NC}

${BOLD}USAGE${NC}
    $0 TICKER YYYY-MM-DD             Single ticker analysis
    $0 list TICKERS YYYY-MM-DD       Multiple tickers (comma-separated)
    $0 scan YYYY-MM-DD               Scan all earnings for date
    $0 whisper [YYYY-MM-DD]          Most anticipated earnings
    $0 sync [--dry-run]              Sync earnings calendar
    $0 sync-cloud                    Sync DB with cloud + backup to GDrive
    $0 health                        System health check

${BOLD}EXAMPLES${NC}
    $0 TSLA 2025-11-25
    $0 list TSLA,NVDA,META 2025-11-27
    $0 scan 2025-11-25
    $0 whisper

${BOLD}REQUIREMENTS${NC}
    TRADIER_API_KEY, ALPHA_VANTAGE_KEY in .env
EOF
}

analyze_single() {
    local ticker=$1
    local earnings_date=$2
    local expiration=${3:-}

    # Validate inputs
    validate_ticker "$ticker" || return 1
    validate_date "$earnings_date" || return 1

    if [ -n "$expiration" ]; then
        validate_date "$expiration" || return 1
    fi

    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}    Analyzing $ticker for $earnings_date${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo ""

    # FIXED: Proper expiration calculation with error handling
    if [ -z "$expiration" ]; then
        expiration=$(calculate_expiration "$earnings_date")
    fi

    # Store bias prediction for later validation (silent, non-blocking)
    # This builds our accuracy tracking dataset over time
    python scripts/store_bias_prediction.py "$ticker" >/dev/null 2>&1 || true

    # Run analysis with strategy generation
    # Capture full output to check for missing data error
    local analysis_output
    analysis_output=$(python scripts/analyze.py "$ticker" \
        --earnings-date "$earnings_date" \
        --expiration "$expiration" \
        --strategies 2>&1) || true  # Don't exit on error due to set -e

    # Check if analysis failed due to missing historical data
    if echo "$analysis_output" | grep -q "No historical data for $ticker"; then
        echo -e "${YELLOW}⚠️  No historical data found for $ticker${NC}"
        echo -e "${BLUE}📊 Auto-backfilling historical earnings data...${NC}"
        echo ""

        # Automatically backfill data (last 3 years)
        # Optimized: Use detected date command type (no failed subprocess)
        local start_date end_date
        if [ "$DATE_CMD" = "gnu" ]; then
            start_date=$(date -d "3 years ago" +%Y-%m-%d 2>/dev/null)
            end_date=$(date -d "yesterday" +%Y-%m-%d 2>/dev/null)
        else
            start_date=$(date -v-3y +%Y-%m-%d 2>/dev/null)
            end_date=$(date -v-1d +%Y-%m-%d 2>/dev/null)
        fi

        if [ -z "$start_date" ] || [ -z "$end_date" ]; then
            echo -e "${RED}Error: Could not calculate dates for backfill${NC}"
            return 1
        fi

        python scripts/backfill_historical.py "$ticker" \
            --start-date "$start_date" \
            --end-date "$end_date" 2>&1 | \
            grep -E "Processing|Backfilled|saved|✓|Total moves" || {
            echo -e "${RED}Backfill failed${NC}"
            return 1
        }

        echo ""
        echo -e "${GREEN}✓ Backfill complete. Retrying analysis...${NC}"
        echo ""

        # Retry analysis after backfill
        analysis_output=$(python scripts/analyze.py "$ticker" \
            --earnings-date "$earnings_date" \
            --expiration "$expiration" \
            --strategies 2>&1) || true  # Don't exit on error
    fi

    # Display analysis results (filter timestamps)
    # Check if analysis succeeded by looking for "ANALYSIS RESULTS" in output
    if echo "$analysis_output" | grep -q "ANALYSIS RESULTS"; then
        # Show from ANALYSIS RESULTS onward (strategies are shown BEFORE this section)
        # Optimized: Single sed with multiple expressions (3-4x faster than chained sed)
        echo "$analysis_output" | \
            sed -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - INFO - //' \
                -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - ERROR - /⚠️  /' \
                -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - WARNING - /⚠️  /' | \
            grep -v "^$"
    else
        # Analysis failed - show error messages
        echo -e "${RED}Analysis failed${NC}"
        echo ""
        echo "$analysis_output" | grep -E "ERROR|Analysis failed|No options|No historical" | \
            sed 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - ERROR - //' | \
            while IFS= read -r line; do
                echo -e "${RED}⚠️  ${line}${NC}"
            done
        return 1
    fi
    echo ""
}

analyze_list() {
    local tickers=$1
    local earnings_date=$2
    local offset_days=${3:-1}

    # Validate date
    validate_date "$earnings_date" || return 1

    # Validate each ticker in comma-separated list
    IFS=',' read -ra TICKER_ARRAY <<< "$tickers"
    for ticker in "${TICKER_ARRAY[@]}"; do
        validate_ticker "$ticker" || return 1
    done

    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}    Analyzing Multiple Tickers${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${YELLOW}Tickers:${NC} $tickers"
    echo -e "${YELLOW}Earnings Date:${NC} $earnings_date"
    echo -e "${YELLOW}Expiration Offset:${NC} +${offset_days} days"
    echo ""

    # Run list analysis with real-time output (unbuffered Python + direct piping)
    # Optimized: Single sed with multiple expressions (3-4x faster than chained sed)
    if ! python -u scripts/scan.py --tickers "$tickers" --expiration-offset "$offset_days" 2>&1 | \
        sed -u -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - INFO - //' \
                -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - ERROR - /⚠️  /' \
                -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - WARNING - /⚠️  /' | \
        grep -v "^$"; then
        echo -e "${RED}Analysis failed${NC}"
        echo -e "${YELLOW}Note: Auto-fetch earnings may not work. Try single ticker mode with explicit dates.${NC}"
        return 1
    fi

    echo ""
}

scan_earnings() {
    local scan_date=$1

    # Validate date
    validate_date "$scan_date" || return 1

    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}    Scanning Earnings for $scan_date${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo ""

    # Run scan mode with real-time output (unbuffered Python + direct piping)
    # Optimized: Single sed with multiple expressions (3-4x faster than chained sed)
    if ! python -u scripts/scan.py --scan-date "$scan_date" 2>&1 | \
        sed -u -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - INFO - //' \
                -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - ERROR - /⚠️  /' \
                -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - WARNING - /⚠️  /' | \
        grep -v "^$"; then
        echo -e "${RED}Scan failed${NC}"
        echo -e "${YELLOW}No earnings found for this date${NC}"
        return 1
    fi

    echo ""
}

# Earnings date validation moved to scan.py (validates only tradeable tickers after filtering)
# This optimizes performance by skipping tickers that won't be displayed

whisper_mode() {
    local week_monday=${1:-}

    # Validate date if provided
    if [ -n "$week_monday" ]; then
        validate_date "$week_monday" || return 1
    fi

    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}    Most Anticipated Earnings${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo ""

    # Run whisper mode with real-time output (unbuffered Python + direct piping)
    # Optimized: Single sed with multiple expressions (3-4x faster than chained sed)
    # Build command with unbuffered flag
    local base_cmd="python -u scripts/scan.py --whisper-week"
    if [ -n "$week_monday" ]; then
        base_cmd="$base_cmd $week_monday"
    fi

    if ! eval "$base_cmd" 2>&1 | \
        sed -u -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - INFO - //' \
                -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - ERROR - /⚠️  /' \
                -e 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.*\] - [^ ]* - WARNING - /⚠️  /' | \
        grep -v "^$"; then
        echo -e "${RED}Whisper mode failed${NC}"
        echo -e "${YELLOW}Could not fetch most anticipated earnings${NC}"
        echo -e "${YELLOW}Tip: Check Reddit API access or use a different week${NC}"
        return 1
    fi

    echo ""
}

show_summary() {
    # Summary is now included in the output from the Python scripts
    # No additional summary needed
    :
}

# Validate past bias predictions (silent, non-blocking)
# Run once per session to check if any predictions need validation
if [[ "${1:-}" != "help" && "${1:-}" != "--help" && "${1:-}" != "-h" && "${1:-}" != "health" ]]; then
    python scripts/validate_bias_predictions.py >/dev/null 2>&1 || true
fi

# Main logic
case "${1:-}" in
    help|--help|-h)
        show_usage
        exit 0
        ;;

    health)
        health_check
        ;;

    sync)
        # Parse sync arguments
        shift  # Remove 'sync' from args
        sync_earnings_calendar "$@"
        ;;

    sync-cloud)
        # Sync local DB with 5.0 cloud (GCS) and backup to Google Drive
        echo -e "${BLUE}${BOLD}Syncing with cloud database...${NC}"
        python3 "../scripts/sync_databases.py"
        ;;

    scan)
        if [ -z "${2:-}" ]; then
            echo -e "${RED}Error: Scan date required${NC}"
            echo "Usage: $0 scan YYYY-MM-DD"
            exit 1
        fi
        health_check
        scan_earnings "$2"
        show_summary
        ;;

    whisper)
        health_check

        # Extract week argument if provided
        WEEK_ARG=""
        for arg in "$@"; do
            if [[ "$arg" != "whisper" ]]; then
                WEEK_ARG="$arg"
            fi
        done

        # Note: Earnings date validation now happens automatically inside scan.py
        # for tradeable tickers only (after filtering), optimizing performance
        whisper_mode "$WEEK_ARG"
        show_summary
        ;;

    list)
        if [ -z "${2:-}" ] || [ -z "${3:-}" ]; then
            echo -e "${RED}Error: Tickers and date required${NC}"
            echo "Usage: $0 list TICKER1,TICKER2,... YYYY-MM-DD [offset_days]"
            exit 1
        fi
        health_check
        analyze_list "$2" "$3" "${4:-1}"
        show_summary
        ;;

    "")
        show_usage
        exit 0
        ;;

    *)
        # Single ticker mode
        if [ -z "${2:-}" ]; then
            echo -e "${RED}Error: Earnings date required${NC}"
            echo "Usage: $0 $1 YYYY-MM-DD [YYYY-MM-DD]"
            exit 1
        fi
        health_check
        analyze_single "$1" "$2" "${3:-}"
        show_summary
        ;;
esac

echo -e "${GREEN}✓ Complete${NC}"
