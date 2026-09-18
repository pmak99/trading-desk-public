-- Panel queries for the provisioned Grafana dashboards.
--
-- Each block is delimited by a `-- name:` comment and executed against a
-- fixture database in CI (scripts/tests/test_dashboard_queries.py). Panels in
-- the dashboard JSON must carry the matching query VERBATIM — the harness
-- tests this file, so a panel with drifted SQL is untested SQL.

-- name: signal_data_freshness
-- substr, not strftime, to shorten the display: strftime re-parses the string
-- as a timestamp and, when SQLite treats a value as UTC-relative, can shift
-- the displayed wall-clock time -- verified this actually happens on
-- job_status.updated_at (see ops_last_run_per_job below), where strftime
-- silently turned 08:00 ET into 12:00. substr just truncates the string, so
-- whatever wall-clock value was written is exactly what displays.
SELECT to_char(MAX(timestamp) AT TIME ZONE 'America/New_York',
               'YYYY-MM-DD HH24:MI') AS last_analysis,
       CAST(EXTRACT(EPOCH FROM (now() - MAX(timestamp))) / 3600 AS INT) AS hours_stale
FROM analysis_log;

-- name: signal_sample_sizes
-- Replaces four separate stat panels (analyses_30d, ab_sample_accrual,
-- term_slope_sample, implied_vs_actual_sample), which between them spent 18 of
-- 24 grid columns reporting counts. Same numbers, one table.
-- APPROXIMATE for the A/B row: /ab-status (ab_evaluate_live.py) remains
-- authoritative -- it grades the FIRST analysis taken before the reaction-day
-- open, one row per issuer reaction, where this counts distinct matched events.
-- This panel says when it is worth re-running that script, never what its
-- verdict is.
-- `ord` sorts but is not selected: Postgres allows ORDER BY on a subquery
-- column absent from the outer list, which keeps a sort key out of the table
-- without depending on a Grafana column-hiding property.
SELECT metric, value, gate FROM (
  SELECT 1 AS ord, 'Analyses (30 days)' AS metric,
         (SELECT COUNT(*) FROM analysis_log
           WHERE timestamp >= now() - interval '30 days') AS value,
         NULL::bigint AS gate
  UNION ALL SELECT 2, 'A/B events with known outcome',
    (SELECT COUNT(*) FROM (
       SELECT DISTINCT a.ticker, a.earnings_date
       FROM analysis_log a
       JOIN historical_moves h ON h.ticker = a.ticker
        AND ABS(h.earnings_date - a.earnings_date) <= 30
       WHERE h.close_move_pct IS NOT NULL AND a.vrp_intraday_ratio IS NOT NULL) y), 300
  UNION ALL SELECT 3, 'Rows carrying term slope',
    (SELECT COUNT(term_slope_ratio) FROM analysis_log), NULL
) x ORDER BY ord;

-- name: signal_crush_by_vrp_tier
-- Does the tier predict anything? The dashboard previously showed the tier
-- DISTRIBUTION twice (an all-time piechart and a 90-day barchart) and its
-- OUTCOME never -- which is the premise the whole system rests on.
-- "Crushed" is research/events.py's definition verbatim (line 134: the implied
-- straddle exceeded the absolute close move). No new definition is invented
-- here, and no VRP ladder is rebuilt: the tier is read from the row's own
-- graded `recommendation`, exactly as signal_vrp_tier_over_time does.
-- GROUPED BY vrp_convention deliberately, never pooled. Every row before the
-- migration (2026-09-16 19:20 ET) is 'intraday'; close-to-close rows accrue
-- from there. CLAUDE.md: never compare the two conventions at a shared numeric
-- threshold -- close-VRP is mechanically smaller, so pooling would compare
-- units, not tiers. The close row reads thin or absent for months. That is the
-- honest rendering of a one-day-old convention, not a broken panel.
-- Latest analysis per (ticker, earnings_date), matching the sibling panels on
-- this dashboard; ab_evaluate_live.py uses the first pre-open analysis instead
-- and remains authoritative for the A/B verdict.
WITH latest AS (
  SELECT ticker, earnings_date, recommendation, vrp_convention, implied_move_pct,
         ROW_NUMBER() OVER (PARTITION BY ticker, earnings_date
                            ORDER BY timestamp DESC) AS rn
  FROM analysis_log
  WHERE recommendation IS NOT NULL
),
joined AS (
  SELECT l.recommendation, l.vrp_convention,
         COALESCE(h.pre_earnings_straddle_pct, l.implied_move_pct) AS implied,
         ABS(h.close_move_pct) AS actual,
         RANK() OVER (PARTITION BY l.ticker, l.earnings_date
                      ORDER BY ABS(h.earnings_date - l.earnings_date)) AS nearness
  FROM latest l
  JOIN historical_moves h
    ON h.ticker = l.ticker AND ABS(h.earnings_date - l.earnings_date) <= 30
  WHERE l.rn = 1 AND h.close_move_pct IS NOT NULL
)
SELECT vrp_convention AS graded_on, recommendation AS tier, COUNT(*) AS events,
       ROUND((100.0 * COUNT(*) FILTER (WHERE implied > actual) / COUNT(*))::numeric, 1)
         AS crush_rate
