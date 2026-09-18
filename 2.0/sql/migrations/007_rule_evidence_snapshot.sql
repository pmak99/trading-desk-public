-- Migration 7: rule_evidence_snapshot, the canonical band tallies (2026-09-17).
--
-- Applied by MigrationManager, all statements in ONE transaction. Statements
-- are split on semicolons, so no semicolon may appear inside a statement or a
-- comment.
--
-- Four dashboard panels report the band tables CLAUDE.md prints, and
-- 2.0/scripts/rule_evidence.py already computes all four. Two of them cannot
-- be expressed in SQL without copying domain logic: exit timing needs
-- is_trading_day (src/domain/market_calendar.py) and TRR needs tail_risk.py's
-- ladder, which trading_trr_at_entry duplicated as SQL literals while
-- filtering away the 65 of 300 positions whose TRR is UNKNOWN. This is the
-- same argument risk_metrics_snapshot already answers for Sharpe.
--
-- Two cohorts per dimension where the dashboard needs both. `earnings` is
-- rule_evidence's own scope and reproduces CLAUDE.md sections 3, 5, 6 and 8
-- exactly. `all` is the wider population trading_trr_at_entry and
-- trading_contract_buckets have always shown and say so in their descriptions.
--
-- band_order exists because band labels do not sort: lexicographically
-- 1-50 precedes 101-200 precedes 200+ precedes 51-100. It is the index of the
-- band in rule_evidence.py's own tuple.
--
-- win_rate is deliberately NOT stored. It is 100.0 * wins / trades in the
-- panel, because a stored derived value drifts from the columns it came from.
CREATE TABLE "rule_evidence_snapshot" (
    "dimension"       text NOT NULL,
    "cohort"          text NOT NULL,
    "band"            text NOT NULL,
    "band_order"      int NOT NULL,
    "trades"          int NOT NULL,
    "wins"            int NOT NULL,
    "pnl"             numeric(14,2),
    "avg_loss"        numeric(14,2),
    "median_quantity" double precision,
    "computed_at"     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY ("dimension", "cohort", "band")
);
