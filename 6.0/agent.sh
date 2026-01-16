#!/bin/bash
# 6.0 Agent System CLI Entry Point
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

# Python from 2.0 venv in main repo
PYTHON="${MAIN_REPO}/2.0/venv/bin/python"

# Check if Python venv exists
if [ ! -f "$PYTHON" ]; then
    echo "Error: Python venv not found at $PYTHON"
    echo "Please run: cd ../2.0 && python -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
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

    *)
        echo "6.0 Agent System - Trading Desk"
        echo ""
        echo "Usage:"
        echo "  $0 prime [DATE]           # Pre-cache sentiment"
        echo "  $0 whisper [DATE]         # Most anticipated earnings"
        echo "  $0 analyze TICKER [DATE]  # Deep dive on single ticker"
        echo "  $0 maintenance health     # System health check"
        echo ""
        echo "Examples:"
        echo "  $0 prime"
        echo "  $0 prime 2026-02-05"
        echo "  $0 whisper"
        echo "  $0 whisper 2026-02-05"
        echo "  $0 analyze NVDA"
        echo "  $0 analyze NVDA 2026-02-05"
        echo "  $0 maintenance health"
        echo ""
        exit 1
        ;;
esac
