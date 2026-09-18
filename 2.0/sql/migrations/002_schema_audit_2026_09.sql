-- Migration 2: schema audit fixes (2026-09-13).
--
-- Applied by MigrationManager, all statements in ONE transaction: every change
-- below lands, or none does. Statements are split on semicolons, so no
-- semicolon may appear inside a statement or a comment.
--
-- Every conversion that could lose information is preceded by a temporary
-- CHECK that proves it will not. If production has drifted from what was
-- measured on 2026-09-13, the guard fails, the transaction rolls back, and
-- nothing is changed. The guards are dropped again immediately.

-- ---------------------------------------------------------------------------
-- 1. Redundant indexes
-- ---------------------------------------------------------------------------
-- Three exact duplicates on trade_journal, from two naming generations.
DROP INDEX idx_journal_earnings;
DROP INDEX idx_journal_sale_date;
DROP INDEX idx_journal_symbol;
-- Leading-column prefixes of a unique index that already serves the lookup.
DROP INDEX idx_trade_journal_symbol;
DROP INDEX idx_earnings_ticker;
DROP INDEX idx_moves_ticker;
DROP INDEX idx_moves_ticker_date;
DROP INDEX idx_bias_ticker;
DROP INDEX idx_job_status_date;
-- The dominant analysis_log read is "this ticker, newest first". The old plan
-- walked the timestamp index backwards and filtered by ticker.
DROP INDEX idx_analysis_ticker;
CREATE INDEX idx_analysis_ticker_timestamp ON analysis_log (ticker, "timestamp" DESC);

-- ---------------------------------------------------------------------------
-- 2. analysis_log: six columns no writer ever filled
-- ---------------------------------------------------------------------------
-- 0 of 1,304 rows non-NULL. The guard makes "verified empty" part of the
-- migration rather than a claim about it. Dropping the columns drops the guard
-- and idx_analysis_strategy with them.
ALTER TABLE analysis_log ADD CONSTRAINT migration2_dead_columns_empty
    CHECK (strategy_type IS NULL AND strategy_score IS NULL AND strategy_pop IS NULL
           AND strategy_rr IS NULL AND contracts IS NULL AND confidence IS NULL);
ALTER TABLE analysis_log
    DROP COLUMN strategy_type,
    DROP COLUMN strategy_score,
    DROP COLUMN strategy_pop,
    DROP COLUMN strategy_rr,
    DROP COLUMN contracts,
    DROP COLUMN confidence;

-- ---------------------------------------------------------------------------
-- 3. Booleans stored as bigint
-- ---------------------------------------------------------------------------
ALTER TABLE sentiment_history ADD CONSTRAINT migration2_bool_guard
    CHECK (prediction_correct IN (0, 1));
ALTER TABLE sentiment_history DROP CONSTRAINT migration2_bool_guard;
ALTER TABLE sentiment_history
    ALTER COLUMN prediction_correct TYPE boolean USING (prediction_correct <> 0);

ALTER TABLE bias_predictions ADD CONSTRAINT migration2_bool_guard
    CHECK (compound_risk_active IN (0, 1) AND sizing_alarm IN (0, 1));
ALTER TABLE bias_predictions DROP CONSTRAINT migration2_bool_guard;
ALTER TABLE bias_predictions
    ALTER COLUMN compound_risk_active TYPE boolean USING (compound_risk_active <> 0),
    ALTER COLUMN sizing_alarm TYPE boolean USING (sizing_alarm <> 0);

-- ---------------------------------------------------------------------------
-- 4. TACO: text dates, a float contract count, an integer flag, float dollars
-- ---------------------------------------------------------------------------
-- The two naive timestamps were written by datetime.now() on the Mac, whose
-- zone is America/New_York (generate_pg_ddl.py's affinity survey recorded the
-- same). They are localised here, once, rather than parsed in the session zone.
ALTER TABLE taco_log
    ALTER COLUMN "timestamp" TYPE timestamptz
        USING ("timestamp"::timestamp AT TIME ZONE 'America/New_York'),
    ALTER COLUMN event_date TYPE date USING event_date::date;

ALTER TABLE taco_positions ADD CONSTRAINT migration2_taco_guard
    CHECK (contracts = trunc(contracts) AND rule_compliant IN (0, 1)
           AND round(premium_paid::numeric, 6) = round(premium_paid::numeric, 2)
           AND round(proceeds::numeric, 6) = round(proceeds::numeric, 2)
           AND round(outcome_pnl::numeric, 6) = round(outcome_pnl::numeric, 2));