FROM joined
WHERE nearness = 1 AND implied IS NOT NULL AND actual > 0
GROUP BY graded_on, tier
ORDER BY graded_on, tier;

-- name: signal_vrp_tier_over_time
-- Pivoted to wide format (one numeric column per tier). Grafana's native
-- barchart panel takes the FIRST field as the x-axis category and every
-- remaining NUMERIC field as its own bar series -- it does not split a
-- long-format categorical column (the previous `tier` text column) into
-- series. The long-format version rendered duplicate week labels on the
-- x-axis with a single unlabeled "n" series, silently dropping the tier
-- dimension entirely.
-- Rolling 90-day window (~13 weeks). analysis_log grows forever, and an
-- unbounded x-axis crowds unreadable within a year regardless of label
-- rotation. Set to 90 days per explicit request 2026-09-09 -- this DOES trim
-- currently-visible history (the earliest 2 of 16 weeks, ~180 analyses), it
-- is not a no-op safety margin like the earlier 120-day version was. Matches
-- the panel's own "regime drift" framing: recent trend, not full history.
-- Labeled by the week's Monday date, not strftime('%Y-%W', ...): a week
-- NUMBER ("2026-32") requires doing calendar math to know what it means; the
-- Monday date ("2026-08-10") is immediately readable. Verified identical
-- bucket count (16) against the old week-number grouping before switching.
-- to_char, never a raw `date` column: Grafana's Postgres datasource returns a
-- date as UTC midnight and the dashboard renders in `timezone: browser`, so in
-- ET every raw date displayed as the PREVIOUS day at 20:00 (journal freshness
-- read "2026-09-10 20:00:00" for a real max of 2026-09-11). Text has no
-- timezone to reinterpret. Same reasoning as ops_last_run_per_job's to_char.
SELECT to_char(date_trunc('week', timestamp), 'YYYY-MM-DD') AS week,
       SUM(CASE WHEN recommendation = 'excellent' THEN 1 ELSE 0 END) AS EXCELLENT,
       SUM(CASE WHEN recommendation = 'good' THEN 1 ELSE 0 END) AS GOOD,
       SUM(CASE WHEN recommendation = 'marginal' THEN 1 ELSE 0 END) AS MARGINAL,
       SUM(CASE WHEN recommendation = 'skip' THEN 1 ELSE 0 END) AS SKIP
FROM analysis_log
WHERE timestamp >= now() - interval '90 days'
GROUP BY week
ORDER BY week;

-- name: signal_implied_vs_actual_tier
-- Bucketed, never averaged: the raw ratio is extremely right-skewed (live
-- data 2026-09: mean 6.8x, median far lower, max 996x -- a handful of events
-- where the actual move landed near zero blow up the denominator). A mean or
-- a printf-bucketed histogram (the term_slope_histogram pattern) would render
-- as one dominant bar plus ~190 singleton outlier bars. Discrete tiers, like
-- the VRP tiers above, are the readable version of the same question: how
-- often does the market's implied move overstate what actually happens (the
-- premise IV crush sells into) vs understate it (the dangerous case for a
-- premium seller). Verified non-degenerate on live data: 2104 / 1403 / 2123 /
-- 3083 across the four tiers (n=8,713).
-- Wide format, single row -- same reason as signal_vrp_tier_distribution:
-- PieChart doesn't honor value-mapping slice colors, only byName field
-- overrides ("multiple fields" mode). Short bare names (no "(1.5-3.0x)"
-- suffix): those need to be valid column identifiers now; the numeric ranges
-- live in this panel's description instead.
SELECT
  SUM(CASE WHEN overpricing_ratio < 1.0 THEN 1 ELSE 0 END) AS UNDERPRICED,
  SUM(CASE WHEN overpricing_ratio >= 1.0 AND overpricing_ratio < 1.5 THEN 1 ELSE 0 END) AS FAIR,
  SUM(CASE WHEN overpricing_ratio >= 1.5 AND overpricing_ratio < 3.0 THEN 1 ELSE 0 END) AS OVERPRICED,
  SUM(CASE WHEN overpricing_ratio >= 3.0 THEN 1 ELSE 0 END) AS HEAVILY_OVERPRICED
FROM (
  SELECT COALESCE(h.pre_earnings_straddle_pct, a.implied_move_pct)
             / NULLIF(ABS(h.close_move_pct), 0) AS overpricing_ratio
  FROM historical_moves h
  LEFT JOIN (
      SELECT hm.id, ev.implied_move_pct,
             RANK() OVER (PARTITION BY hm.id
                          ORDER BY ABS(ev.earnings_date - hm.earnings_date)) AS nearness
      FROM (
          SELECT ticker, earnings_date, implied_move_pct,
                 ROW_NUMBER() OVER (PARTITION BY ticker, earnings_date ORDER BY timestamp DESC) AS rn
          FROM analysis_log
      ) ev
      JOIN historical_moves hm
        ON hm.ticker = ev.ticker AND ABS(ev.earnings_date - hm.earnings_date) <= 30
      WHERE ev.rn = 1
  ) a ON a.id = h.id AND a.nearness = 1
  WHERE COALESCE(h.pre_earnings_straddle_pct, a.implied_move_pct) IS NOT NULL
    AND h.close_move_pct IS NOT NULL
)
WHERE overpricing_ratio IS NOT NULL;

