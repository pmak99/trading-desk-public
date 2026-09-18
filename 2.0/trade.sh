#!/bin/bash
# IV Crush 2.0 - Fire-and-Forget Trading Script
# Strategy: Balanced (Sharpe 8.07, Win Rate 100% on 8 trades Q2-Q4 2024)
#
# Usage:
#   ./trade.sh TSLA 2025-11-25                    # Single ticker
#   ./trade.sh list TSLA,NVDA,META 2025-11-27       # Multiple tickers, one earnings date
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

# Load .env so every subcommand reaches Postgres.
#
# Without this, anything touching the database (trr, scan, whisper, health,
# analyze) falls back to libpq defaults and dies with
#   connection to server on socket "/tmp/.s.PGSQL.5432" failed
# because PGHOST is unset. That made correctness depend on whether the caller
# happened to have sourced .env first -- most slash commands did not.
#
# `set -a` is what exports; a plain `source` leaves the vars unexported and the
# Python children see nothing. Callers that already source .env are unaffected.
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

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

    # Route through the Python DTE-floor logic (same path as scan/whisper):
    # Mon/Tue -> same-week Friday, Wed -> next Friday, Thu/Fri -> next week's
    # Friday. Enforces MIN_DTE (default 3, CLAUDE.md §5), read as `list` reads it;
    # until 2026-09-14 this call left it at the default. The old
    # shell version did earnings+1 day, which put Thu/Fri reporters on a
    # 0-1 DTE chain.
    expiration=$(python -c "
import sys, datetime
from src.config.config import Config
from src.domain.trading_expiration import calculate_expiration_date
from src.domain.enums import EarningsTiming
print(calculate_expiration_date(datetime.date.fromisoformat(sys.argv[1]), EarningsTiming.UNKNOWN,
                                min_dte=Config.from_env().thresholds.min_dte))
" "$earnings_date" 2>/dev/null)

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

# backup_database() was removed at the Postgres cutover (2026-09-12).
# It was 217 lines that backed up 2.0/data/ivcrush.db -- a file that no
# longer exists -- and nothing in this script ever called it. Its only
# coverage was a shell test grepping for its own definition.
# Backups now: a host-side pg-backup script (outside this repo), deployed to
# /home/trading/scripts/pg-backup.sh and run nightly by
# trading-pg-backup.timer -- pg_dump -Fc to local disk, the backup host and Drive.

sync_earnings_calendar() {
    # Sync earnings calendar with latest data from Finnhub (chunked bulk fetch)
    # cross-validated against Yahoo Finance.
    # Default: focused mode — only traded tickers (strategies + trade_journal).
    # Use --all to sync the full calendar (slow, thousands of tickers).
    #
    # Args:
    #   --dry-run          Preview only, no DB writes
    #   --check-staleness  Report stale dates and exit
    #   --all              Sync all tickers (not just traded ones)
    #   --threshold N      Staleness threshold in days
    #
    # The Python script is report-only unless told --execute (2026-09-13), so
    # this wrapper passes it by default and --dry-run is what withholds it.

    local mode_flag="--execute"
    local extra_args=""

    # Parse arguments
    for arg in "$@"; do
        case "$arg" in
            --dry-run|-n)
                mode_flag=""
                ;;
            --check-staleness|-s)
                extra_args="$extra_args --check-staleness"
                ;;
            --all)
                extra_args="$extra_args --all"
                ;;
            --threshold*)
                extra_args="$extra_args $arg"
                ;;
        esac
    done

    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}  Earnings Calendar Sync${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"

    if [ -z "$mode_flag" ]; then
        echo -e "${YELLOW}Mode: DRY RUN (preview only)${NC}"
    fi

    # The full output goes to a file; the terminal gets what decides the run: every
    # warning and error line, then the summary. A focused sync logs ~800 lines, and
    # the unattended /maintenance run on 2026-09-15 saw a tool output truncated
    # before the summary -- it reported one date conflict of four and read exit 1
    # as harmless. weekly-maintenance.sh prunes these logs after 12 weeks.
    local log_dir="${TRADING_LOG_DIR:-$HOME/logs}"
    mkdir -p "$log_dir"
    local log_file
    log_file="$log_dir/sync-calendar-$(TZ=America/New_York date +%Y%m%d-%H%M%S).log"

    local exit_code=0
    python scripts/sync_earnings_calendar.py $mode_flag $extra_args > "$log_file" 2>&1 || exit_code=$?

    local prefix='s/^[0-9]\{4\}-[0-9-]* [0-9:]* - \[[^]]*\] - [^ ]* - //'
    if grep -q "SYNC SUMMARY" "$log_file"; then
        echo "Warnings and errors:"
        grep -E " - (WARNING|ERROR|CRITICAL) - " "$log_file" | sed "$prefix" || echo "  (none)"
        sed -n '/SYNC SUMMARY/,$p' "$log_file" | sed "$prefix"
    elif [[ "$extra_args" == *--check-staleness* ]]; then
        sed "$prefix" "$log_file"
    else
        echo -e "${RED}No SYNC SUMMARY: the sync did not finish. Last 60 lines:${NC}"
        tail -n 60 "$log_file"
    fi
    echo "Full output ($(wc -l < "$log_file" | tr -d ' ') lines): $log_file"

    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}✓ Earnings calendar synced${NC}\n"
    elif [[ "$extra_args" == *--check-staleness* ]]; then
        echo -e "${RED}✗ Stale data detected${NC}\n"
    else
        echo -e "${RED}✗ Exit ${exit_code}: see Errors above (need review vs failed)${NC}\n"
    fi

    return $exit_code
}

