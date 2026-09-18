-- Migration 5: the positions view (2026-09-15).
--
-- Applied by MigrationManager, all statements in ONE transaction. Statements
-- are split on semicolons, so no semicolon may appear inside a statement or a
-- comment.
--
-- strategies holds a row per account. The same spread opened in the IRA and in
-- TAXABLE is two rows, and every trade count and win rate that read the table
-- counted it twice: ten pairs on 2026-09-15 (PLTR 297/322, DDOG 342/472, SPXW
-- 301/333, SPY 134/335, WDAY 369/379, ZS 404/411, HON 438/446, COF 444/449,
-- GOOG 445/450, NVDA 466/468). rule_evidence.py merged them in Python from that
-- day and the slash commands and Grafana did not. The view is the one
-- definition. Rows merge when symbol, strategy type, entry and exit dates,
-- expiration, legs (option type and strike, from trade_journal), earnings date
-- and parent all match. Size and P&L are summed and the win is decided on the
-- summed P&L. A second copy within one account pairs with the other account's
-- second copy, never with its own account. grafana reads it through trading's
-- default privileges (the tracked role grants).

CREATE VIEW "positions" AS
WITH legs AS (
    SELECT strategy_id,
           string_agg(DISTINCT option_type || ' ' || strike::text, ','
                      ORDER BY option_type || ' ' || strike::text) AS legs
    FROM trade_journal
    WHERE strategy_id IS NOT NULL AND option_type IS NOT NULL
    GROUP BY strategy_id
),
keyed AS (
    SELECT s.*, coalesce(l.legs, '') AS legs,
           row_number() OVER (
               PARTITION BY s.symbol, s.strategy_type, s.acquired_date, s.sale_date, s.expiration,
                            coalesce(l.legs, ''), s.earnings_date, s.parent_strategy_id, s.account_type
               ORDER BY s.id) AS copy
    FROM strategies s
    LEFT JOIN legs l ON l.strategy_id = s.id
)
SELECT min(id) AS id,
       array_agg(id ORDER BY id) AS strategy_ids,
       symbol,
       strategy_type,
       string_agg(account_type, '+' ORDER BY account_type) AS account_type,
       acquired_date,
       sale_date,
       min(days_held) AS days_held,
       expiration,
       sum(quantity) AS quantity,
       sum(net_credit) AS net_credit,
       sum(net_debit) AS net_debit,
       sum(gain_loss) AS gain_loss,
       sum(gain_loss) > 0 AS is_winner,
       earnings_date,
       min(actual_move) AS actual_move,
       min(trade_type) AS trade_type,
       parent_strategy_id,
       min(campaign_id) AS campaign_id,
       min(trr_at_entry) AS trr_at_entry,
       min(position_limit_at_entry) AS position_limit_at_entry,
       min(created_at) AS created_at,
       legs
FROM keyed
GROUP BY symbol, strategy_type, acquired_date, sale_date, expiration, legs, earnings_date,
         parent_strategy_id, copy