-- name: signal_implied_vs_actual_recent
-- Per-event detail behind the tier chart above -- identical join, unaggregated.
-- Ticker breaks same-day ties: ordered by date alone, which rows of one day
-- appeared, and which made the 50, depended on the plan.
-- to_char, never a raw `date` column: Grafana's Postgres datasource returns a
-- date as UTC midnight and the dashboard renders in `timezone: browser`, so in
-- ET every raw date displayed as the PREVIOUS day at 20:00 (journal freshness
-- read "2026-09-10 20:00:00" for a real max of 2026-09-11). Text has no
-- timezone to reinterpret. Same reasoning as ops_last_run_per_job's to_char.
SELECT to_char(h.earnings_date, 'YYYY-MM-DD') AS earnings_date, h.ticker,
       ROUND((COALESCE(h.pre_earnings_straddle_pct, a.implied_move_pct))::numeric, 2) AS implied_move_pct,
       ROUND((h.close_move_pct)::numeric, 2) AS actual_move_pct,
       ROUND((COALESCE(h.pre_earnings_straddle_pct, a.implied_move_pct)
             / NULLIF(ABS(h.close_move_pct), 0))::numeric, 2) AS overpricing_ratio
FROM historical_moves h
LEFT JOIN (
    SELECT hm.id, ev.implied_move_pct,
           RANK() OVER (PARTITION BY hm.id
                        ORDER BY ABS(ev.earnings_date - hm.earnings_date)) AS nearness
    FROM (
        SELECT ticker, earnings_date, implied_move_pct,
               ROW_NUMBER() OVER (PARTITION BY ticker, earnings_date ORDER BY timestamp DESC) AS rn
        FROM analysis_log
    ) ev
    JOIN historical_moves hm
      ON hm.ticker = ev.ticker AND ABS(ev.earnings_date - hm.earnings_date) <= 30
    WHERE ev.rn = 1
) a ON a.id = h.id AND a.nearness = 1
WHERE COALESCE(h.pre_earnings_straddle_pct, a.implied_move_pct) IS NOT NULL
  AND h.close_move_pct IS NOT NULL
ORDER BY h.earnings_date DESC, h.ticker
LIMIT 50;

