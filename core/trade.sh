#!/bin/bash
# IV Crush 2.0 - Fire-and-Forget Trading Script
# Strategy: Balanced (Sharpe 8.07, Win Rate 100% on 8 trades Q2-Q4 2024)
#
# Usage:
#   ./trade.sh TSLA 2025-11-25                    # Single ticker
#   ./trade.sh list TSLA,NVDA,META 2025-11-27       # Multiple tickers
#   ./trade.sh scan 2025-11-25                     # Scan all earnings for date
#   ./trade.sh whisper [YYYY-MM-DD]                # Most anticipated earnings (current or specific week)
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

    # Atomic file locking to prevent race conditions
    local backup_lock="${backup_dir}/.backup.lock"

    # Try to acquire lock (fail if another backup is in progress)
    if ! mkdir "$backup_lock" 2>/dev/null; then
        # Another backup is in progress, skip silently
        return 0
    fi

    # Ensure lock is removed on exit/error
    trap 'rmdir "$backup_lock" 2>/dev/null || true' RETURN

    # Check if we need to backup (skip if last backup <6 hours old)
    local backup_interval_days
    backup_interval_days=$(echo "scale=4; $backup_interval_hours / 24" | bc)

    local last_backup
    last_backup=$(find "$backup_dir" -maxdepth 1 -name "ivcrush_*.db" -type f -mtime "-${backup_interval_days}" -print -quit 2>/dev/null)

    if [ -n "$last_backup" ]; then
        rmdir "$backup_lock" 2>/dev/null
        trap - RETURN
        return 0  # Recent backup exists, skip
    fi

    # Create UTC timestamp to avoid DST issues
    local timestamp
    timestamp=$(TZ=UTC date +%Y%m%d_%H%M%S_UTC)
    local backup_file="${backup_dir}/ivcrush_${timestamp}.db"
    local temp_backup="${backup_file}.tmp.$$"

    # Ensure temp file is cleaned up on error
    trap 'rm -f "$temp_backup" 2>/dev/null; rmdir "$backup_lock" 2>/dev/null || true' RETURN

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
        echo -e "${YELLOW}⚠️  Backup failed (continuing anyway)${NC}" >&2
        rm -f "$temp_backup" 2>/dev/null
        rmdir "$backup_lock" 2>/dev/null
        trap - RETURN
        return 0
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
        echo -e "${YELLOW}⚠️  Backup size mismatch (continuing anyway)${NC}" >&2
        rm -f "$temp_backup"
        rmdir "$backup_lock" 2>/dev/null
        trap - RETURN
        return 0
    fi

    # 2. Verify database can be opened
    if ! sqlite3 "$temp_backup" "SELECT 1;" >/dev/null 2>&1; then
        echo -e "${YELLOW}⚠️  Backup verification failed (continuing anyway)${NC}" >&2
        rm -f "$temp_backup"
        rmdir "$backup_lock" 2>/dev/null
        trap - RETURN
        return 0
    fi

    # Atomic rename to commit backup
    if ! mv "$temp_backup" "$backup_file" 2>/dev/null; then
        echo -e "${YELLOW}⚠️  Backup failed (continuing anyway)${NC}" >&2
        rm -f "$temp_backup"
        rmdir "$backup_lock" 2>/dev/null
        trap - RETURN
        return 0
    fi

    # Sync to Google Drive (async, non-blocking)
    local gdrive_backup_dir="$HOME/Library/CloudStorage/GoogleDrive-pmakwana99@gmail.com/My Drive/Trading/Database Backups"
    if [ -d "$gdrive_backup_dir" ]; then
        # Copy in background to avoid blocking
        (cp "$backup_file" "$gdrive_backup_dir/" 2>/dev/null || true) &
    fi

    # Cleanup old backups (keep last N days)
    find "$backup_dir" -maxdepth 1 -name "ivcrush_*.db" -type f -mtime "+${retention_days}" -delete 2>/dev/null || true

    # Cleanup old Google Drive backups (keep last N days)
    if [ -d "$gdrive_backup_dir" ]; then
        find "$gdrive_backup_dir" -maxdepth 1 -name "ivcrush_*.db" -type f -mtime "+${retention_days}" -delete 2>/dev/null || true
    fi

    # Release lock and clear trap
    rmdir "$backup_lock" 2>/dev/null
    trap - RETURN
}