ALTER TABLE taco_positions DROP CONSTRAINT migration2_taco_guard;
ALTER TABLE taco_positions
    ALTER COLUMN contracts TYPE bigint USING contracts::bigint,
    ALTER COLUMN expiration TYPE date USING expiration::date,
    ALTER COLUMN entry_date TYPE date USING entry_date::date,
    ALTER COLUMN event_date TYPE date USING event_date::date,
    ALTER COLUMN exit_date TYPE date USING exit_date::date,
    ALTER COLUMN created_at TYPE timestamptz
        USING (created_at::timestamp AT TIME ZONE 'America/New_York'),
    ALTER COLUMN rule_compliant DROP DEFAULT,
    ALTER COLUMN rule_compliant TYPE boolean USING (rule_compliant <> 0),
    ALTER COLUMN rule_compliant SET DEFAULT true,
    ALTER COLUMN premium_paid TYPE numeric(14,2) USING round(premium_paid::numeric, 2),
    ALTER COLUMN proceeds TYPE numeric(14,2) USING round(proceeds::numeric, 2),
    ALTER COLUMN outcome_pnl TYPE numeric(14,2) USING round(outcome_pnl::numeric, 2);

-- ---------------------------------------------------------------------------
-- 5. Remaining text dates
-- ---------------------------------------------------------------------------
ALTER TABLE iv_history ALTER COLUMN expiration TYPE date USING expiration::date;
-- MigrationManager now writes now(), not now()::text.
ALTER TABLE schema_migrations
    ALTER COLUMN applied_at TYPE timestamptz USING applied_at::timestamptz;

-- ---------------------------------------------------------------------------
-- 6. Money as numeric(14,2)
-- ---------------------------------------------------------------------------
-- The guards prove every value is already a whole number of cents at six
-- decimal places, so the round() below only removes binary floating-point
-- residue (583484.8700000001), never a real cent.
ALTER TABLE strategies ADD CONSTRAINT migration2_money_guard
    CHECK (round(gain_loss::numeric, 6) = round(gain_loss::numeric, 2)
           AND round(net_credit::numeric, 6) = round(net_credit::numeric, 2)
           AND round(net_debit::numeric, 6) = round(net_debit::numeric, 2));
ALTER TABLE strategies DROP CONSTRAINT migration2_money_guard;
ALTER TABLE strategies
    ALTER COLUMN gain_loss TYPE numeric(14,2) USING round(gain_loss::numeric, 2),
    ALTER COLUMN net_credit TYPE numeric(14,2) USING round(net_credit::numeric, 2),
    ALTER COLUMN net_debit TYPE numeric(14,2) USING round(net_debit::numeric, 2);

-- The journal's dedup guard. It was enforced twice -- a plain UNIQUE that lets
-- NULL option_type rows (stock legs) through, and a COALESCE expression index
-- that does not -- and the importer named the index in ON CONFLICT ON
-- CONSTRAINT, which accepts only constraints, so every import row failed.
-- One constraint replaces both: NULLS NOT DISTINCT makes NULL equal NULL, the
-- COALESCE index's behaviour (no row holds '' or 0, so nothing changes).
ALTER TABLE trade_journal
    DROP CONSTRAINT trade_journal_symbol_acquired_date_sale_date_option_type_st_key;
DROP INDEX idx_trade_journal_dedup;

ALTER TABLE trade_journal ADD CONSTRAINT migration2_money_guard
    CHECK (round(cost_basis::numeric, 6) = round(cost_basis::numeric, 2)
           AND round(proceeds::numeric, 6) = round(proceeds::numeric, 2)
           AND round(gain_loss::numeric, 6) = round(gain_loss::numeric, 2)
           AND round(wash_sale_amount::numeric, 6) = round(wash_sale_amount::numeric, 2));
ALTER TABLE trade_journal DROP CONSTRAINT migration2_money_guard;
ALTER TABLE trade_journal
    ALTER COLUMN cost_basis TYPE numeric(14,2) USING round(cost_basis::numeric, 2),
    ALTER COLUMN proceeds TYPE numeric(14,2) USING round(proceeds::numeric, 2),
    ALTER COLUMN gain_loss TYPE numeric(14,2) USING round(gain_loss::numeric, 2),
    ALTER COLUMN wash_sale_amount TYPE numeric(14,2) USING round(wash_sale_amount::numeric, 2);

ALTER TABLE trade_journal ADD CONSTRAINT trade_journal_dedup
    UNIQUE NULLS NOT DISTINCT
    (symbol, acquired_date, sale_date, option_type, strike, cost_basis, account_type);

-- ---------------------------------------------------------------------------
-- 7. Referential integrity
-- ---------------------------------------------------------------------------
-- 0 orphans today. regroup_strategies.py re-points children before deleting.
ALTER TABLE strategies ADD CONSTRAINT strategies_parent_strategy_id_fkey
    FOREIGN KEY (parent_strategy_id) REFERENCES strategies (id);

-- ---------------------------------------------------------------------------
-- 8. Closed vocabularies
-- ---------------------------------------------------------------------------
-- Each list is the complete set the code can write, traced to its enum or
-- literal (2026-09-13) and asserted against those enums by
-- scripts/tests/test_baseline_schema.py, so adding an enum member without
-- widening its CHECK fails a test instead of a write. NULL passes a CHECK.
-- Deliberately NOT constrained: sentiment_history.sentiment_direction (an LLM
-- fills it, and /council writes skew levels), and the free-text columns.
ALTER TABLE analysis_log
    ADD CONSTRAINT analysis_log_recommendation_check
        CHECK (recommendation IN ('excellent', 'good', 'marginal', 'skip')),
    ADD CONSTRAINT analysis_log_vix_regime_check
        CHECK (vix_regime IN ('very_low', 'low', 'normal', 'normal_high',
                              'elevated', 'elevated_high', 'high', 'extreme')),
    ADD CONSTRAINT analysis_log_source_check
        CHECK (source IN ('analyze', 'scan', 'whisper', 'ticker'));