-- name: signal_term_slope_histogram
-- printf, not ROUND: ROUND() returns SQLite type REAL, and Grafana's native
-- barchart panel rejects a numeric x-axis outright ("Bar charts require a
-- string or time field") -- it doesn't auto-stringify. printf produces TEXT
-- with the same one-decimal formatting.
SELECT to_char(term_slope_ratio, 'FM990.0') AS slope_bucket, COUNT(*) AS n
FROM analysis_log
WHERE term_slope_ratio IS NOT NULL
GROUP BY slope_bucket
ORDER BY slope_bucket;

-- name: signal_recent_analyses
SELECT to_char(timestamp AT TIME ZONE 'America/New_York',
               'YYYY-MM-DD HH24:MI') AS timestamp, ticker,
       to_char(earnings_date, 'YYYY-MM-DD') AS earnings_date,
       ROUND((vrp_close_ratio)::numeric, 2) AS vrp_close,
       ROUND((vrp_intraday_ratio)::numeric, 2) AS vrp_intraday,
       vrp_convention AS graded_on,
       recommendation,
       ROUND((term_slope_ratio)::numeric, 2) AS term_slope,
       COALESCE(source, 'unknown') AS source
FROM analysis_log
ORDER BY timestamp DESC
LIMIT 50;

-- name: trading_journal_freshness
-- A SECOND, independent clock: the journal only advances when /journal runs.
-- Must not share the signal dashboard's freshness stat.
-- Every trade count, win rate, average and size band on this dashboard reads
-- positions, one row per trade: strategies holds a row per account, and a
-- trade placed in both the IRA and TAXABLE is two rows there (migration 5).
-- Per-account P&L and the per-row recent-trades list stay on strategies.
-- to_char, never a raw `date` column: Grafana's Postgres datasource returns a
-- date as UTC midnight and the dashboard renders in `timezone: browser`, so in
-- ET every raw date displayed as the PREVIOUS day at 20:00 (journal freshness
-- read "2026-09-10 20:00:00" for a real max of 2026-09-11). Text has no
-- timezone to reinterpret. Same reasoning as ops_last_run_per_job's to_char.
SELECT to_char(MAX(sale_date), 'YYYY-MM-DD') AS last_closed_trade,
       COUNT(*) AS total_trades
FROM positions;

-- name: trading_monthly_pnl
-- Pivoted to wide format (one numeric column per account): same reason as
-- signal_vrp_tier_over_time -- Grafana's native barchart needs one column per
-- series, not a categorical column (the previous `account_type` text column)
-- identifying which series a row belongs to.
-- strategies, not positions: a merged position's account is 'IRA+TAXABLE'.
-- Raw CASH P&L, not wash-sale adjusted. Fidelity's CSV omits adjusted basis,
-- so this deliberately will not tie to their UI.
SELECT to_char(sale_date, 'YYYY-MM') AS month,
       ROUND((SUM(CASE WHEN account_type = 'IRA' THEN gain_loss ELSE 0 END))::numeric, 0) AS IRA,
       ROUND((SUM(CASE WHEN account_type = 'TAXABLE' THEN gain_loss ELSE 0 END))::numeric, 0) AS TAXABLE
FROM strategies
WHERE sale_date IS NOT NULL
GROUP BY month
ORDER BY month;

-- name: trading_win_rate_by_month
SELECT to_char(sale_date, 'YYYY-MM') AS month,
       COUNT(*) AS trades,
       ROUND((100.0 * COUNT(*) FILTER (WHERE is_winner) / COUNT(*))::numeric, 1) AS win_rate
FROM positions
WHERE sale_date IS NOT NULL
GROUP BY month
ORDER BY month;

-- name: trading_contract_buckets
-- Reads rule_evidence_snapshot, so the band boundaries are §6's by
-- construction rather than a second copy of them written out here.
-- cohort 'all' is every option trade with a quantity, STOCK excluded because
-- its quantity is shares -- exactly the scope this panel had before, verified
-- 2026-09-17 against the old query at 164 / 42 / 40 / 29 with identical P&L.
-- A trade placed in both accounts is one position with both accounts'
-- contracts summed (§6), because rule_evidence loads the positions view.
-- ORDER BY band_order, never band: lexicographically 1-50 precedes 101-200
-- precedes 200+ precedes 51-100.
SELECT band AS bucket, trades, pnl, avg_loss
FROM rule_evidence_snapshot
WHERE dimension = 'size' AND cohort = 'all'
ORDER BY band_order;

-- name: trading_trr_at_entry
-- Reads rule_evidence_snapshot, so NO TRR ladder appears here: the 2.5 / 1.5
-- boundaries live in src/domain/tail_risk.py and reach this table through
-- rule_evidence.trr_tier_at_entry. Until 2026-09-17 this query wrote them out
-- as SQL literals AND filtered `trr_at_entry IS NOT NULL`, which silently
-- dropped the 65 of 300 positions whose TRR is UNKNOWN -- a named tier in
-- CLAUDE.md §3, not an absence.
-- The stored column and the recomputed tier agree: both call
-- tail_risk.trr_as_of over gap moves before entry (235 of 235 on 2026-09-17).
-- cohort 'all' preserves what this panel has always shown, every trade rather
-- than §3's earnings-trade table; cohort 'earnings' holds the latter if a
-- panel ever wants it.
SELECT band AS trr_level, trades, pnl
FROM rule_evidence_snapshot
WHERE dimension = 'trr' AND cohort = 'all'
ORDER BY band_order;

-- name: trading_exit_timing
-- CLAUDE.md §8, the largest measured effect in the book and, until 2026-09-17,
-- the only rule with no panel anywhere: spreads out the next trading day win
-- 90.1% (n=111) against 56.0% held two days or more
-- (n=25).
-- Reads the snapshot because "next trading day" is is_trading_day()
-- (src/domain/market_calendar.py), NOT calendar days. positions.days_held is
-- calendar days: the two agreed on all 136 spreads on 2026-09-17, but a spread
-- entered Friday and exited Monday is 3 calendar days and 1 trading day and
-- would land in the opposite band. Reimplementing the NYSE calendar in SQL is
-- the same duplication as a ladder.
SELECT cohort AS strategy, band AS exit_timing, trades,
       ROUND(100.0 * wins / NULLIF(trades, 0), 1) AS win_rate, pnl
FROM rule_evidence_snapshot
WHERE dimension = 'held'
ORDER BY cohort, band_order;

-- name: trading_dte_bands
-- CLAUDE.md §5, the evidence for the MIN_DTE = 3 floor. Win rates MATCH at 0-2
-- and 3-5 (84.1% both); losses run about 43% larger at 0-2, so the case for the
-- floor is loss severity alone, not win rate. Earnings trades only, matching
-- §5's table.
-- DTE is plain expiration - acquired_date and would be correct in SQL. It reads
-- the snapshot so that '0-2' / '3-5' / '6+' have ONE definition
-- (rule_evidence.DTE_BANDS) rather than a second copy here.
SELECT band AS dte_at_entry, trades,
       ROUND(100.0 * wins / NULLIF(trades, 0), 1) AS win_rate, avg_loss, pnl
FROM rule_evidence_snapshot
WHERE dimension = 'dte' AND cohort = 'earnings'
ORDER BY band_order;

-- name: trading_trade_type
-- Every writer stores trade_type since 2026-09-13, when the 93 NULL rows (every
-- IRA option position and every stock row) were set to NEW; 'unlabelled' means
-- a writer skipped the column.
SELECT COALESCE(trade_type, 'unlabelled') AS trade_type,
       COUNT(*) AS trades, ROUND((SUM(gain_loss))::numeric, 0) AS pnl
FROM positions
GROUP BY trade_type
ORDER BY trades DESC;

-- name: trading_banned_strategy_tripwire
-- Date-scoped: STRANGLE and IRON_CONDOR exist historically and always will
-- (permanently banned, see CLAUDE.md). An unscoped count would fire forever;
-- only post-ban rows indicate a generator regression.
SELECT COUNT(*) AS banned_after_ban
FROM positions
WHERE strategy_type IN ('IRON_CONDOR', 'IRON_BUTTERFLY', 'STRANGLE')
  AND acquired_date > '2026-05-31';

-- name: trading_one_position_tripwire
-- CLAUDE.md §6: one position per ticker per earnings event. No second bet, no
-- repair. A repair campaign on record was two positions on one event.
-- Date-scoped like trading_banned_strategy_tripwire, and for the same reason:
-- 15 events violate this all-time but only 1 since 2026-05-31 (most recent
-- 2026-08-03). An unscoped count could never return to zero, and a tripwire
-- that is permanently red is decoration.
-- Reads positions, so a trade placed in both the IRA and TAXABLE is one
-- position, not two. Each real violation is a SPREAD *and* a SINGLE on the
-- same ticker and event, entered the same day (one case: a 100-lot
-- single plus a 200-lot spread, both losers).
-- SELECT COUNT(*), never a bare GROUP BY ... HAVING: the HAVING form returns
-- ZERO rows when clean, and test_all_queries_return_at_least_one_row has no
-- exemption list -- a clean tripwire would fail CI.
SELECT COUNT(*) AS multi_position_events
FROM (
  SELECT symbol, earnings_date
  FROM positions
  WHERE earnings_date IS NOT NULL
    AND strategy_type IN ('SPREAD', 'SINGLE')
    AND acquired_date > '2026-05-31'
  GROUP BY symbol, earnings_date
  HAVING COUNT(*) > 1
) x;

-- name: trading_contract_cap_tripwire
-- CLAUDE.md §6 rule 5: never exceed 100 contracts, whatever the conviction.
-- Date-scoped for the same reason as the tripwire above: 69 positions breach
-- it all-time, 11 since 2026-05-31 (all single-name, no index trade inflating
-- the count).
-- STOCK is excluded because its quantity is shares, not contracts.
-- ⚠️ A contract cap is not a risk cap -- a high-priced single was 110 contracts at
-- ~$3,090 each, ~$340k of risk while reading as compliant. This counts
-- breaches of the stated rule, not dollars at risk; size on max loss.
SELECT COUNT(*) AS over_contract_cap
FROM positions
WHERE strategy_type <> 'STOCK'
  AND quantity > 100
  AND acquired_date > '2026-05-31';

-- name: trading_recent_closed
-- strategies, one row per account: the list shows each account's row, so a
-- trade placed in both accounts appears twice. Counts read positions.
-- to_char, never a raw `date` column: Grafana's Postgres datasource returns a
-- date as UTC midnight and the dashboard renders in `timezone: browser`, so in
-- ET every raw date displayed as the PREVIOUS day at 20:00 (journal freshness
-- read "2026-09-10 20:00:00" for a real max of 2026-09-11). Text has no
-- timezone to reinterpret. Same reasoning as ops_last_run_per_job's to_char.
SELECT to_char(sale_date, 'YYYY-MM-DD') AS sale_date,
       symbol, strategy_type, account_type, quantity, days_held,
       ROUND((gain_loss)::numeric, 0) AS pnl, is_winner
FROM strategies
WHERE sale_date IS NOT NULL
ORDER BY sale_date DESC
LIMIT 50;

-- name: trading_stance_accuracy
-- Was our implied stance (bullish = short a PUT, bearish = short a CALL) a
-- winning bet? Short leg identified via the acquired_date > sale_date
-- inversion -- Fidelity's sell-to-open convention (documented under
-- Databases > Known data quality issues) -- grouped by (strike, option_type)
-- FIRST: a raw trade_journal row is one partial FILL, not a leg, and
-- counting fills as legs was root-caused live against this exact query
-- 2026-09-11 (see Incident History) before being fixed here, the fourth
-- occurrence of the same bug pattern this session. proceeds > cost_basis was
-- tried first and rejected: it conflates direction with win/loss, since a
-- LOSING short leg can show proceeds < cost_basis and look long -- validated
-- the date-inversion signal instead against 1,121 legs (87.8% of inverted
-- legs won vs 84.6% of normal legs lost, the expected split for a real
-- direction signal). Scoped to SPREAD/SINGLE: IRON_CONDOR/STRANGLE carry two
-- short legs of opposite type, so there is no single implied stance.
-- One row per trade: positions.id is the lowest of its strategies ids, and a
-- merged position's rows share their legs, so its short leg joins on that id
-- once (joining every strategy_id would count the trade once per account).
WITH short_legs AS (
  SELECT strategy_id, strike, option_type,
         MAX(CASE WHEN acquired_date > sale_date THEN 1 ELSE 0 END) AS is_short
  FROM trade_journal
  WHERE option_type IS NOT NULL
  GROUP BY strategy_id, strike, option_type
),
resolved_short AS (
  SELECT strategy_id, MIN(option_type) AS short_option_type
  FROM short_legs
  WHERE is_short = 1
  GROUP BY strategy_id
  HAVING COUNT(*) = 1
)
SELECT CASE WHEN rs.short_option_type = 'PUT' THEN 'BULLISH' ELSE 'BEARISH' END AS stance,
       COUNT(*) AS trades,
       ROUND((100.0 * COUNT(*) FILTER (WHERE s.is_winner) / COUNT(*))::numeric, 1) AS win_rate,
       ROUND((SUM(s.gain_loss))::numeric, 0) AS pnl
