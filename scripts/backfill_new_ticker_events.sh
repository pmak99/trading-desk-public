#!/usr/bin/env bash
# /maintenance Step 7b: backfill tickers new to the system, one stored BMO/AMC
# event at a time, report-only first.
#
# Usage: scripts/backfill_new_ticker_events.sh EVENTS_FILE [MAX_EVENTS]
#   EVENTS_FILE  "TICKER YYYY-MM-DD" per line -- Step 7a's query output
#   MAX_EVENTS   stop after this many (default 100); the rest wait for next week
#
# For each event: run backfill_historical.py report-only for that one day. If it
# printed `Would add`, discovery would insert calendar rows on one source -- say
# REVIEW and do not run it. If it printed no `Would save`, there is nothing to
# record -- say REVIEW. Otherwise run the same command with --execute.
#
# Since 2026-09-14 a one-day run on a stored event cannot print `Would add`:
# discovery runs only for a ticker with no move row, and only inside the day,
# which is already stored. The check is a backstop against that regressing.
#
# This was a while-loop pasted into maintenance.md until 2026-09-14. It moved
# here because the weekly run no longer skips permissions, and Claude Code's
# checker refuses a `while`/`if` compound whatever the rules say; one command
# with one rule is what it can match. The logic is unchanged.
#
# Runs in the foreground by design: ~20 s an event (two ~1.3 s script runs and
# two 8 s pauses for Twelve Data's 8-a-minute free tier), so 100 events is ~35
# minutes. /maintenance calls it with a one-hour Bash timeout, never in the
# background -- a background job dies with the `claude -p` session that
# started it.
set -uo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "usage: scripts/backfill_new_ticker_events.sh EVENTS_FILE [MAX_EVENTS]" >&2
    exit 2
fi

EVENTS=$1
MAX=${2:-100}
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# PYTHON is for tests; the default is the venv every other step uses.
PY="${PYTHON:-$ROOT/2.0/venv/bin/python}"
BACKFILL="$ROOT/2.0/scripts/backfill_historical.py"
SPACING_SEC=8

if [ ! -r "$EVENTS" ]; then
    echo "backfill_new_ticker_events: cannot read $EVENTS" >&2
    exit 2
fi

total=$(grep -c '[^[:space:]]' "$EVENTS" || true)
done_count=0

while read -r TICKER DAY; do
    [ -n "$TICKER" ] || continue
    [ "$done_count" -lt "$MAX" ] || break
    done_count=$((done_count + 1))

    OUT=$("$PY" "$BACKFILL" "$TICKER" --start-date "$DAY" --end-date "$DAY" 2>&1)
    if printf '%s\n' "$OUT" | grep -q "Would add"; then
        echo "REVIEW $TICKER $DAY: discovery would insert calendar rows (single source) -- not run"
        printf '%s\n' "$OUT" | grep "Would add"
    elif ! printf '%s\n' "$OUT" | grep -q "Would save"; then
        echo "REVIEW $TICKER $DAY: no move computed (no bars, or a closed-market date)"
    else
        sleep "$SPACING_SEC"
        "$PY" "$BACKFILL" --execute "$TICKER" --start-date "$DAY" --end-date "$DAY" 2>&1 \
            | grep -E "Saved|Failed|Could not"
    fi
    sleep "$SPACING_SEC"
done < "$EVENTS"

echo "processed $done_count of $total events; $((total - done_count)) remain"
