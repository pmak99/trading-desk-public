-- Bring production's schema up to 2.0/sql/baseline_schema.sql.
--
-- Production's schema was created in Task 1. Task 4 then added TYPE_OVERRIDES
-- to 2.0/scripts/generate_pg_ddl.py -- the cases where leaving a column as
-- text causes a correctness bug, not merely an untidy type -- and regenerated
-- baseline_schema.sql. Production was never rebuilt from the regenerated
-- baseline, so it drifted by exactly these eight columns.
--
-- Found during the Task 5 cutover by a cross-engine value check, not by the
-- loader's own verification: the loader compares counts, id ranges, referential
-- integrity and one timezone probe, and all of those passed against the wrong
-- types. What caught it was digesting text columns value-by-value.
--
-- The eight are the entire difference. Verified before writing this file:
--   * information_schema.columns diff against a scratch database built from
--     baseline_schema.sql lists these eight rows and nothing else;
--   * pg_indexes definitions hash identically     (md5 91973bbc0a25731023ca573039949ae0);
--   * pg_constraint definitions hash identically  (md5 f5a65c0ad3efa5a827f6d1bb48a7d282).
-- That is why this is an ALTER and not a DROP SCHEMA + reload: with the diff
-- proven complete, the two reach the same end state and this one does not
-- destroy 47,560 freshly loaded rows to get there.
--
-- Why each column matters, so nobody "simplifies" these back to text:
--
--   job_status.date / updated_at   The once-per-day claim in
--                                  scripts/jobs/runner.py is an
--                                  ON CONFLICT (date, job_name) DO UPDATE
--                                  guarded by
--                                  updated_at < now() - make_interval(...).
--                                  Against text, the conflict target compares
--                                  a Python date to a string and the staleness
--                                  guard has no interval arithmetic at all.
--                                  Both tables are empty, so this is cheap now
--                                  and load-bearing on Monday.
--
--   sentiment_cache.cached_at      The 3-hour TTL. Text cannot be subtracted
--                   .date          from now(); this is the same naive-vs-aware
--                                  defect already fixed in earnings_fetcher,
--                                  which raised TypeError on every cached read.
--
--   sentiment_history.collected_at UTC is the measured convention here, not an
--                    .updated_at   assumption -- Task 1's probe established it,
--                    .earnings_date and the original plan had it wrong.
--
--   strategies.quantity            numeric, because it derives from
--                                  trade_journal.quantity via
--                                  _quantity_from_legs(), and that column
--                                  stores 9 fractional values today despite
--                                  being declared INTEGER. Zero strategies rows
--                                  are fractional right now, so bigint has lost
--                                  nothing yet -- it is a latent truncation
--                                  waiting for the next regroup, which is
--                                  exactly the class of silent narrowing this
--                                  migration exists to end.
--
-- Every value carries an explicit offset already, so each cast is lossless and
-- unambiguous. One transaction: all eight, or none.

BEGIN;

ALTER TABLE job_status
    ALTER COLUMN date       TYPE date        USING date::date,
    ALTER COLUMN updated_at TYPE timestamptz USING updated_at::timestamptz;

ALTER TABLE sentiment_cache
    ALTER COLUMN cached_at  TYPE timestamptz USING cached_at::timestamptz,
    ALTER COLUMN date       TYPE date        USING date::date;

ALTER TABLE sentiment_history
    ALTER COLUMN collected_at  TYPE timestamptz USING collected_at::timestamptz,
    ALTER COLUMN earnings_date TYPE date        USING earnings_date::date,
    ALTER COLUMN updated_at    TYPE timestamptz USING updated_at::timestamptz;

ALTER TABLE strategies
    ALTER COLUMN quantity TYPE numeric USING quantity::numeric;

COMMIT;