FROM positions s
JOIN resolved_short rs ON rs.strategy_id = s.id
WHERE s.strategy_type IN ('SPREAD', 'SINGLE')
  AND s.earnings_date IS NOT NULL AND s.sale_date IS NOT NULL
GROUP BY stance;

-- name: trading_direction_accuracy
-- Did the stock actually move the direction the position implied? A short
-- PUT implies bullish (correct if the close move was >= 0); a short CALL
-- implies bearish (correct if <= 0). Same short-leg detection as the stance
-- panel above, joined to the nearest historical move within 30 days -- an
-- exact-date join silently drops corrected dates (Aug 24 2026 incident).
-- Ranked by distance in one pass (RANK keeps equally near moves); until
-- 2026-09-14 a MIN subquery correlated per candidate row (4,203 loops, ~23 ms
-- median for each of the three panels, ~3-5 ms this way, identical rows).
WITH short_legs AS (
  SELECT strategy_id, strike, option_type,
         MAX(CASE WHEN acquired_date > sale_date THEN 1 ELSE 0 END) AS is_short
  FROM trade_journal
  WHERE option_type IS NOT NULL
  GROUP BY strategy_id, strike, option_type
),
resolved_short AS (
  SELECT strategy_id, MIN(option_type) AS short_option_type
  FROM short_legs
  WHERE is_short = 1
  GROUP BY strategy_id
  HAVING COUNT(*) = 1
),
nearest_hm AS (
  SELECT strategy_id, close_move_pct
  FROM (
    SELECT s.id AS strategy_id, h.close_move_pct,
           RANK() OVER (PARTITION BY s.id
                        ORDER BY ABS(h.earnings_date - s.earnings_date)) AS nearness
    FROM positions s
    JOIN historical_moves h
      ON h.ticker = s.symbol AND ABS(h.earnings_date - s.earnings_date) <= 30
    WHERE s.strategy_type IN ('SPREAD', 'SINGLE') AND s.earnings_date IS NOT NULL
  ) ranked
  WHERE nearness = 1
)
SELECT CASE
         WHEN (rs.short_option_type = 'PUT' AND nh.close_move_pct >= 0)
           OR (rs.short_option_type = 'CALL' AND nh.close_move_pct <= 0)
         THEN 'DIRECTION_CORRECT' ELSE 'DIRECTION_WRONG' END AS call,
       COUNT(*) AS trades,
       ROUND((100.0 * COUNT(*) FILTER (WHERE s.is_winner) / COUNT(*))::numeric, 1) AS win_rate,
       ROUND((SUM(s.gain_loss))::numeric, 0) AS pnl
