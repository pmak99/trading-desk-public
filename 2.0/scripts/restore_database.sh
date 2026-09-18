#!/usr/bin/env bash
# Restore the trading database from a nightly pg_dump.
#
# Rewritten 2026-09-12. The previous version of this file restored a SQLite
# file into 2.0/data/ivcrush.db -- which nothing has read since the Postgres
# migration. That is the worst kind of leftover: in an incident it would run,
# print success, and leave the operator believing the database was restored.
#
# Companion to the host-side restore-verify job, which proves monthly that
# the dump reconstitutes. This is the one you reach for when you actually need
# the data back.
#
# ⚠️ DEFAULTS TO A SIDE-BY-SIDE DATABASE, NOT PRODUCTION. Restoring over live
# `ivcrush` is destructive and irreversible, so it needs --into-live plus a
# typed confirmation. The safe default lets you inspect a restored copy and
# copy rows across, which is what an incident usually actually needs.
#
# Usage:
#   restore_database.sh                       # newest dump -> ivcrush_restored
#   restore_database.sh --list                # show available dumps
#   restore_database.sh --dump PATH           # a specific dump
#   restore_database.sh --target NAME         # a different side-by-side name
#   restore_database.sh --into-live           # overwrite production (asks)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$(cd "$SCRIPT_DIR/.." && pwd)/.env"

# Where nightly dumps land. the server is the host that makes them; from the Mac,
# pass --dump with a path pulled off Drive or the backup host.
BACKUP_DIR="${PG_BACKUP_DIR:-/home/trading/backups}"
TARGET="ivcrush_restored"
DUMP=""
INTO_LIVE=0

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; NC=$'\033[0m'
log()  { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die()  { printf '%s%s%s\n' "$RED" "FATAL: $*" "$NC" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --list)      LIST=1; shift ;;
        --dump)      DUMP="${2:-}"; shift 2 ;;
        --target)    TARGET="${2:-}"; shift 2 ;;
        --into-live) INTO_LIVE=1; shift ;;
        -h|--help)   sed -n '2,23p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *)           die "unknown argument: $1" ;;
    esac
done

[ -r "$ENV_FILE" ] || die "cannot read $ENV_FILE"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# ⚠️ Resolve the client binaries rather than trusting PATH -- on the Mac libpq
# is keg-only and none of psql/pg_restore are on the Bash tool's PATH (~/.zshrc
# is not consulted there). Same candidate list as scripts/db, deliberately: if
# one of these grows a new location, both need it.
find_pg() {
    local name="$1" p
    p="$(command -v "$name" || true)"
    [ -n "$p" ] && { printf '%s' "$p"; return 0; }
    for p in "/opt/homebrew/opt/libpq/bin/$name" \
             "/usr/local/opt/libpq/bin/$name" \
             "/usr/bin/$name"; do
        [ -x "$p" ] && { printf '%s' "$p"; return 0; }
    done
    return 1
}
PSQL="$(find_pg psql)"             || die "psql not found (Mac: brew install libpq)"
PG_RESTORE="$(find_pg pg_restore)" || die "pg_restore not found (Mac: brew install libpq)"

# ---- --list ------------------------------------------------------------------
if [ -n "${LIST:-}" ]; then
    if [ ! -d "$BACKUP_DIR" ]; then
        echo "No backup directory at $BACKUP_DIR."
        echo "On the Mac, fetch one from Drive or the backup host and pass --dump PATH."
        exit 1
    fi
    printf '%sAvailable dumps in %s:%s\n' "$GREEN" "$BACKUP_DIR" "$NC"
    # `ls -t`, not `find -printf`: -printf is GNU-only and this script has to
    # run on the Mac too. Our own filenames, so no spaces or newlines.
    # shellcheck disable=SC2012
    ls -t "$BACKUP_DIR"/ivcrush-*.dump 2>/dev/null \
        | while read -r f; do printf '  %-44s %6s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)"; done
    exit 0
fi

# ---- pick a dump -------------------------------------------------------------
if [ -z "$DUMP" ]; then
    DUMP=$(find "$BACKUP_DIR" -maxdepth 1 -name 'ivcrush-*.dump' -type f 2>/dev/null | sort | tail -1)
    [ -n "$DUMP" ] || die "no dump found in $BACKUP_DIR (try --dump PATH, or --list)"
fi
[ -r "$DUMP" ] || die "cannot read $DUMP"

