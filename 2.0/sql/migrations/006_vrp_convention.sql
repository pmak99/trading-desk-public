-- Migration 6: analysis_log and sentiment_history name their VRP convention (2026-09-16).
--
-- Applied by MigrationManager, all statements in ONE transaction. Statements
-- are split on semicolons, so no semicolon may appear inside a statement or a
-- comment.
--
-- The live VRP denominator moves from the mean open-to-close move (intraday) to
-- the mean close-to-close move (docs/superpowers/plans/2026-09-16-vrp-close-denominator.md).
-- analysis_log.vrp_ratio and historical_mean_pct held the intraday figures and would
-- have silently changed units under the same names, so they are renamed to say so,
-- and a reader that missed the change fails on a missing column instead. Both
-- become nullable: after the switch the intraday figure is a reference that can be
-- absent. vrp_convention records which convention graded recommendation, and a
-- CHECK holds that convention's ratio present. Every existing row was graded on
-- intraday. sentiment_history.vrp_ratio stores whatever VRP the whisper table
-- showed, so it gains the same marker, present exactly when the ratio is.

ALTER TABLE analysis_log RENAME COLUMN vrp_ratio TO vrp_intraday_ratio;
ALTER TABLE analysis_log RENAME COLUMN historical_mean_pct TO historical_intraday_mean_pct;
ALTER TABLE analysis_log ALTER COLUMN vrp_intraday_ratio DROP NOT NULL;
ALTER TABLE analysis_log ALTER COLUMN historical_intraday_mean_pct DROP NOT NULL;
ALTER TABLE analysis_log ADD COLUMN vrp_convention text NOT NULL DEFAULT 'intraday';
ALTER TABLE analysis_log ALTER COLUMN vrp_convention DROP DEFAULT;
ALTER TABLE analysis_log ADD CONSTRAINT analysis_log_vrp_convention_check
    CHECK (vrp_convention IN ('intraday', 'close'));
ALTER TABLE analysis_log ADD CONSTRAINT analysis_log_graded_ratio_present
    CHECK ((vrp_convention = 'intraday' AND vrp_intraday_ratio IS NOT NULL)
        OR (vrp_convention = 'close' AND vrp_close_ratio IS NOT NULL));
ALTER TABLE sentiment_history ADD COLUMN vrp_convention text;
UPDATE sentiment_history SET vrp_convention = 'intraday' WHERE vrp_ratio IS NOT NULL;
ALTER TABLE sentiment_history ADD CONSTRAINT sentiment_history_vrp_convention_check
    CHECK (vrp_convention IN ('intraday', 'close'));
ALTER TABLE sentiment_history ADD CONSTRAINT sentiment_history_vrp_convention_with_ratio
    CHECK ((vrp_ratio IS NULL) = (vrp_convention IS NULL));