FROM positions s
JOIN resolved_short rs ON rs.strategy_id = s.id
JOIN nearest_hm nh ON nh.strategy_id = s.id
WHERE s.strategy_type IN ('SPREAD', 'SINGLE')
  AND s.earnings_date IS NOT NULL AND s.sale_date IS NOT NULL
  AND nh.close_move_pct IS NOT NULL
GROUP BY call;

-- name: trading_strike_accuracy
-- Did the short strike hold? HELD = a short PUT strike stayed below the
-- actual close, or a short CALL strike stayed above it -- BREACHED means
-- price crossed the short strike, i.e. the position was exposed to
-- assignment/max-loss risk regardless of how it was eventually closed. Same
-- short-leg detection and nearest-within-30-days join as the panels above.
-- Excludes rows where short_strike/earnings_close falls outside a plausible
-- 0.45-2.2x band (~4 rows in production: NFLX/CRWD/NOW) -- flagged
-- 2026-09-11 as a likely historical_moves price-scale data-quality issue for
-- those specific tickers, out of scope to fix here. Not applied to the
-- stance/direction panels above: those compare percentages, which a
-- proportional price-scaling artifact leaves unaffected; only an absolute
-- strike-vs-price comparison needs the filter.
WITH short_legs AS (
  SELECT strategy_id, strike, option_type,
         MAX(CASE WHEN acquired_date > sale_date THEN 1 ELSE 0 END) AS is_short
  FROM trade_journal
  WHERE option_type IS NOT NULL
  GROUP BY strategy_id, strike, option_type
),
resolved_short AS (
  SELECT strategy_id, MIN(strike) AS short_strike, MIN(option_type) AS short_option_type
  FROM short_legs
  WHERE is_short = 1
  GROUP BY strategy_id
  HAVING COUNT(*) = 1
),
nearest_hm AS (
  SELECT strategy_id, earnings_close
  FROM (
    SELECT s.id AS strategy_id, h.earnings_close,
           RANK() OVER (PARTITION BY s.id
                        ORDER BY ABS(h.earnings_date - s.earnings_date)) AS nearness
    FROM positions s
    JOIN historical_moves h
      ON h.ticker = s.symbol AND ABS(h.earnings_date - s.earnings_date) <= 30
    WHERE s.strategy_type IN ('SPREAD', 'SINGLE') AND s.earnings_date IS NOT NULL
  ) ranked
  WHERE nearness = 1
)
SELECT CASE
         WHEN (rs.short_option_type = 'PUT' AND nh.earnings_close <= rs.short_strike)
           OR (rs.short_option_type = 'CALL' AND nh.earnings_close >= rs.short_strike)
         THEN 'BREACHED' ELSE 'HELD' END AS outcome,
       COUNT(*) AS trades,
       ROUND((100.0 * COUNT(*) FILTER (WHERE s.is_winner) / COUNT(*))::numeric, 1) AS win_rate,
       ROUND((SUM(s.gain_loss))::numeric, 0) AS pnl
