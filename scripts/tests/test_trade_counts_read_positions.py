"""A trade count, win rate or average in command or panel SQL reads `positions`.

`strategies` holds a row per account: the same trade placed in the IRA and in
TAXABLE is two rows, so every count and win rate over the table counted it
twice -- ten pairs on 2026-09-15, when migration 5 added the `positions` view
(one row per trade). The slash commands and the Grafana panels are SQL that
nothing imports, so this scans them.

A SELECT that aggregates with COUNT or AVG fails when it reads `strategies` --
directly, through a CTE, or through a subquery in its FROM -- unless it is one of
the reviewed row counts below. A sum is the same over either relation and is not
flagged; per-account P&L (split by account_type) stays on `strategies`, where a
merged position's account 'IRA+TAXABLE' cannot be split. A count over a scalar
subquery that itself reads `strategies` is caught; a count outside the SELECT
(a shell `wc -l` of exported rows) is not.
"""

import re
from pathlib import Path

import pytest

from scripts.dashboard_queries import load_queries

REPO = Path(__file__).resolve().parents[2]
COMMANDS = REPO / ".claude" / "commands"
QUERIES = REPO / "2.0" / "dashboards" / "queries.sql"

# location -> (text in the flagged SELECT, why it counts strategies rows rather than trades)
ROW_COUNTS = {
    ".claude/commands/backfill.md": (
        "num_positions",
        "only zero vs non-zero is read, on an id chain built from strategies' parent and "
        "campaign links"),
}

READS = re.compile(r"\b(?:FROM|JOIN)\s+(\w+)", re.I)
# Upper case: the command files write SQL keywords that way, and their prose says
# "derive the outcome from strategies".
READS_STRATEGIES = re.compile(r"\b(?:FROM|JOIN)\s+strategies\b")
AGGREGATE = re.compile(r"\b(?:COUNT|AVG)\s*\(", re.I)
# `-- note` is a comment; `--csv` is a psql flag.
COMMENT = re.compile(r"(?:^|(?<=\s))--(?:\s.*)?$", re.M)
DB_CALL = re.compile(r'scripts/db\s[^"\n]*(?:\\\n\s*)?"((?:[^"\\]|\\.)*)"', re.S)
CHILD = re.compile(r"\(§(\d+)\)")


def _scopes(sql):
    """Every parenthesised span of `sql`, innermost first and the whole statement last,
    each with its nested spans as `(§n)` tokens; and `full`, which expands the tokens."""
    stack, spans = [[]], []
    for ch in sql:
        if ch == "(":
            stack.append([])
        elif ch == ")" and len(stack) > 1:
            spans.append("".join(stack.pop()))
            stack[-1].append(f"(§{len(spans) - 1})")
        else:
            stack[-1].append(ch)
    while len(stack) > 1:       # unbalanced: close what is open
        spans.append("".join(stack.pop()))
        stack[-1].append(f"(§{len(spans) - 1})")
    spans.append("".join(stack[0]))

    def full(text):
        return CHILD.sub(lambda m: "(" + full(spans[int(m.group(1))]) + ")", text)

    return spans, full


def counts_over_strategies(sql):
    """The SELECTs in `sql` that count or average over `strategies`, whitespace-collapsed.
    Each branch of a UNION is its own SELECT."""
    spans, full = _scopes(COMMENT.sub("", sql))
    ctes = {}
    for own in spans:
        for m in re.finditer(r"\b(\w+)\s+AS\s+(?:NOT\s+)?(?:MATERIALIZED\s+)?\(§(\d+)\)", own, re.I):
            ctes[m.group(1).lower()] = full(f"(§{m.group(2)})")
    derived = {"strategies"}
    while True:
        more = {name for name, body in ctes.items()
                if name not in derived and any(r.lower() in derived for r in READS.findall(body))}
        if not more:
            break
        derived |= more

    def reads_strategies(text):
        return any(r.lower() in derived for r in READS.findall(text))

    hits = []
    for own in spans:
        for branch in re.split(r"\b(?:UNION|INTERSECT|EXCEPT)\b", own, flags=re.I):
            if not AGGREGATE.search(branch):
                continue
            subqueries = [full(f"(§{n})") for n in
                          re.findall(r"\b(?:FROM|JOIN)\s+\(§(\d+)\)", branch, re.I)]
            if reads_strategies(branch) or any(reads_strategies(s) for s in subqueries):
                hits.append(" ".join(full(branch).split()))
    return hits


def command_statements(text):
    """Every SQL statement of a command file: `scripts/db` strings, ```sql fences and
    inline `SELECT ...` spans, split on semicolons."""
    blocks = [m.group(1).replace('\\"', '"') for m in DB_CALL.finditer(text)]
    blocks += re.findall(r"```sql\n(.*?)```", text, re.S)
    blocks += re.findall(r"`(SELECT [^`]+)`", re.sub(r"```.*?```", "", text, flags=re.S))
    return [s for b in blocks for s in b.split(";") if s.strip()]


