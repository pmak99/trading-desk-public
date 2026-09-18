#!/usr/bin/env bash
# Unattended A/B forward-validation run for the quarterly cloud routine.
#
# WHY THIS EXISTS AS A SCRIPT RATHER THAN STEPS IN THE ROUTINE PROMPT:
# the routine runs on the the server bridge host, where every Bash call hits the
# permission prompt -- and an unattended routine can never answer one. The
# first bridge run (2026-09-05) stalled at worker_status=requires_action on a
# `git pull`. Allowlisting the individual commands is brittle: the agent varies
# the exact string (pipes, `~` vs absolute paths, redirections) and prefix
# matching then misses. Collapsing everything into one script means the host
# needs exactly ONE permission rule:
#
#   "Bash(bash /home/trading/trading-desk/2.0/scripts/run_ab_eval_remote.sh)"
#
# That rule is why this file survived the Postgres cutover unchanged in NAME
# while being rewritten inside: renaming or splitting it would silently break
# the routine, which cannot answer the resulting prompt.
#
# It also puts the logic under version control and code review instead of in
# prompt prose that can drift.
#
# Read-only by design: it pulls (--ff-only) and runs SELECTs. There is no
# longer a database copy to fetch or shred -- see below.
set -uo pipefail

REPO="${REPO:-$HOME/trading-desk}"
ENV_FILE="$REPO/2.0/.env"
PY="$REPO/2.0/venv/bin/python"

echo "=== 1/2  Refresh checkout ==="
_before="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"
if git -C "$REPO" pull --ff-only 2>&1 | tail -3; then
    echo "checkout: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
else
    echo "WARNING: pull failed; running against the existing checkout."
    echo "The criteria may be stale -- say so in the report."
fi
_after="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo unknown)"

# ⚠️ This script pulls the repository that CONTAINS it, so the pull can rewrite
# the file that is currently executing. Bash reads a script lazily, by byte
# offset, so continuing after a rewrite runs the OLD logic at best and a
# garbled fragment at worst. Observed for real on 2026-09-12: the first run
# after the Postgres cutover pulled the new version and then carried on with
# the pre-cutover code, dutifully trying to fetch the database from a GCS
# bucket that no longer answers.
#
# So: if the pull moved HEAD, exec the new version once. AB_EVAL_REEXEC stops
# it looping if the working tree is somehow always dirty.
if [ "$_before" != "$_after" ] && [ -z "${AB_EVAL_REEXEC:-}" ]; then
    echo "(checkout moved $_before -> $_after; re-executing the updated script)"
    export AB_EVAL_REEXEC=1
    exec bash "$REPO/2.0/scripts/run_ab_eval_remote.sh" "$@"
fi

echo
echo "=== 2/2  Evaluate (Postgres, via the venv) ==="
# ⚠️ The venv is REQUIRED. The evaluator used to be stdlib-only (sqlite3), so
# a bare python3 sufficed; it reads Postgres through psycopg now and a bare
# python3 dies on the import.
if [ ! -x "$PY" ]; then
    echo "FATAL: no venv interpreter at $PY."
    echo "Do NOT estimate or reconstruct results. Report this failure and"
    echo "recommend running /ab-status locally instead."
    exit 1
fi
if [ ! -r "$ENV_FILE" ]; then
    echo "FATAL: cannot read $ENV_FILE (PG* credentials live there)."
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

"$PY" "$REPO/2.0/scripts/ab_evaluate_live.py"
rc=$?

cat <<'CAVEAT'

--- provenance ---
This read the live Postgres database directly. There is one database now, so
the event count here is the same number a local /ab-status reports -- no sync,
no lag, no reconciliation.

That was NOT true before 2026-09-12. This script used to fetch a copy of
ivcrush.db from GCS, and that copy under-reported: 209 outcome-known events
against local's 232 on 2026-09-05, while being NEWER by object timestamp --
which looked like a contradiction but was not, because a sync rewrote the
object (updating its mtime) while analysis_log itself was never merged into
it. If you are comparing against a historical run, that is why its numbers
were lower.

Two consequences worth knowing. The GCS bucket is now unreachable anyway (the
project's billing account is unlinked), so the old path could not run even if
it were still wired up. And this host's access is a normal read: nothing here
can write, but that is now a property of what the script does rather than of
a read-only GCS credential.
CAVEAT

echo
echo "=== done (evaluator exit $rc) ==="
exit "$rc"
