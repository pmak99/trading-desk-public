-- Migration 4: sentiment_history.size_modifier (2026-09-14).
--
-- Applied by MigrationManager, all statements in ONE transaction. Statements
-- are split on semicolons, so no semicolon may appear inside a statement or a
-- comment.
--
-- The column held the contrarian sizing's multiplier: 0.8 for a sentiment score
-- of 0.6 or more, 1.2 for -0.6 or less, 1.0 otherwise. The 4.0 sentiment store
-- added it on 2026-02-03 and backfilled the rows already collected. Nothing has
-- written it since and nothing reads it. The 14 rows collected from 2026-02-03
-- on whose score meets the rule hold 1.0, and the sizing itself was removed
-- 2026-09-14 (CLAUDE.md section 7).
--
-- Migration 3 kept it for the 17 rows that hold 0.8 or 1.2, collected
-- 2025-12-08 to 2026-01-30. Each is the rule applied to its row's own
-- sentiment_score, which stays. The guard is their record, as
-- (ticker, earnings_date, size_modifier), and makes the record part of the
-- migration: if any row holds a value other than 1.0 that it does not name, the
-- CHECK fails, the transaction rolls back and nothing is changed. Dropping the
-- column drops the guard with it.

ALTER TABLE sentiment_history ADD CONSTRAINT migration4_size_modifier_recorded
    CHECK (size_modifier = 1.0 OR (ticker, earnings_date, size_modifier) IN (
        ('ADBE', '2025-12-10', 0.8),
        ('ORCL', '2025-12-10', 0.8),
        ('AVGO', '2025-12-11', 0.8),
        ('LULU', '2025-12-11', 1.2),
        ('LEN', '2025-12-16', 1.2),
        ('GIS', '2025-12-17', 1.2),
        ('JBL', '2025-12-17', 0.8),
        ('ACN', '2025-12-18', 0.8),
        ('CTAS', '2025-12-18', 0.8),
        ('FDX', '2025-12-18', 0.8),
        ('MU', '2025-12-18', 0.8),
        ('CCL', '2025-12-19', 0.8),
        ('PAYX', '2025-12-19', 0.8),
        ('UAL', '2026-01-20', 0.8),
        ('INTC', '2026-01-22', 0.8),
        ('MSFT', '2026-01-28', 0.8),
        ('MSFT', '2026-02-05', 0.8)
    ));
ALTER TABLE sentiment_history DROP COLUMN size_modifier;