def _sources():
    for path in sorted(COMMANDS.glob("*.md")):
        rel = str(path.relative_to(REPO))
        for sql in command_statements(path.read_text()):
            yield rel, sql
    for name, sql in load_queries(QUERIES).items():
        yield f"{QUERIES.relative_to(REPO)}#{name}", sql


def test_the_scan_catches_a_win_rate_over_strategies():
    sql = ("SELECT symbol, COUNT(*) AS trades,"
           " ROUND((100.0 * COUNT(*) FILTER (WHERE is_winner) / COUNT(*))::numeric, 1)"
           " FROM strategies GROUP BY symbol")
    assert counts_over_strategies(sql)
    assert counts_over_strategies(sql.replace("strategies", "positions")) == []


def test_the_scan_catches_a_share_whose_denominator_reads_strategies():
    """/risk's shape: the numerator over one relation, the denominator over the other."""
    sql = ("SELECT strategy_type, COUNT(*) AS trades,"
           " ROUND((100.0 * COUNT(*) / (SELECT COUNT(*) FROM strategies"
           " WHERE sale_date >= CURRENT_DATE - 90))::numeric, 1) AS pct"
           " FROM positions GROUP BY strategy_type")
    assert counts_over_strategies(sql) == [
        "SELECT COUNT(*) FROM strategies WHERE sale_date >= CURRENT_DATE - 90"]


def test_the_scan_follows_ctes_and_subqueries_in_from():
    """/taco's shape counts over a CTE; the Grafana accuracy panels over a join to one."""
    cte = ("WITH t AS (SELECT s.gain_loss, (SELECT string_agg(j.option_type, '/')"
           " FROM trade_journal j WHERE j.strategy_id = s.id) AS side FROM strategies s),"
           " u AS (SELECT * FROM t)"
           " SELECT side, count(*) AS trades FROM u GROUP BY side")
    assert counts_over_strategies(cte)
    subquery = "SELECT count(*) FROM (SELECT DISTINCT id FROM strategies) x"
    assert counts_over_strategies(subquery)


def test_the_scan_ignores_sums_symbol_lists_and_comments():
    ok = [
        # /maintenance: a count over historical_moves UNIONed with a strategies symbol list
        "WITH sparse AS (SELECT ticker FROM historical_moves GROUP BY ticker HAVING COUNT(*) < 5"
        " UNION SELECT symbol FROM (SELECT symbol FROM strategies UNION"
        " SELECT symbol FROM trade_journal) t WHERE symbol NOT IN (SELECT ticker FROM historical_moves))"
        " SELECT e.ticker FROM earnings_calendar e JOIN sparse s ON s.ticker = e.ticker",
        # per-account P&L
        "SELECT to_char(sale_date, 'YYYY-MM') AS month,"
        " SUM(CASE WHEN account_type = 'IRA' THEN gain_loss ELSE 0 END) AS ira FROM strategies GROUP BY 1",
        # a comment is prose
        "-- COUNT(*) FROM strategies counted mirrored trades twice\nSELECT COUNT(*) FROM positions",
        # an id chain with no aggregate
        "WITH direct AS (SELECT id FROM strategies WHERE symbol = 'MU')"
        " SELECT s.* FROM strategies s JOIN direct d ON d.id = s.id",
    ]
    assert [counts_over_strategies(sql) for sql in ok] == [[], [], [], []]
    assert command_statements('scripts/db --csv -c \\\n  "SELECT COUNT(*) FROM strategies;"') == [
        "SELECT COUNT(*) FROM strategies"]


def test_the_extractor_sees_every_strategies_read_in_the_command_files():
    """A read the extractor misses (SQL in a python block, a new quoting shape) would pass
    the scan unseen."""
    missed = []
    for path in sorted(COMMANDS.glob("*.md")):
        text = path.read_text()
        seen = sum(len(READS_STRATEGIES.findall(s)) for s in command_statements(text))
        if seen != len(READS_STRATEGIES.findall(text)):
            missed.append(f"{path.name}: {len(READS_STRATEGIES.findall(text))} in the file, {seen} extracted")
    assert missed == [], "\n".join(missed)


@pytest.mark.skipif(not COMMANDS.is_dir(),
                    reason="the operator's .claude/commands/ definitions are not part of this repository")
def test_no_command_or_panel_counts_trades_over_strategies():
    hits, used = [], set()
    for location, sql in _sources():
        for select in counts_over_strategies(sql):
            allowed = ROW_COUNTS.get(location)
            if allowed and allowed[0] in select:
                used.add(location)
                continue
            hits.append(f"{location}: {select[:160]}")
    assert hits == [], (
        "count trades over `positions` (one row per trade), not `strategies` (a row per "
        "account):\n" + "\n".join(hits))
    assert used == set(ROW_COUNTS), f"stale ROW_COUNTS entries: {sorted(set(ROW_COUNTS) - used)}"
