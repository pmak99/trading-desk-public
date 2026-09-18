-- Migration 3: bias_predictions' three dead columns (2026-09-14).
--
-- Applied by MigrationManager, all statements in ONE transaction. Statements
-- are split on semicolons, so no semicolon may appear inside a statement or a
-- comment.
--
-- vrp_ratio, implied_move_pct and historical_mean_pct were never written. The
-- one writer, 2.0/scripts/store_bias_prediction.py, bound NULL for all three:
-- the lookup meant to fill them called a method VRPCalculator does not have.
-- Nothing read them. The same analysis logs its implied move and VRP to
-- analysis_log. 0 of 123 rows non-NULL on 2026-09-14.
--
-- The guard makes "verified empty" part of the migration: if any row holds a
-- value by the time this runs, the CHECK fails, the transaction rolls back and
-- nothing is changed. Dropping the columns drops the guard with them.
--
-- Deliberately not dropped: sentiment_history.size_modifier. Nothing reads or
-- writes it, but 17 rows from Dec 2025 - Jan 2026 hold 0.8 or 1.2 rather than
-- its 1.0 default.

ALTER TABLE bias_predictions ADD CONSTRAINT migration3_dead_columns_empty
    CHECK (vrp_ratio IS NULL AND implied_move_pct IS NULL AND historical_mean_pct IS NULL);
ALTER TABLE bias_predictions
    DROP COLUMN vrp_ratio,
    DROP COLUMN implied_move_pct,
    DROP COLUMN historical_mean_pct;