ALTER TABLE bias_predictions
    ADD CONSTRAINT bias_predictions_directional_bias_check
        CHECK (directional_bias IN ('strong_bearish', 'bearish', 'weak_bearish', 'neutral',
                                    'weak_bullish', 'bullish', 'strong_bullish')),
    ADD CONSTRAINT bias_predictions_fused_bias_check
        CHECK (fused_bias IN ('strong_bearish', 'bearish', 'weak_bearish', 'neutral',
                              'weak_bullish', 'bullish', 'strong_bullish')),
    ADD CONSTRAINT bias_predictions_skew_strength_check
        CHECK (skew_strength IN ('smile', 'inverse_smile', 'smirk')),
    ADD CONSTRAINT bias_predictions_trr_level_check
        CHECK (trr_level IN ('LOW', 'NORMAL', 'HIGH')),
    ADD CONSTRAINT bias_predictions_actual_direction_check
        CHECK (actual_direction IN ('UP', 'DOWN', 'FLAT'));

ALTER TABLE sentiment_history
    ADD CONSTRAINT sentiment_history_actual_direction_check
        CHECK (actual_direction IN ('UP', 'DOWN', 'FLAT')),
    ADD CONSTRAINT sentiment_history_trade_outcome_check
        CHECK (trade_outcome IN ('WIN', 'LOSS', 'SKIP'));

ALTER TABLE job_status
    ADD CONSTRAINT job_status_status_check
        CHECK (status IN ('running', 'success', 'error'));

ALTER TABLE taco_positions
    ADD CONSTRAINT taco_positions_status_check CHECK (status IN ('OPEN', 'CLOSED')),
    ADD CONSTRAINT taco_positions_direction_check CHECK (direction IN ('CALL', 'PUT'));

ALTER TABLE taco_log
    ADD CONSTRAINT taco_log_mode_check CHECK (mode IN ('check', 'positions')),
    ADD CONSTRAINT taco_log_tier_check CHECK (tier IN ('FULL', 'HALF', 'PILOT', 'SKIP')),
    ADD CONSTRAINT taco_log_direction_check CHECK (direction IN ('CALL', 'PUT'));

ALTER TABLE strategies
    ADD CONSTRAINT strategies_account_type_check CHECK (account_type IN ('TAXABLE', 'IRA'));

ALTER TABLE trade_journal
    ADD CONSTRAINT trade_journal_account_type_check CHECK (account_type IN ('TAXABLE', 'IRA')),
    ADD CONSTRAINT trade_journal_option_type_check CHECK (option_type IN ('CALL', 'PUT')),
    ADD CONSTRAINT trade_journal_term_check CHECK (term IN ('SHORT', 'LONG'));

-- ---------------------------------------------------------------------------
-- 9. historical_moves: the percentages derive from the prices, by definition
-- ---------------------------------------------------------------------------
-- CLAUDE.md's stored-vs-prices invariant becomes a property of the schema. A
-- writer can no longer store a percentage its own prices contradict -- the
-- class of bug that put 1,757 abs() values into this table in Nov-Dec 2025.
-- Recomputing changes no value a writer stored correctly (the formulas are the
-- writers' own, bit for bit). It does correct the near-zero rows whose SIGN the
-- Sep 8 repair missed, because |2x| fell under its 0.02 tolerance.
ALTER TABLE historical_moves
    ADD CONSTRAINT historical_moves_prices_positive
        CHECK (prev_close > 0 AND earnings_open > 0),
    DROP COLUMN intraday_move_pct,
    DROP COLUMN gap_move_pct,
    DROP COLUMN close_move_pct;
ALTER TABLE historical_moves
    ADD COLUMN intraday_move_pct double precision NOT NULL
        GENERATED ALWAYS AS ((earnings_close - earnings_open) / earnings_open * 100) STORED,
    ADD COLUMN gap_move_pct double precision NOT NULL
        GENERATED ALWAYS AS ((earnings_open - prev_close) / prev_close * 100) STORED,
    ADD COLUMN close_move_pct double precision NOT NULL
        GENERATED ALWAYS AS ((earnings_close - prev_close) / prev_close * 100) STORED;

-- ---------------------------------------------------------------------------
-- 10. earnings_calendar: created_at later than updated_at on 16,582 rows
-- ---------------------------------------------------------------------------
-- A SQLite-era writer stored an upstream fetch time (08:00) as updated_at, and
-- the load stamped created_at later. created_at has no reader. Moving it back
-- restores created <= updated without touching updated_at, which the calendar
-- fetcher uses as a "last checked" fallback -- making that look newer would be
-- the unsafe direction.
UPDATE earnings_calendar SET created_at = updated_at WHERE updated_at < created_at;
