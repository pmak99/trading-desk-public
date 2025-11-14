#!/bin/bash
# IV Crush 2.0 - Fire-and-Forget Trading Script
# Strategy: Balanced (Sharpe 8.07, Win Rate 100% on 8 trades Q2-Q4 2024)
# Position Sizing: Half-Kelly (5%) - Conservative start, validated with 208 empirical trades
#
# Usage:
#   ./trade.sh NVDA 2025-11-20                    # Single ticker
#   ./trade.sh list NVDA,WMT,AMD 2025-11-20       # Multiple tickers
#   ./trade.sh scan 2025-11-20                     # Scan all earnings for date
#   ./trade.sh health                              # Health check

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

# Change to script directory
cd "$(dirname "$0")"

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

    # Try GNU date (Linux)
    expiration=$(date -d "$earnings_date + 1 day" +%Y-%m-%d 2>/dev/null)

    # Try BSD date (macOS) if GNU date failed
    if [ -z "$expiration" ]; then
        expiration=$(date -v+1d -j -f "%Y-%m-%d" "$earnings_date" +%Y-%m-%d 2>/dev/null)
    fi

    # FIXED: Proper error handling if both fail
    if [ -z "$expiration" ]; then
        echo -e "${RED}Error: Could not calculate expiration date${NC}"
        echo "Please provide expiration date manually:"
        echo "  $0 $ticker $earnings_date YYYY-MM-DD"
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

show_usage() {
    echo -e "${BOLD}IV Crush 2.0 - Fire-and-Forget Trading${NC}"
    echo ""
    echo "Usage:"
    echo "  $0 TICKER YYYY-MM-DD [YYYY-MM-DD]           # Analyze single ticker"
    echo "  $0 list TICKERS YYYY-MM-DD [offset_days]    # Analyze multiple tickers"
    echo "  $0 scan YYYY-MM-DD                           # Scan earnings for date"
    echo "  $0 health                                    # Health check only"
    echo ""
    echo "Examples:"
    echo "  ${GREEN}# Single ticker with strategy recommendations${NC}"
    echo "  $0 NVDA 2025-11-20"
    echo "  $0 NVDA 2025-11-20 2025-11-22              # Custom expiration"
    echo ""
    echo "  ${GREEN}# Multiple tickers (comma-separated)${NC}"
    echo "  $0 list NVDA,WMT,AMD 2025-11-20"
    echo "  $0 list NVDA,WMT,AMD 2025-11-20 1          # With offset days"
    echo ""
    echo "  ${GREEN}# Scan all earnings for a specific date${NC}"
    echo "  $0 scan 2025-11-20"
    echo ""
    echo "  ${GREEN}# Quick health check${NC}"
    echo "  $0 health"
    echo ""
    echo -e "${YELLOW}Features:${NC}"
    echo "  ✓ Complete VRP analysis with edge scores"
    echo "  ✓ Strategy recommendations (Iron Condor, Credit Spreads)"
    echo "  ✓ Strike selections with P/L calculations"
    echo "  ✓ Phase 4 metrics (consistency, skew analysis)"
    echo ""
    echo -e "${YELLOW}Database:${NC} 675 earnings moves, 52 tickers (2022-2024)"
    echo -e "${YELLOW}Strategy:${NC} Balanced with Half-Kelly Position Sizing (5% capital per trade)"
    echo ""
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
        local start_date
        start_date=$(date -d "3 years ago" +%Y-%m-%d 2>/dev/null || date -v-3y +%Y-%m-%d 2>/dev/null || echo "2022-01-01")
        local end_date
        end_date=$(date -d "yesterday" +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d 2>/dev/null || echo "2025-11-12")

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

    # Display analysis results (filter timestamps and show from ANALYSIS RESULTS onward)
    echo "$analysis_output" | \
        sed -n '/ANALYSIS RESULTS/,$p' | \
        sed 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.\] - [^ ]* - INFO - //' || {
        echo -e "${RED}Analysis failed${NC}"
        return 1
    }
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

    # FIXED: Consistent error handling - return on failure
    python scripts/scan.py \
        --tickers "$tickers" \
        --expiration-offset "$offset_days" 2>&1 | \
        grep -v "^[0-9]\{4\}-" | \
        grep -A 500 "Processing\|VRP\|TRADEABLE\|SKIP\|Score\|Strategy" || {
        echo -e "${RED}Analysis failed${NC}"
        echo -e "${YELLOW}Note: Auto-fetch earnings may not work. Try single ticker mode with explicit dates.${NC}"
        return 1
    }
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

    # FIXED: Consistent error handling - return on failure
    python scripts/scan.py \
        --scan-date "$scan_date" 2>&1 | \
        grep -v "^[0-9]\{4\}-" | \
        grep -A 500 "SCANNING MODE\|Found\|Processing\|VRP\|Score\|TRADEABLE\|opportunities" || {
        echo -e "${RED}Scan failed${NC}"
        echo -e "${YELLOW}No earnings found for this date${NC}"
        return 1
    }
    echo ""
}

show_summary() {
    echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}${BOLD}    Next Steps${NC}"
    echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "If ✅ TRADEABLE:"
    echo "  1. Review recommended strategy (Iron Condor, Credit Spreads, etc.)"
    echo "  2. Check strikes, net credit, and max profit/loss"
    echo "  3. Execute trade in your broker"
    echo "  4. Position size: 5% of capital (half-Kelly, conservative start)"
    echo "  5. Track outcome vs prediction"
    echo ""
    echo "If ⏭️ SKIP:"
    echo "  • VRP too low (< 1.5x)"
    echo "  • Move to next opportunity"
    echo "  • Don't force trades"
    echo ""
    echo -e "${YELLOW}Remember:${NC} Use 5% position sizing (half-Kelly). Target: 85-92% win rate"
    echo ""
}

# Main logic
case "$1" in
    health)
        health_check
        ;;

    scan)
        if [ -z "$2" ]; then
            echo -e "${RED}Error: Scan date required${NC}"
            echo "Usage: $0 scan YYYY-MM-DD"
            exit 1
        fi
        health_check
        scan_earnings "$2"
        show_summary
        ;;

    list)
        if [ -z "$2" ] || [ -z "$3" ]; then
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
        if [ -z "$2" ]; then
            echo -e "${RED}Error: Earnings date required${NC}"
            echo "Usage: $0 $1 YYYY-MM-DD [YYYY-MM-DD]"
            exit 1
        fi
        health_check
        analyze_single "$1" "$2" "$3"
        show_summary
        ;;
esac

echo -e "${GREEN}✓ Complete${NC}"