show_usage() {
    cat << EOF
${BLUE}${BOLD}IV Crush 2.0 - Earnings IV Crush Trading${NC}

${BOLD}USAGE${NC}
    $0 TICKER YYYY-MM-DD             Single ticker analysis
    $0 list TICKERS YYYY-MM-DD [N]   Multiple tickers (comma-separated) reporting on the date;
                                     N: expiration N days out instead of the weekday rule
    $0 scan YYYY-MM-DD               Scan all earnings for date
    $0 whisper [YYYY-MM-DD]          Most anticipated earnings
    $0 trr TICKER [TICKER ...]       Live tail-risk ratio (never read position_limits)
    $0 harvest                       45 DTE non-earnings premium candidates
    $0 taco ARGS...                  TACO index macro-event engine (see taco_cli.py)
    $0 sync [--dry-run]              Sync earnings calendar
    $0 health                        System health check

${BOLD}EXAMPLES${NC}
    $0 TSLA 2025-11-25
    $0 list TSLA,NVDA,META 2025-11-27
    $0 scan 2025-11-25
    $0 whisper
    $0 trr NVDA AMD MU
    $0 harvest

${BOLD}REQUIREMENTS${NC}
    TRADIER_API_KEY, FINNHUB_API_KEY in .env
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

    # Store bias prediction for later validation (silent, non-blocking).
    # analyze.py does not run the scan path, where scan/whisper/list/the bot
    # write theirs; the script keeps the event's first pre-open prediction,
    # so a repeat run writes nothing. The date is the one analysed: until
    # 2026-09-14 the script took the ticker's next calendar date instead.
    python scripts/store_bias_prediction.py "$ticker" --earnings-date "$earnings_date" >/dev/null 2>&1 || true

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
            --end-date "$end_date" --execute 2>&1 | \
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
    local offset_days=${3:-}

    # Validate date
    validate_date "$earnings_date" || return 1

    # Validate each ticker in comma-separated list
    IFS=',' read -ra TICKER_ARRAY <<< "$tickers"
    for ticker in "${TICKER_ARRAY[@]}"; do
        validate_ticker "$ticker" || return 1
    done

    if [ -n "$offset_days" ] && [[ ! "$offset_days" =~ ^[0-9]+$ ]]; then
        echo -e "${RED}Error: Invalid offset '$offset_days'${NC}"
        echo "Offset must be a whole number of days (e.g., 3)"
        return 1
    fi

    # The expiration the scan applies, not the one given. Without an offset it is
    # the weekday rule; an offset below MIN_DTE is raised to it (CLAUDE.md §5).
    # Until 2026-09-14 the offset defaulted to 1, so the weekday rule never
    # applied, and this header printed "+1 days" while every ticker used +3.
    local floor expiration offset_used min_dte
    floor=$(python -c "
import sys
from datetime import date
from src.config.config import Config
from src.domain.enums import EarningsTiming
from src.domain.trading_expiration import calculate_expiration_date, floored_offset_days
min_dte = Config.from_env().thresholds.min_dte
offset = int(sys.argv[2]) if sys.argv[2] else None
expiration = calculate_expiration_date(
    date.fromisoformat(sys.argv[1]), EarningsTiming.UNKNOWN, offset, min_dte=min_dte)
print(expiration, floored_offset_days(offset, min_dte) if offset is not None else '-', min_dte)
" "$earnings_date" "$offset_days") || {
        echo -e "${RED}Error: Could not compute the expiration for $earnings_date${NC}"
        return 1
    }
    read -r expiration offset_used min_dte <<< "$floor"

    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${BLUE}${BOLD}    Analyzing Multiple Tickers${NC}"
    echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
    echo -e "${YELLOW}Tickers:${NC} $tickers"
    echo -e "${YELLOW}Earnings Date:${NC} $earnings_date"
    if [ -z "$offset_days" ]; then
        echo -e "${YELLOW}Expiration:${NC} ${expiration} (weekday rule, at least ${min_dte} DTE)"
    elif [ "$offset_used" != "$offset_days" ]; then
        echo -e "${YELLOW}Expiration Offset:${NC} +${offset_used} days (+${offset_days} raised to the ${min_dte}-day minimum)"
    else
        echo -e "${YELLOW}Expiration Offset:${NC} +${offset_used} days"
    fi
    echo ""

    # Every ticker is analysed for $earnings_date (with its stored BMO/AMC tag);
    # the offset reaches the scan only when one was given.
    # Run list analysis with real-time output (unbuffered Python + direct piping)
    # Optimized: Single sed with multiple expressions (3-4x faster than chained sed)
    if ! python -u scripts/scan.py --tickers "$tickers" --earnings-date "$earnings_date" ${offset_days:+--expiration-offset "$offset_days"} 2>&1 | \
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
        echo -e "${YELLOW}Tip: Try a different week${NC}"
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
if [[ -n "${1:-}" && "${1:-}" != "help" && "${1:-}" != "--help" && "${1:-}" != "-h" && "${1:-}" != "health" ]]; then
    python scripts/validate_bias_predictions.py --execute >/dev/null 2>&1 || true
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

    harvest)
        health_check
        python -u scripts/scan.py --harvest 2>&1 | grep -v "^$"
        show_summary
        ;;

    taco)
        # TACO index macro-event engine (see .claude/commands/taco.md)
        python -u scripts/taco_cli.py "${@:2}"
        ;;

    trr)
        # Live Tail Risk Ratio for one or more tickers, matching the engine
        # exactly (see scripts/compute_live_trr.py). Do not query
        # position_limits.tail_risk_ratio/tail_risk_level directly — that
        # snapshot is frozen at 2026-06-24 (ORATS sunset) and can disagree
        # with a live recompute on the tier, not just the number.
        if [ -z "${2:-}" ]; then
            echo -e "${RED}Error: At least one ticker required${NC}"
            echo "Usage: $0 trr TICKER [TICKER ...]"
            exit 1
        fi
        python scripts/compute_live_trr.py "${@:2}"
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
        analyze_list "$2" "$3" "${4:-}"
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
