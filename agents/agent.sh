#!/bin/bash
# agents Agent System CLI Entry Point
#
# Usage:
#   ./agent.sh prime [DATE]           # Pre-cache sentiment
#   ./agent.sh whisper [DATE]         # Most anticipated earnings
#   ./agent.sh analyze TICKER [DATE]  # Deep dive on single ticker
#   ./agent.sh maintenance health     # System health check
#
# Examples:
#   ./agent.sh prime
#   ./agent.sh prime 2026-02-05
#   ./agent.sh whisper
#   ./agent.sh whisper 2026-02-05
#   ./agent.sh analyze NVDA
#   ./agent.sh analyze NVDA 2026-02-05
#   ./agent.sh maintenance health

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Find main repo root
# If in worktree, .git is a file pointing to main repo
if [ -f "$(git rev-parse --git-dir 2>/dev/null)/commondir" ]; then
    # We're in a worktree - find main repo via git dir
    MAIN_REPO="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
else
    # We're in main repo
    MAIN_REPO="$(git rev-parse --show-toplevel)"
fi

# Load environment variables from existing .env files (secrets interpolation)
# This avoids duplicating secrets - sources from existing secure locations
#
# Strategy: Load cloud first (for PERPLEXITY_API_KEY), then core (for DB_PATH, TRADIER, etc.)
# core loaded last so its paths take priority
for ENV_FILE in "${MAIN_REPO}/cloud/.env" "${MAIN_REPO}/core/.env" "${SCRIPT_DIR}/.env"; do
    if [ -f "$ENV_FILE" ]; then
        # Validate .env contains only VAR=VALUE lines (no commands)
        if grep -qvE '^\s*(#.*|[A-Za-z_][A-Za-z0-9_]*=.*|)\s*$' "$ENV_FILE"; then
            echo "WARNING: $ENV_FILE contains non-variable lines, skipping" >&2
            continue
        fi
        set -a  # Auto-export all variables
        source "$ENV_FILE" 2>/dev/null || true
        set +a
    fi
done

# Ensure DB_PATH is absolute (relative paths cause issues when running from agents/)
if [[ "$DB_PATH" != /* ]]; then
    export DB_PATH="${MAIN_REPO}/core/${DB_PATH}"
fi

# Python from core venv in main repo
PYTHON="${MAIN_REPO}/core/venv/bin/python"

# Check if Python venv exists
if [ ! -f "$PYTHON" ]; then
    echo "Error: Python venv not found at $PYTHON"
    echo "Please run: cd ../core && python -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Parse command
COMMAND="$1"
shift || true  # Don't exit if no more args

case "$COMMAND" in
    prime)
        DATE="${1:-}"
        if [ -z "$DATE" ]; then
            echo "Running /prime (next 5 days)..."
            exec "$PYTHON" -m src.cli.prime
        else
            echo "Running /prime starting $DATE..."
            exec "$PYTHON" -m src.cli.prime "$DATE"
        fi
        ;;

    whisper)
        DATE="${1:-}"
        if [ -z "$DATE" ]; then
            echo "Running /whisper (next 5 days)..."
            exec "$PYTHON" -m src.cli.whisper
        else
            echo "Running /whisper for $DATE..."
            exec "$PYTHON" -m src.cli.whisper "$DATE"
        fi
        ;;

    analyze)
        TICKER="${1:-}"
        DATE="${2:-}"

        if [ -z "$TICKER" ]; then
            echo "Error: TICKER required"
            echo "Usage: $0 analyze TICKER [DATE]"
            exit 1
        fi

        if [ -z "$DATE" ]; then
            echo "Running /analyze $TICKER (auto-detect date)..."
            exec "$PYTHON" -m src.cli.analyze "$TICKER"
        else
            echo "Running /analyze $TICKER $DATE..."
            exec "$PYTHON" -m src.cli.analyze "$TICKER" "$DATE"
        fi
        ;;

    maintenance)
        TASK="${1:-health}"
        shift || true
        echo "Running /maintenance $TASK..."
        exec "$PYTHON" -m src.cli.maintenance "$TASK" "$@"
        ;;

    help|-h|--help)
        echo "═══════════════════════════════════════════════════════════════"
        echo "  agents Agent System - Trading Desk"
        echo "  Agent-based orchestration with parallel processing"
        echo "═══════════════════════════════════════════════════════════════"
        echo ""
        echo "USAGE:"
        echo "  ./agent.sh <command> [arguments]"
        echo ""
        echo "COMMANDS:"
        echo ""
        echo "  prime [DATE]              Pre-cache sentiment for upcoming earnings"
        echo "                            Default: next 5 days"
        echo ""
        echo "  whisper [DATE]            Find most anticipated earnings with VRP analysis"
        echo "                            Shows: TRR badges, real sector warnings, scores"
        echo "                            Default: current/next week (Fri-Sun → next week)"
        echo ""
        echo "  analyze TICKER [DATE]     Deep dive on single ticker"
        echo "                            Shows: VRP, liquidity, sentiment, patterns,"
        echo "                                   position limits, strategies"
        echo "                            Default: auto-detect earnings date"
        echo ""
        echo "  maintenance <task>        System maintenance operations"
        echo "      health                  System health check"
        echo "      data-quality            Database integrity scan"
        echo "      data-quality --fix      Auto-fix safe data issues"
        echo "      data-quality --dry-run  Preview fixes without applying"
        echo "      sector-sync             Populate sector data from Finnhub"
        echo "      cache-cleanup           Clean expired caches"
        echo ""
        echo "  help, -h, --help          Show this help message"
        echo ""
        echo "EXAMPLES:"
        echo "  ./agent.sh prime                      # Cache sentiment for next 5 days"
        echo "  ./agent.sh whisper                    # This week's best opportunities"
        echo "  ./agent.sh whisper 2026-01-27         # Specific week"
        echo "  ./agent.sh analyze NFLX               # Full NFLX analysis"
        echo "  ./agent.sh analyze NVDA 2026-01-28    # NVDA for specific date"
        echo "  ./agent.sh maintenance health         # Check system status"
        echo "  ./agent.sh maintenance data-quality --fix  # Auto-fix data issues"
        echo ""
        echo "TYPICAL WORKFLOW:"
        echo "  Morning:  ./agent.sh prime            # Pre-cache sentiment (~10s)"
        echo "  Then:     ./agent.sh whisper          # Instant results from cache"
        echo "  Pick:     ./agent.sh analyze NVDA     # Deep dive on best candidate"
        echo ""
        echo "PHASE 3 FEATURES:"
        echo "  • TRR-based position limits (prevents oversizing on high-risk tickers)"
        echo "  • Real sector data from Finnhub (proper cross-ticker correlation)"
        echo "  • Historical pattern recognition (directional bias, streaks, trends)"
        echo "  • Automated data quality fixes (--fix mode)"
        echo ""
        exit 0
        ;;

    *)
        echo "Unknown command: $COMMAND"
        echo ""
        echo "Run './agent.sh help' for usage information."
        echo ""
        exit 1
        ;;
esac