show_usage() {
    cat << EOF
${BLUE}${BOLD}═══════════════════════════════════════════════════════════════════════${NC}
${BLUE}${BOLD}                    IV Crush 2.0 - Fire-and-Forget Trading${NC}
${BLUE}${BOLD}═══════════════════════════════════════════════════════════════════════${NC}

${BOLD}DESCRIPTION${NC}
    Earnings options trading system that identifies high-probability IV crush
    opportunities using Volatility Risk Premium (VRP) strategy.

    ${GREEN}Strategy:${NC} Sell options when implied volatility > historical volatility,
              profit when IV crushes after earnings announcement.

${BOLD}USAGE${NC}
    $0 TICKER YYYY-MM-DD [YYYY-MM-DD]
    $0 list TICKERS YYYY-MM-DD [offset_days]
    $0 scan YYYY-MM-DD
    $0 whisper [YYYY-MM-DD]
    $0 health
    $0 --help | -h | help

${BOLD}COMMANDS${NC}
    ${GREEN}TICKER analysis${NC}
        Analyze single ticker for earnings IV crush opportunity.
        Auto-calculates expiration as earnings_date + 1 day.
        Auto-backfills historical data if missing (last 3 years).

        Examples:
            $0 TSLA 2025-11-25
            $0 AAPL 2025-11-28 2025-11-29        # Custom expiration

    ${GREEN}list${NC}
        Analyze multiple tickers (comma-separated).
        Auto-fetches earnings dates via Alpha Vantage API.

        Examples:
            $0 list TSLA,NVDA,META 2025-11-27
            $0 list TSLA,NVDA,META 2025-11-27 1    # With offset days

    ${GREEN}scan${NC}
        Scan all earnings for a specific date.
        Fetches calendar from Alpha Vantage, analyzes all tickers.

        Example:
            $0 scan 2025-11-25

    ${GREEN}whisper${NC}
        Fetch and analyze "most anticipated earnings" from Earnings Whispers.
        Uses Reddit API (r/wallstreetbets) or OCR fallback.
        Auto-validates earnings dates (Yahoo Finance + Alpha Vantage).
        Auto-backfills historical data for discovered tickers.

        Examples:
            $0 whisper                            # Current week
            $0 whisper 2025-11-24                 # Specific week (Monday)
            $0 whisper --skip-validation          # Skip date validation

    ${GREEN}health${NC}
        System health check - verify APIs, database, cache operational.

        Example:
            $0 health

    ${GREEN}--help, -h, help${NC}
        Display this help message.

${BOLD}WHAT YOU GET${NC}
    ${YELLOW}✓${NC} Implied Move (interpolated straddle price)
    ${YELLOW}✓${NC} VRP Ratio (2.0x+ = EXCELLENT edge)
    ${YELLOW}✓${NC} Strategy Recommendations (Iron Condor, Credit Spreads)
    ${YELLOW}✓${NC} Strike selections with P/L, Greeks, probabilities
    ${YELLOW}✓${NC} Consistency score (exponentially weighted historical data)
    ${YELLOW}✓${NC} Skew analysis (polynomial fitting for directional bias)
    ${YELLOW}✓${NC} TRADEABLE or SKIP recommendation

${BOLD}OUTPUT EXAMPLE${NC}
    ${GREEN}✅ TRADEABLE OPPORTUNITY${NC}
    VRP Ratio: 2.26x → EXCELLENT

    ${BOLD}★ RECOMMENDED: BULL PUT SPREAD${NC}
      Strikes: Short \$177.50P / Long \$170.00P
      Net Credit: \$2.20
      Max Profit: \$8,158.50 (37 contracts)
      Probability of Profit: 69.1%
      Reward/Risk: 0.42
      Theta: +\$329/day

${BOLD}FEATURES${NC}
    ${YELLOW}Database:${NC} 675 earnings moves, 52 tickers (2022-2024)
    ${YELLOW}Strategy:${NC} VRP-based earnings IV crush detection
    ${YELLOW}Validation:${NC} Sharpe 8.07, 100% win rate on 8 selected trades (Q2-Q4 2024)
    ${YELLOW}Cache:${NC} L1 (memory 30s) + L2 (SQLite 300s) hybrid for performance
    ${YELLOW}Database:${NC} WAL mode for concurrent access, 30s connection timeouts
    ${YELLOW}Resilience:${NC} Circuit breakers, retry logic, health monitoring

${BOLD}REQUIREMENTS${NC}
    - Tradier API key (TRADIER_API_KEY in .env)
    - Alpha Vantage API key (ALPHA_VANTAGE_KEY in .env)
    - Python 3.11+ with venv setup

${BOLD}DOCUMENTATION${NC}
    README.md               - Full system documentation
    LIVE_TRADING_GUIDE.md   - Trading operations guide
    docs/METRICS_GUIDE.md   - Understanding VRP, Edge Score, and metrics

${BOLD}MORE INFO${NC}
    Repository: https://github.com/pmak99/trading-desk
    Issues: Report bugs via GitHub Issues

${BLUE}${BOLD}═══════════════════════════════════════════════════════════════════════${NC}
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

        python scripts/backfill_yfinance.py "$ticker" \
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

validate_earnings_dates() {
    # Cross-reference earnings dates from Yahoo Finance and Alpha Vantage
    # to ensure accuracy before analysis
    echo -e "${BLUE}🔍 Validating earnings dates...${NC}"

    # Run validation for whisper tickers (non-blocking, informational)
    # Let the script display its own progress bar - grep would buffer/filter it
    python scripts/validate_earnings_dates.py --whisper-week 2>&1 || true
    echo ""
}

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

    scan)
        if [ -z "${2:-}" ]; then
            echo -e "${RED}Error: Scan date required${NC}"
            echo "Usage: $0 scan YYYY-MM-DD"
            exit 1
        fi
        health_check
        backup_database
        scan_earnings "$2"
        show_summary
        ;;

    whisper)
        health_check
        backup_database

        # Check if --skip-validation flag is present
        SKIP_VALIDATION=false
        WEEK_ARG=""
        for arg in "$@"; do
            if [[ "$arg" == "--skip-validation" ]]; then
                SKIP_VALIDATION=true
            elif [[ "$arg" != "whisper" ]]; then
                WEEK_ARG="$arg"
            fi
        done

        # Run validation unless skipped
        if [[ "$SKIP_VALIDATION" == false ]]; then
            validate_earnings_dates
        else
            echo -e "${YELLOW}⚠️  Skipping earnings date validation${NC}"
            echo ""
        fi

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
        backup_database
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
        backup_database
        analyze_single "$1" "$2" "${3:-}"
        show_summary
        ;;
esac

echo -e "${GREEN}✓ Complete${NC}"