FROM positions s
JOIN resolved_short rs ON rs.strategy_id = s.id
JOIN nearest_hm nh ON nh.strategy_id = s.id
WHERE s.strategy_type IN ('SPREAD', 'SINGLE')
  AND s.earnings_date IS NOT NULL AND s.sale_date IS NOT NULL
  AND nh.earnings_close IS NOT NULL AND nh.earnings_close > 0
  AND (rs.short_strike / nh.earnings_close) BETWEEN 0.45 AND 2.2
GROUP BY outcome;

-- name: trading_direction_strike_crosstab
-- The synthesis panel: direction and strike outcome are NOT the same
-- question (direction is about the SIGN of the move from entry, strike is
-- about whether price crossed a specific level), so all four combinations
-- are possible in principle -- whether one is actually empty is a live data
-- question, not a logical certainty. Same short-leg detection, join, and
-- plausibility filter as the two panels above.
WITH short_legs AS (
  SELECT strategy_id, strike, option_type,
         MAX(CASE WHEN acquired_date > sale_date THEN 1 ELSE 0 END) AS is_short
  FROM trade_journal
  WHERE option_type IS NOT NULL
  GROUP BY strategy_id, strike, option_type
),
resolved_short AS (
  SELECT strategy_id, MIN(strike) AS short_strike, MIN(option_type) AS short_option_type
  FROM short_legs
  WHERE is_short = 1
  GROUP BY strategy_id
  HAVING COUNT(*) = 1
),
nearest_hm AS (
  SELECT strategy_id, close_move_pct, earnings_close
  FROM (
    SELECT s.id AS strategy_id, h.close_move_pct, h.earnings_close,
           RANK() OVER (PARTITION BY s.id
                        ORDER BY ABS(h.earnings_date - s.earnings_date)) AS nearness
    FROM positions s
    JOIN historical_moves h
      ON h.ticker = s.symbol AND ABS(h.earnings_date - s.earnings_date) <= 30
    WHERE s.strategy_type IN ('SPREAD', 'SINGLE') AND s.earnings_date IS NOT NULL
  ) ranked
  WHERE nearness = 1
)
SELECT
  CASE WHEN (rs.short_option_type = 'PUT' AND nh.close_move_pct >= 0)
         OR (rs.short_option_type = 'CALL' AND nh.close_move_pct <= 0)
       THEN 'direction right' ELSE 'direction wrong' END
  || ', strike ' ||
  CASE WHEN (rs.short_option_type = 'PUT' AND nh.earnings_close <= rs.short_strike)
         OR (rs.short_option_type = 'CALL' AND nh.earnings_close >= rs.short_strike)
       THEN 'breached' ELSE 'held' END AS scenario,
  COUNT(*) AS trades,
  ROUND((100.0 * COUNT(*) FILTER (WHERE s.is_winner) / COUNT(*))::numeric, 1) AS win_rate,
  ROUND((SUM(s.gain_loss))::numeric, 0) AS pnl
FROM positions s
JOIN resolved_short rs ON rs.strategy_id = s.id
JOIN nearest_hm nh ON nh.strategy_id = s.id
WHERE s.strategy_type IN ('SPREAD', 'SINGLE')
  AND s.earnings_date IS NOT NULL AND s.sale_date IS NOT NULL
  AND nh.close_move_pct IS NOT NULL AND nh.earnings_close IS NOT NULL
  AND nh.earnings_close > 0
  AND (rs.short_strike / nh.earnings_close) BETWEEN 0.45 AND 2.2
GROUP BY scenario
ORDER BY pnl DESC;

