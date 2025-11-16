#!/bin/bash
# IV Crush 2.0 - Fire-and-Forget Trading Script
# Strategy: Balanced (Sharpe 8.07, Win Rate 100% on 8 trades Q2-Q4 2024)
# Position Sizing: Half-Kelly (5%) - Conservative start, validated with 208 empirical trades
#
# Usage:
#   ./trade.sh NVDA 2025-11-20                    # Single ticker
#   ./trade.sh list NVDA,WMT,AMD 2025-11-20       # Multiple tickers
#   ./trade.sh scan 2025-11-20                     # Scan all earnings for date
#   ./trade.sh whisper [YYYY-MM-DD]                # Most anticipated earnings (current or specific week)
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
            $0 NVDA 2025-11-20
            $0 AAPL 2025-01-31 2025-02-01        # Custom expiration

    ${GREEN}list${NC}
        Analyze multiple tickers (comma-separated).
        Auto-fetches earnings dates via Alpha Vantage API.

        Examples:
            $0 list NVDA,WMT,AMD 2025-11-20
            $0 list NVDA,WMT,AMD 2025-11-20 1    # With offset days

    ${GREEN}scan${NC}
        Scan all earnings for a specific date.
        Fetches calendar from Alpha Vantage, analyzes all tickers.

        Example:
            $0 scan 2025-11-20

    ${GREEN}whisper${NC}
        Fetch and analyze "most anticipated earnings" from Earnings Whispers.
        Uses Reddit API (r/wallstreetbets) or OCR fallback.
        Auto-backfills historical data for discovered tickers.

        Examples:
            $0 whisper                            # Current week
            $0 whisper 2025-11-10                 # Specific week (Monday)

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
    ${YELLOW}Strategy:${NC} Balanced with Half-Kelly Position Sizing (5% capital per trade)
    ${YELLOW}Validation:${NC} Sharpe 8.07, 100% win rate on 8 selected trades (Q2-Q4 2024)
    ${YELLOW}Cache:${NC} L1 (memory 30s) + L2 (SQLite 300s) hybrid for performance
    ${YELLOW}Database:${NC} WAL mode for concurrent access, 30s connection timeouts
    ${YELLOW}Resilience:${NC} Circuit breakers, retry logic, health monitoring

${BOLD}POSITION SIZING${NC}
    ${YELLOW}Current:${NC} Half-Kelly (5% of capital per trade)
    ${YELLOW}Target:${NC} Quarter-Kelly (10%) after live validation
    ${YELLOW}Basis:${NC} 208 empirical trades from Q2-Q4 2024

${BOLD}REQUIREMENTS${NC}
    - Tradier API key (TRADIER_API_KEY in .env)
    - Alpha Vantage API key (ALPHA_VANTAGE_KEY in .env)
    - Python 3.11+ with venv setup

${BOLD}DOCUMENTATION${NC}
    README.md               - Full system documentation
    LIVE_TRADING_GUIDE.md   - Trading operations guide
    POSITION_SIZING_DEPLOYMENT.md - Position sizing & validation

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

    # Display analysis results (filter timestamps)
    # Check if analysis succeeded by looking for "ANALYSIS RESULTS" in output
    if echo "$analysis_output" | grep -q "ANALYSIS RESULTS"; then
        # Show from ANALYSIS RESULTS onward to capture full output including SUMMARY section
        echo "$analysis_output" | \
            sed -n '/ANALYSIS RESULTS/,$p' | \
            sed 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.\] - [^ ]* - INFO - //'
    else
        # Analysis failed - show error messages
        echo -e "${RED}Analysis failed${NC}"
        echo ""
        echo "$analysis_output" | grep -E "ERROR|Analysis failed|No options|No historical" | \
            sed 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.\] - [^ ]* - ERROR - //' | \
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

    # FIXED: Consistent error handling - return on failure
    python scripts/scan.py \
        --tickers "$tickers" \
        --expiration-offset "$offset_days" 2>&1 | \
        grep -v "^[0-9]\{4\}-" | \
        grep -A 500 "Processing\|VRP\|TRADEABLE\|SKIP\|Score\|Strategy\|Analysis Complete\|Total\|Analyzed\|Skipped\|Errors\|OPPORTUNITIES" || {
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
        grep -A 500 "SCANNING MODE\|Found\|Processing\|VRP\|Score\|TRADEABLE\|Scan Complete\|Total\|Analyzed\|Skipped\|Errors\|OPPORTUNITIES" || {
        echo -e "${RED}Scan failed${NC}"
        echo -e "${YELLOW}No earnings found for this date${NC}"
        return 1
    }
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

    # Build command
    local cmd="python scripts/scan.py --whisper-week"
    if [ -n "$week_monday" ]; then
        cmd="$cmd $week_monday"
    fi

    # Run whisper mode
    $cmd 2>&1 | \
        sed 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.\] - [^ ]* - INFO - //' | \
        sed 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.\] - [^ ]* - ERROR - /⚠️  /' | \
        sed 's/^[0-9]\{4\}-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9] - \[.\] - [^ ]* - WARNING - /⚠️  /' | \
        grep -E "WHISPER MODE|Retrieved|Tickers:|Filtered|VRP|TRADEABLE|SKIP|Analysis|Total|Analyzed|Skipped|Errors|OPPORTUNITIES|SUMMARY|RESULT|Most Anticipated|Why This Matters|Next Steps|Mode:|Week:|⚠️|Failed|^   [0-9]+\.|^   •|Edge|EXCELLENT|GOOD" || {
        echo -e "${RED}Whisper mode failed${NC}"
        echo -e "${YELLOW}Could not fetch most anticipated earnings${NC}"
        echo -e "${YELLOW}Tip: Check Twitter access or use a different week${NC}"
        return 1
    }
    echo ""
}

show_summary() {
    # Summary is now included in the output from the Python scripts
    # No additional summary needed
    :
}

# Main logic
case "$1" in
    help|--help|-h)
        show_usage
        exit 0
        ;;

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

    whisper)
        health_check
        whisper_mode "$2"
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