# Verify the archive is READABLE before dropping anything. pg_restore -l parses
# the table of contents, so this catches a truncated file that `test -s` passes.
TABLES=$("$PG_RESTORE" -l "$DUMP" 2>/dev/null | grep -c 'TABLE DATA')
[ "${TABLES:-0}" -ge 10 ] || die "$DUMP lists only ${TABLES:-0} tables with data (expected >=10) -- refusing"
log "dump: $DUMP ($(du -h "$DUMP" | cut -f1), $TABLES tables with data)"

# ---- confirm ------------------------------------------------------------------
if [ "$INTO_LIVE" -eq 1 ]; then
    TARGET="$PGDATABASE"
    LIVE_ROWS=$("$PSQL" -tAc "SELECT COALESCE(sum(n_live_tup),0) FROM pg_stat_user_tables" -d "$PGDATABASE" 2>/dev/null || echo "?")
    cat <<EOF

${RED}This OVERWRITES the live trading database.${NC}
  host      : ${PGHOST}:${PGPORT}
  database  : ${PGDATABASE}   (~${LIVE_ROWS} rows right now)
  from      : ${DUMP}

Everything written since that dump is lost. There is no undo.
${YELLOW}If you only need to look at old data, cancel and re-run without --into-live --
that restores side by side and leaves production alone.${NC}

EOF
    printf "Type 'overwrite production' to proceed: "
    read -r reply
    [ "$reply" = "overwrite production" ] || { echo "Aborted."; exit 1; }
else
    log "restoring side by side into '$TARGET' -- production '$PGDATABASE' is untouched"
fi

# ---- restore -------------------------------------------------------------------
# ⚠️ Never restore into ivcrush_test or ivcrush_scratch. The pg-marked tests build
# their per-test schemas in ivcrush_test (since 2026-09-13; before that the
# fixture used production), and scratch is a working copy people use --
# clobbering either turns an incident recovery into a failure that looks
# unrelated. Checked here rather than at parse time so it also covers
# --into-live, where TARGET comes from PGDATABASE.
case "$TARGET" in
    ivcrush_test|ivcrush_scratch)
        die "refusing to restore into $TARGET (tests and working copies live there)" ;;
esac

# ⚠️ Run as `trading`, pg_restore reports an error creating pg_stat_statements:
# only the superuser may create that extension. It is query statistics, not
# application data, so the restored copy is complete without it. After an
# --into-live restore, re-create it as the superuser:
#   docker exec trading-postgres psql -U postgres -d ivcrush -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements'
# On a cluster rebuilt from scratch, create the roles and grants first.

if [ "$INTO_LIVE" -eq 0 ]; then
    "$PSQL" -d postgres -q -c "DROP DATABASE IF EXISTS \"$TARGET\";" || die "could not drop $TARGET"
    "$PSQL" -d postgres -q -c "CREATE DATABASE \"$TARGET\";"         || die "could not create $TARGET"
    RESTORE_ARGS=(--no-owner --no-privileges -d "$TARGET")
else
    # --clean --if-exists drops each object before recreating it, so this works
    # against a populated database without dropping the database itself (which
    # would fail anyway while anything holds a connection).
    RESTORE_ARGS=(--no-owner --no-privileges --clean --if-exists -d "$TARGET")
fi

if ! "$PG_RESTORE" "${RESTORE_ARGS[@]}" "$DUMP" 2>/tmp/.restore_err; then
    # pg_restore warns about non-fatal things (missing roles, DROP of an object
    # that was not there). Show them; judge by the row counts below.
    printf '%s%s%s\n' "$YELLOW" "pg_restore reported issues:" "$NC"
    tail -10 /tmp/.restore_err | sed 's/^/    /'
fi

# ---- report --------------------------------------------------------------------
echo
printf '%s%s%s\n' "$GREEN" "Restored into '$TARGET':" "$NC"
"$PSQL" -d "$TARGET" -c "
    SELECT c.relname AS table_name,
           (xpath('/row/c/text()',
                  query_to_xml(format('SELECT count(*) AS c FROM public.%I', c.relname),
                               false, true, '')))[1]::text::bigint AS rows
      FROM pg_class c
      JOIN pg_namespace ns ON ns.oid = c.relnamespace
     WHERE ns.nspname = 'public' AND c.relkind = 'r'
     ORDER BY rows DESC;"

if [ "$INTO_LIVE" -eq 0 ]; then
    cat <<EOF
Production was not modified. To query the restored copy:

    PGDATABASE=$TARGET scripts/db -c "SELECT ..."

and to drop it when you are done:

    psql -d postgres -c 'DROP DATABASE "$TARGET";'
EOF
fi