-- name: trading_risk_metrics
-- Computed in Python (scripts/refresh_risk_metrics.py::refresh_risk_metrics),
-- not here: Sharpe/Sortino need net_campaigns()'s netting rule (each option leg
-- claimed by at most one overlapping single-name stock campaign, per
-- docs/SHARPE_ANALYSIS_2026-09.md §2), which is not expressible as a plain
-- SQL aggregate. This is a deliberately trivial read of a precomputed table,
-- the same pattern this dashboard already uses for A/B verdicts (computed in
-- ab_evaluate_live.py, never reimplemented in a panel query). 1- and
-- 3-month windows are too few data points for these ratios to mean much
-- statistically -- shown anyway, as a directional trend line, not a claim.
-- computed shows WHEN the weekly refresh last wrote the row (migration 8). A
-- precomputed panel cannot otherwise distinguish a current Sharpe from one
-- left behind by a refresh that stopped running weeks ago. Rendered with
-- to_char, never as a bare timestamp: text has no timezone for the browser to
-- reinterpret. NULL reads 'never' -- rows written before migration 8, whose
-- real compute time is not recoverable.
SELECT window_months, ROUND((sharpe)::numeric, 2) AS sharpe, ROUND((sortino)::numeric, 2) AS sortino,
       n_months,
       COALESCE(to_char(computed_at, 'YYYY-MM-DD HH24:MI'), 'never') AS computed
FROM risk_metrics_snapshot
ORDER BY window_months;

-- name: ops_last_run_per_job
-- health mirrors runner.py's own claim logic (STALE_RUNNING_AFTER_SEC=3600):
-- a 'running' row is only suspect past that age, matching what runner.py
-- itself treats as a dead claim it will reclaim. Computed here, not left to
-- Grafana threshold config, because "stale" depends on TWO fields jointly
-- (status AND age) -- a single-field numeric threshold can't express that,
-- but a single categorical column can be colored by value mapping.
-- updated_at is displayed via substr, never strftime: job_status.updated_at
-- carries a timezone offset (e.g. -04:00), and strftime re-parses that as
-- UTC-relative, silently shifting the displayed time (verified: 08:00 ET
-- rendered as 12:00). substr truncates the string without reinterpreting it,
-- so the original ET wall-clock value displays correctly. The health CASE
-- below still reads the RAW updated_at, not the truncated display column --
-- only the shown value is shortened, the staleness math needs full precision.
SELECT job_name, status,
       to_char(updated_at AT TIME ZONE 'America/New_York',
               'YYYY-MM-DD HH24:MI') AS updated_at,
       (CURRENT_DATE - date) AS days_ago,
       CASE WHEN status = 'error' THEN 'ERROR'
            WHEN status = 'running'
             AND now() - updated_at > interval '1 hour'
            THEN 'STALE'
            ELSE 'OK' END AS health
FROM job_status
WHERE (job_name, date) IN (SELECT job_name, MAX(date) FROM job_status GROUP BY job_name)
ORDER BY job_name;

-- name: ops_run_history
-- to_char, never a raw `date` column: Grafana's Postgres datasource returns a
-- date as UTC midnight and the dashboard renders in `timezone: browser`, so in
-- ET every raw date displayed as the PREVIOUS day at 20:00 (journal freshness
-- read "2026-09-10 20:00:00" for a real max of 2026-09-11). Text has no
-- timezone to reinterpret. Same reasoning as ops_last_run_per_job's to_char.
SELECT to_char(date, 'YYYY-MM-DD') AS date, job_name, status
FROM job_status
ORDER BY date DESC, job_name
LIMIT 90;

-- name: ops_days_since_success
SELECT job_name,
       (CURRENT_DATE - MAX(date)) AS days_since_success
FROM job_status
WHERE status = 'success'
GROUP BY job_name;

-- name: ops_failure_count
-- Scoped to 30 days. Unbounded this read 0 on 2026-09-17, so it was not red --
-- but it had no date scope at all, meaning the FIRST error ever recorded would
-- have turned it red permanently with no path back to green. That is the "a
-- monitor that is always red is decoration" failure mode, latent rather than
-- live. A window makes it an alert again.
SELECT COUNT(*) AS failures
FROM job_status
WHERE status = 'error'
  AND date >= CURRENT_DATE - 30;

-- name: ops_history_coverage
-- Replaces a row count of three of sixteen tables, which answered a question
-- Kuma monitors 44/45 (can a client reach the database) and ops_last_run_per_job
-- (did the job run) already answer better.
-- This answers one nothing else on any dashboard does: how stale is the move
-- history that every VRP and TRR is computed from. On 2026-09-16, 488 of 984
-- tickers with >=4 quarters were stale.
-- A DISTRIBUTION, not a threshold: QUARTER_GAP_DAYS lives in
-- common/constants.py, and restating 150 here would be the same duplication as
-- a VRP or TRR ladder in SQL. The reader sees the shape and judges.
-- The numeric prefix is the sort key -- plain text ordering put '181-365d'
-- before '91-180d'.
SELECT CASE WHEN age <= 90 THEN '1. 0-90d'
            WHEN age <= 180 THEN '2. 91-180d'
            WHEN age <= 365 THEN '3. 181-365d'
            ELSE '4. over 365d' END AS newest_move_age,
       COUNT(*) AS tickers
FROM (
  SELECT ticker, CURRENT_DATE - MAX(earnings_date) AS age
  FROM historical_moves
  GROUP BY ticker
) x
GROUP BY newest_move_age
ORDER BY newest_move_age;

