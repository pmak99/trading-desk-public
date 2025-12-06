#!/bin/bash
# Add tickers to database for syncing
#
# Usage:
#   ./scripts/add_tickers.sh AAPL MSFT GOOGL
#   ./scripts/add_tickers.sh --file tickers.txt
#   echo "AAPL,MSFT,GOOGL" | ./scripts/add_tickers.sh --stdin

set -e

DB_PATH="data/ivcrush.db"
PLACEHOLDER_DATE="2025-12-15"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

cd "$(dirname "$0")/.."

add_ticker() {
    local ticker="$1"

    # Check if already exists
    count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM earnings_calendar WHERE ticker = '$ticker' AND earnings_date >= date('now')")

    if [ "$count" -gt 0 ]; then
        echo -e "${YELLOW}✓ $ticker: Already in database${NC}"
        return 0
    fi

    # Add ticker
    sqlite3 "$DB_PATH" "INSERT OR IGNORE INTO earnings_calendar (ticker, earnings_date, timing, confirmed) VALUES ('$ticker', '$PLACEHOLDER_DATE', 'UNKNOWN', 0)"
    echo -e "${GREEN}+ $ticker: Added to database${NC}"
}

# Parse arguments
if [ "$1" = "--file" ]; then
    # Read from file
    while read -r ticker; do
        [ -z "$ticker" ] && continue
        add_ticker "$ticker"
    done < "$2"
elif [ "$1" = "--stdin" ]; then
    # Read from stdin (comma or newline separated)
    while IFS=',' read -ra TICKERS; do
        for ticker in "${TICKERS[@]}"; do
            ticker=$(echo "$ticker" | xargs)  # Trim whitespace
            [ -z "$ticker" ] && continue
            add_ticker "$ticker"
        done
    done
else
    # Read from command line args
    for ticker in "$@"; do
        add_ticker "$ticker"
    done
fi

echo ""
echo "Run './trade.sh sync' to fetch correct earnings dates"
