"""Generate provisioned Grafana dashboard JSON from 2.0/dashboards/queries.sql.

Panels must carry their SQL verbatim from queries.sql, because that file is what
CI executes against a fixture — a panel with drifted SQL is untested SQL.
Generating the JSON makes that structural instead of a convention someone has to
remember. Re-run after editing queries.sql or a panel spec:

    2.0/venv/bin/python -m scripts.build_dashboards
"""

import json
import sys
from pathlib import Path

from scripts.dashboard_queries import load_queries

ROOT = Path(__file__).resolve().parents[1]
QUERIES = ROOT / "2.0" / "dashboards" / "queries.sql"
OUT_DIR = ROOT / "2.0" / "dashboards"

# The uid is deliberately unchanged across the SQLite -> Postgres repoint, so
# every panel keeps resolving its datasource; only the type moves.
DS = {"type": "grafana-postgresql-datasource", "uid": "ivcrush"}

# One semantic ramp, reused everywhere a panel buckets outcomes into a
# good->bad quality/risk tier: VRP tiers (piechart + over-time barchart) and
# the implied-vs-actual tiers (piechart). Red means the same thing in every
# panel on the Signal dashboard instead of Grafana's default arbitrary
# per-panel hue assignment. Applied via series_color_override on every one
# of these -- see that function's docstring for why value_mapping_override
# (tried first, screenshot-confirmed not to work on PieChart) isn't used.
QUALITY_TIER_COLORS = {
    "EXCELLENT": "dark-green", "GOOD": "light-green",
    "MARGINAL": "orange", "SKIP": "red",
}
OVERPRICING_TIER_COLORS = {
    "HEAVILY_OVERPRICED": "dark-green", "OVERPRICED": "light-green",
    "FAIR": "orange", "UNDERPRICED": "red",
}


def series_color_override(field_name, color):
    """Fix a wide-format series' (whole column's) color by field name.

    This is the ONLY mechanism used for tier coloring now. A per-VALUE
    mapping (matching e.g. "EXCELLENT" wherever it appears in one categorical
    column) was tried first for the piecharts and, per a live screenshot,
    silently did nothing -- Grafana's PieChart colors slices from
    fieldConfig.defaults.color (palette-classic by default), and value
    mappings only drive Stat/Gauge/BarGauge/Table display, never PieChart
    slice fill. The fix was pivoting both piechart queries to wide format
    (one numeric column per tier, single row) so PieChart's "multiple
    fields" mode applies, and coloring each FIELD via this same override --
    the exact mechanism already proven for the over-time barchart's columns.
    """
    return {
        "matcher": {"id": "byName", "options": field_name},
        "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
    }


def field_props_override(field_name, **props):
    """Set arbitrary fieldConfig.defaults-style properties (unit, decimals,
    thresholds, custom.hideFrom, ...) on one named field only, leaving every
    other field in the same query at its panel-level default."""
    return {
        "matcher": {"id": "byName", "options": field_name},
        "properties": [{"id": k, "value": v} for k, v in props.items()],
    }


# Every Stat/PieChart/Gauge/BarGauge panel defaults to color.mode:
# "thresholds", and Grafana's OWN built-in default thresholds (not ours --
# nothing was ever configured) are {green below 80, red at/above 80}. Every
# plain informational count here (sample sizes, disagreement counts, a
# closed-trade total) exceeds 80 eventually and renders in alarm red with
# zero signal behind it -- confirmed live: Journal freshness's 316, A/B
# sample's 299, Term-structure sample's 157, Convention disagreements' 704
# all rendered red despite none of them being a threshold breach anyone
# intended. NEUTRAL_COLOR opts a panel OUT of threshold-based coloring
# entirely with a fixed neutral color, reserving red/green for the two
# panels that carry a real, deliberately-configured alarm (Data freshness's
# hours_stale, Banned strategies after ban).
NEUTRAL_COLOR = {"color": {"mode": "fixed", "fixedColor": "text"}}


# Grafana labels a Stat tile and a Table column with the FIELD NAME, which is
# the raw SQL identifier -- panels read "hours_stale", "outcome_known_events",
# "lower_boundary". Explicit map, not auto-title-casing of snake_case: a byName
# matcher may resolve against the RENAMED field for later overrides, and the two
# tier piecharts color their slices via series_color_override matching byName on
# EXCELLENT/GOOD/MARGINAL/SKIP. Those tier names stay unmapped deliberately.
DISPLAY_NAMES = {
    "signal_data_freshness": {"last_analysis": "Newest analysis", "hours_stale": "Stale"},
    "signal_implied_vs_actual_recent": {
        "earnings_date": "Earnings", "ticker": "Ticker",
        "implied_move_pct": "Implied move", "actual_move_pct": "Actual move",
        "overpricing_ratio": "Implied / actual"},
    "signal_recent_analyses": {
        "timestamp": "Logged", "ticker": "Ticker", "earnings_date": "Earnings",
        "vrp_close": "VRP (close)", "vrp_intraday": "VRP (intraday)",
        "graded_on": "Graded on", "recommendation": "Tier",
        "term_slope": "Term slope", "source": "Source"},
    "signal_sample_sizes": {"metric": "Metric", "value": "Value", "gate": "Gate"},
    "signal_crush_by_vrp_tier": {
        "graded_on": "Graded on", "tier": "Tier",
        "events": "Events", "crush_rate": "Crush rate"},
    "signal_term_slope_histogram": {"slope_bucket": "Slope", "n": "Events"},
    "trading_journal_freshness": {
        "last_closed_trade": "Last closed", "total_trades": "Trades"},
    "trading_one_position_tripwire": {"multi_position_events": "Events"},
    "trading_contract_cap_tripwire": {"over_contract_cap": "Positions"},
    "trading_contract_buckets": {
        "bucket": "Contracts", "trades": "Trades", "pnl": "P&L", "avg_loss": "Avg loss"},
    "trading_trr_at_entry": {"trr_level": "TRR", "trades": "Trades", "pnl": "P&L"},
    "trading_exit_timing": {
        "strategy": "Strategy", "exit_timing": "Held", "trades": "Trades",
        "win_rate": "Win rate", "pnl": "P&L"},
    "trading_dte_bands": {
        "dte_at_entry": "DTE", "trades": "Trades", "win_rate": "Win rate",
        "avg_loss": "Avg loss", "pnl": "P&L"},
    "trading_trade_type": {"trade_type": "Type", "trades": "Trades", "pnl": "P&L"},
    "trading_recent_closed": {
        "sale_date": "Closed", "symbol": "Ticker", "strategy_type": "Strategy",
        "account_type": "Account", "quantity": "Size", "days_held": "Days held",
        "pnl": "P&L", "is_winner": "Won"},
    "trading_stance_accuracy": {
        "stance": "Stance", "trades": "Trades", "win_rate": "Win rate", "pnl": "P&L"},
    "trading_direction_accuracy": {
        "call": "Call", "trades": "Trades", "win_rate": "Win rate", "pnl": "P&L"},
    "trading_strike_accuracy": {
        "outcome": "Outcome", "trades": "Trades", "win_rate": "Win rate", "pnl": "P&L"},
    "trading_direction_strike_crosstab": {
        "scenario": "Scenario", "trades": "Trades", "win_rate": "Win rate", "pnl": "P&L"},
    "trading_risk_metrics": {
        "window_months": "Window (months)", "sharpe": "Sharpe",
        "sortino": "Sortino", "n_months": "Months"},
    "ops_last_run_per_job": {
        "job_name": "Job", "status": "Status", "updated_at": "Updated",
        "days_ago": "Days ago", "health": "Health"},
    "ops_days_since_success": {"job_name": "Job", "days_since_success": "Days"},
    "ops_failure_count": {"failures": "Failures (30d)"},
    "ops_history_coverage": {"newest_move_age": "Newest move", "tickers": "Tickers"},
    "ops_run_history": {"date": "Date", "job_name": "Job", "status": "Status"},
}


# Grafana distributes table width evenly across columns, which spreads six
# columns across a wide display leaving large dead gaps. Pinning the short,
# fixed-width columns hands the slack to the ones that need it. Only short
# columns are pinned; anything unpinned shares what is left.
COLUMN_WIDTHS = {
    "signal_implied_vs_actual_recent": {
        "earnings_date": 110, "ticker": 90, "implied_move_pct": 130,
        "actual_move_pct": 130, "overpricing_ratio": 130},
    "signal_recent_analyses": {
        "timestamp": 140, "ticker": 90, "earnings_date": 110, "vrp_close": 110,
        "vrp_intraday": 120, "graded_on": 110, "term_slope": 110, "source": 100},
    "trading_recent_closed": {
        "sale_date": 110, "symbol": 90, "strategy_type": 110, "account_type": 100,
        "quantity": 90, "days_held": 100, "pnl": 110, "is_winner": 80},
    "trading_trade_type": {"trade_type": 160, "trades": 110, "pnl": 130},
    "trading_risk_metrics": {
        "window_months": 160, "sharpe": 120, "sortino": 120, "n_months": 120},
    "ops_last_run_per_job": {
        "job_name": 220, "status": 110, "updated_at": 150,
        "days_ago": 100, "health": 100},
    "ops_run_history": {"date": 120, "job_name": 220, "status": 110},
}


# (query_name, title, panel_type, w, h, description, field_config_overrides)
SIGNAL_PANELS = [
    [
        ("signal_data_freshness", "Data freshness", "stat", 8, 5,
         "Age of the newest analysis_log row. Red past 48h — a stale dashboard "
         "read as current is the failure mode this panel exists to prevent.",
         # Per-field, not panel-level: the query's OTHER field is a date string,
         # which an hour unit + numeric threshold has no business touching.
         {"_overrides": [field_props_override(
             "hours_stale", unit="h",
             thresholds={"mode": "absolute", "steps": [
                 {"color": "green", "value": None}, {"color": "red", "value": 48}]})]}),
        ("signal_sample_sizes", "Sample sizes", "table", 16, 5,
         "Every count this dashboard reports, in one table — it replaced four "
         "stat panels that between them spent 18 of 24 columns on counts. The "
         "A/B row is APPROXIMATE: /ab-status remains authoritative, and this "
         "says when it is worth re-running that script, never what its verdict "
         "is.", {}),
    ],
    [
        ("signal_crush_by_vrp_tier", "Crush rate by VRP tier", "table", 8, 8,
         "Does the tier predict? Crushed = the implied straddle exceeded the "
         "absolute close move (research/events.py's definition verbatim). Split "
         "by the convention that graded each row and never pooled — close-VRP "
         "is mechanically smaller, so a shared threshold would compare units, "
         "not tiers. Close-to-close deployed 2026-09-16, so that row reads thin "
         "or absent for months by design.",
         {"_overrides": [field_props_override("crush_rate", unit="percent", decimals=1)]}),
        ("signal_vrp_tier_over_time", "VRP tier mix over time", "barchart", 16, 8,
         "Regime drift in what the scanner surfaces.",
         {"_overrides": [series_color_override(tier, color)
                          for tier, color in QUALITY_TIER_COLORS.items()]}),
    ],
    [
        ("signal_implied_vs_actual_tier", "Implied vs actual move", "piechart", 8, 8,
         "Was the market's implied move bigger or smaller than what actually "
         "happened (ranges: UNDERPRICED <1.0x, FAIR 1.0-1.5x, OVERPRICED "
         "1.5-3.0x, HEAVILY_OVERPRICED 3.0x+). Bucketed, not averaged: the raw "
         "ratio is extremely right-skewed (a near-zero actual move blows up the "
         "denominator) -- a mean or histogram would be dominated by outliers. "
         "UNDERPRICED is the dangerous tier for a premium seller: the market's "
         "move estimate was too small.",
         {"_overrides": [series_color_override(tier, color)
                          for tier, color in OVERPRICING_TIER_COLORS.items()]}),
        ("signal_term_slope_histogram", "Term-slope distribution", "barchart", 16, 8,
         "Observed range runs ~1.04-2.48. Makes the retired 1.10 cutoff visibly "
         "beneath the distribution floor. If the term-structure hypothesis ever "
         "passes, the deployed cutoff derives from THESE terciles (~1.38 / 1.56 "
         "on 218 rows, 2026-09-17), never from 1.10. Observational, not a gate: "
         "at n=58 the live split reads -4.2 pts, CI [-30.2, +24.6].", {}),
    ],
    [
        ("signal_implied_vs_actual_recent", "Implied vs actual (recent)", "table", 24, 10,
         "Per-event detail behind the tier chart above. Newest 50.", {}),
    ],
    [
        ("signal_recent_analyses", "Recent analyses", "table", 24, 10,
         "Newest 50. graded_on names the VRP convention that set the tier.", {}),
    ],
]

TRADING_PANELS = [
    [
        ("trading_journal_freshness", "Journal freshness", "stat", 9, 4,
         "CLOSED TRADES ONLY — there is no open-position tracker for the "
         "earnings system, so this is not current exposure. Advances only when "
         "/journal runs, independently of the signal dashboard's clock. Counts "
         "trades: one placed in both the IRA and TAXABLE is one.",
         dict(NEUTRAL_COLOR)),
        ("trading_banned_strategy_tripwire", "Banned strategies after ban", "stat", 5, 4,
         "Date-scoped. STRANGLE and IRON_CONDOR exist historically and always "
         "will; only post-ban rows indicate a generator regression. Must read 0.",
         {"thresholds": {"mode": "absolute", "steps": [
             {"color": "green", "value": None}, {"color": "red", "value": 1}]}}),
        ("trading_one_position_tripwire", "Second bets on one event", "stat", 5, 4,
         "§6: one position per ticker per earnings event — the rule a repair campaign "
         "on record broke. Post-2026-05-31 only: 15 events violate it "
         "all-time, 1 since. Each is a SPREAD and a SINGLE on the same event, "
         "same day. Should read 0.",
         {"thresholds": {"mode": "absolute", "steps": [
             {"color": "green", "value": None}, {"color": "red", "value": 1}]}}),
        ("trading_contract_cap_tripwire", "Over the 100-contract cap", "stat", 5, 4,
         "§6 rule 5: never exceed 100 contracts. Post-2026-05-31 only; reads 11 "
         "as of 2026-09-17, all single-name. ⚠️ A contract cap is not a risk "
         "cap — a high-priced single was 110 contracts at ~$3,090 each. Size on max loss.",
         {"thresholds": {"mode": "absolute", "steps": [
             {"color": "green", "value": None}, {"color": "red", "value": 1}]}}),
    ],
    [
        ("trading_monthly_pnl", "Monthly P&L by account", "barchart", 24, 9,
         "RAW CASH P&L, not wash-sale adjusted — Fidelity's CSV omits adjusted basis, "
         "so this will not tie to their UI. IRA and TAXABLE stay split: different tax "
         "treatment. Full-width: 26 months and growing (see the Win rate panel below "
         "for why) -- revisit with a rolling window once month count outgrows even "
         "this width.",
         {"unit": "currencyUSD", "decimals": 0}),
    ],
    [
        ("trading_win_rate_by_month", "Win rate by month", "barchart", 24, 9,
         "Closed trades only; a trade placed in both accounts counts once. Bar chart, "
         "not timeseries: 'month' is a text label "
         "(strftime %Y-%m), not a temporal field Grafana can plot on a time axis. "
         "Full-width, own row -- at half width this squeezed 26 months of labels "
         "unreadably. 'trades' is queried but hidden from the plot (visible on "
         "hover): it and win_rate share no common scale, so plotting both as bars "
         "on one axis would misrepresent one of them.",
         {"_overrides": [
             field_props_override("win_rate", unit="percent", decimals=1),
             field_props_override("trades", **{"custom.hideFrom": {
                 "viz": True, "legend": True, "tooltip": False}}),
         ]}),
    ],
    [
        ("trading_contract_buckets", "Contract-size buckets", "table", 12, 8,
         "Closed option trades by size, both accounts' contracts together for a trade "
         "placed in both (CLAUDE.md §6); STOCK is left out (shares). Every option "
         "trade, index and banned structures included -- not §6's earnings-trade "
         "table, which rule_evidence.py prints. Read from rule_evidence_snapshot "
         "since 2026-09-17, so the band boundaries ARE §6's rather than a second "
         "copy in SQL. Size was chosen trade by trade, so a band's P&L is not what "
         "size did.",
         {"_overrides": [
             field_props_override("pnl", unit="currencyUSD", decimals=0),
             field_props_override("avg_loss", unit="currencyUSD", decimals=0),
         ]}),
        ("trading_trr_at_entry", "TRR at entry", "table", 12, 8,
         "Tail-risk tier as of the entry date, one row per trade. Every trade, not "
         "§3's earnings-trade table (rule_evidence.py prints that). ⚠️ UNKNOWN is a "
         "TIER — fewer than 4 quarters of history — not an absence: until "
         "2026-09-17 this panel filtered those 65 of 300 positions away and wrote "
         "tail_risk.py's 2.5 / 1.5 ladder out in SQL. Both now arrive through "
         "rule_evidence_snapshot, so there is no ladder here to drift.",
         {"_overrides": [field_props_override("pnl", unit="currencyUSD", decimals=0)]}),
    ],
    [
        ("trading_exit_timing", "Exit timing", "table", 12, 8,
         "§8, the largest measured effect in the book: spreads out the next "
         "trading day win 90.1% (n=111) against 56.0% "
         "held two days or more (n=25). TRADING days, from "
         "market_calendar — positions.days_held is CALENDAR days, and a spread "
         "entered Friday and exited Monday would fall in the wrong band. Read "
         "from rule_evidence_snapshot for exactly that reason.",
         {"_overrides": [
             field_props_override("win_rate", unit="percent", decimals=1),
             field_props_override("pnl", unit="currencyUSD", decimals=0),
         ]}),
        ("trading_dte_bands", "DTE at entry", "table", 12, 8,
         "§5's evidence for the MIN_DTE = 3 floor. Win rates MATCH at 0-2 and "
         "3-5 (84.1% both); losses run about 43% larger at 0-2, so the case for "
         "the floor is loss severity, not win rate. Earnings trades only, "
         "matching §5's table.",
         {"_overrides": [
             field_props_override("win_rate", unit="percent", decimals=1),
             field_props_override("avg_loss", unit="currencyUSD", decimals=0),
             field_props_override("pnl", unit="currencyUSD", decimals=0),
         ]}),
    ],
    [
        ("trading_trade_type", "Trade type", "table", 24, 7,
         "One row per trade. REPAIR and ROLL are banned management actions; "
         "'unlabelled' means a writer skipped trade_type.",
         {"_overrides": [field_props_override("pnl", unit="currencyUSD", decimals=0)]}),
    ],
    [
        ("trading_recent_closed", "Recent closed trades", "table", 24, 10,
         "Newest 50 journal rows, one per account: a trade placed in both the IRA and "
         "TAXABLE shows twice.",
         {"_overrides": [field_props_override("pnl", unit="currencyUSD", decimals=0)]}),
    ],
    [
        ("trading_stance_accuracy", "Analysis accuracy: stance", "table", 8, 7,
         "Bullish = short a PUT, bearish = short a CALL. Short leg identified via "
         "the acquired>sale date inversion (Fidelity's sell-to-open convention), "
         "not proceeds>cost_basis -- that conflates direction with win/loss.",
         {"_overrides": [
             field_props_override("win_rate", unit="percent", decimals=1),
             field_props_override("pnl", unit="currencyUSD", decimals=0),
         ]}),
        ("trading_direction_accuracy", "Analysis accuracy: direction", "table", 8, 7,
         "Did the stock actually move the direction the position implied.",
         {"_overrides": [
             field_props_override("win_rate", unit="percent", decimals=1),
             field_props_override("pnl", unit="currencyUSD", decimals=0),
         ]}),
        ("trading_strike_accuracy", "Analysis accuracy: strike selection", "table", 8, 7,
         "Did price cross the short strike. Excludes ~4 rows (NFLX/CRWD/NOW) with "
         "an implausible strike/price ratio -- a likely historical_moves "
         "price-scale issue for those tickers, flagged not fixed.",
         {"_overrides": [
             field_props_override("win_rate", unit="percent", decimals=1),
             field_props_override("pnl", unit="currencyUSD", decimals=0),
         ]}),
    ],
    [
        ("trading_direction_strike_crosstab", "Direction vs. strike: which mattered more",
         "table", 24, 8,
         "The synthesis panel. Direction and strike are different questions (sign "
         "of the move vs. whether it crossed a specific level) -- their four "
         "combinations aren't logically forced, only empirically observed here.",
         {"_overrides": [
             field_props_override("win_rate", unit="percent", decimals=1),
             field_props_override("pnl", unit="currencyUSD", decimals=0),
         ]}),
    ],
    [
        ("trading_risk_metrics", "Sharpe / Sortino (trailing)", "table", 24, 6,
         "Book as traded, netted per docs/SHARPE_ANALYSIS_2026-09.md §2 -- NOT "
         "the raw unnetted P&L every other panel on this page uses (netting can "
         "flip the sign of a concentrated-equity position; see that doc's §3). "
         "Computed in Python (scripts/refresh_risk_metrics.py), not this "
         "query -- the netting rule isn't expressible as a plain SQL aggregate. "
         "Point estimates only, no confidence interval -- see "
         "docs/SHARPE_BOOTSTRAP_2026-09.md for that. 1- and 3-month windows are "
         "too few data points for either ratio to be statistically meaningful; "
         "read them as a trend line, not a claim.",
         {"_overrides": [
             field_props_override("sharpe", decimals=2),
             field_props_override("sortino", decimals=2),
         ]}),
    ],
]

OPS_PANELS = [
    [
        ("ops_last_run_per_job", "Last run per job", "table", 12, 5,
         "Red on error; amber on a 'running' row older than 1h (runner.py's own "
         "stale-claim threshold). Colored via the health column, not a numeric "
         "threshold on status/days_ago directly -- staleness depends on both fields "
         "jointly, which a single-field threshold cannot express.",
         {"_overrides": [{
             "matcher": {"id": "byName", "options": "health"},
             "properties": [
                 {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                 {"id": "mappings", "value": [{"type": "value", "options": {
                     "ERROR": {"color": "red", "index": 0},
                     "STALE": {"color": "orange", "index": 1},
                     "OK": {"color": "green", "index": 2},
                 }}]},
             ],
         }]}),
        ("ops_days_since_success", "Days since last success", "stat", 6, 5,
         "Per job. A silently missed day shows here first.", {}),
        ("ops_failure_count", "Job failures", "stat", 6, 5,
         "Total 'error' rows in job_status.",
         {"thresholds": {"mode": "absolute", "steps": [
             {"color": "green", "value": None}, {"color": "red", "value": 1}]}}),
    ],
    [
        ("ops_run_history", "Run history", "table", 14, 9,
         "Day x job. Job DURATION is deliberately absent: job_status has no duration "
         "column and the started_at schema change is deferred (spec prerequisite 7).", {}),
        ("ops_history_coverage", "Move-history coverage", "table", 10, 9,
         "Tickers by the age of their newest recorded earnings move — the "
         "history every VRP and TRR runs on. A distribution, not a threshold: "
         "the staleness rule lives in common/constants.py, and restating it "
         "here would duplicate it. 2026-09-17: 898 / 113 / 341 across the "
         "first three bands.", {}),
    ],
]

DASHBOARDS = {
    "signal.json": {
        "uid": "ivcrush-signal",
        "title": "IV Crush — Signal",
        "tags": ["ivcrush", "signal"],
        "panels": SIGNAL_PANELS,
    },
    "trading.json": {
        "uid": "ivcrush-trading",
        "title": "IV Crush — Trading & P&L",
        "tags": ["ivcrush", "trading"],
        "panels": TRADING_PANELS,
    },
    "operations.json": {
        "uid": "ivcrush-operations",
        "title": "IV Crush — Operations",
        "tags": ["ivcrush", "ops"],
        "panels": OPS_PANELS,
    },
}


# PieChart (and its siblings Stat/Gauge/BarGauge) inherit Grafana's shared
# SingleStatBaseOptions, whose reduceOptions.values defaults to false: every
# field gets collapsed to ONE value via reduceOptions.calcs (default
# "lastNotNull") before rendering. For a multi-row categorical query, that
# discards all but one row and, having no per-row string left to label it
# with, falls back to the field's own name ("n"). Symptom: one slice, labeled
# "n", instead of one slice per category. values: true turns that off: each
# row becomes its own value, auto-labeled from the adjacent string column
# (source, on the one piechart still in this long-format shape).
PIECHART_OPTIONS = {
    "reduceOptions": {"values": True, "calcs": [], "fields": ""},
    "pieType": "pie",
    # At w=8 a right-placed legend took roughly 40% of the panel, leaving the
    # pie constrained by the remainder; bottom gives it the full width.
    # displayLabels drops "name" because the legend already carries names --
    # drawing both duplicated every label on top of the slices.
    "displayLabels": ["percent"],
    "legend": {"displayMode": "list", "placement": "bottom", "values": []},
    "tooltip": {"mode": "single", "sort": "none"},
}

# The tier piechart (its query pivoted to wide format specifically so
# series_color_override's per-field coloring applies -- see that function's
# docstring) must NOT use the values:true long-format hack above: with wide
# format there is no per-row category string to label slices with, and
# values:true would try to reduce over ROWS (there's exactly one) instead of
# per FIELD. Grafana's own default reduceOptions (values:false, one value per
# numeric field via lastNotNull) is exactly PieChart's documented "multiple
# fields" mode, so this omits reduceOptions entirely rather than overriding it.
PIECHART_WIDE_QUERIES = {"signal_implied_vs_actual_tier"}

PIECHART_WIDE_OPTIONS = {
    "pieType": "pie",
    # At w=8 a right-placed legend took roughly 40% of the panel, leaving the
    # pie constrained by the remainder; bottom gives it the full width.
    # displayLabels drops "name" because the legend already carries names --
    # drawing both duplicated every label on top of the slices.
    "displayLabels": ["percent"],
    "legend": {"displayMode": "list", "placement": "bottom", "values": []},
    "tooltip": {"mode": "single", "sort": "none"},
}

# Stat's per-field reduction (reduceOptions.values: false, the Grafana
# default) filters to NUMERIC fields only when reduceOptions.fields is its
# default empty string -- confirmed live: Data freshness and Journal
# freshness each query one string field (a date) alongside one number, and
# the date silently never rendered, only the number. "/.*/' is Grafana's
# documented escape hatch: a field-name regex matching everything, which
# stops the implicit numeric-only filter so both fields render as separate
# stat tiles the way ops_last_run_per_job's non-stat multi-field panels
# already do it correctly.
STAT_ALL_FIELDS_QUERIES = {"signal_data_freshness", "trading_journal_freshness"}

STAT_ALL_FIELDS_OPTIONS = {"reduceOptions": {"values": False, "fields": "/.*/"}}

# Stat inherits the same SingleStatBaseOptions family as PieChart/Gauge/
# BarGauge, so it has the identical collapse bug: reduceOptions.values
# defaults to false, which reduces every field to ONE value via
# reduceOptions.calcs (default "lastNotNull") before rendering. Every other
# stat panel here queries a single aggregate row (COUNT(*), MAX(...)), so that
# default is correct and harmless -- these are the only ones whose query has a
# GROUP BY and returns one row per category, and need the same values: true
# fix, rendering one stat tile per row instead of silently collapsing to the
# last one.
STAT_MULTI_QUERIES = {"ops_days_since_success"}

STAT_MULTI_OPTIONS = {
    "reduceOptions": {"values": True, "calcs": [], "fields": ""},
    "orientation": "auto",
    "textMode": "auto",
    "colorMode": "value",
    "graphMode": "none",
    "justifyMode": "auto",
}

# BarChart's default xTickLabelRotation is 0 (horizontal), which crowds badly
# once there are more than a handful of categories. Every barchart panel here
# has a text category axis (week, month, slope bucket) that either grows
# without bound or already has enough distinct values to overlap at 0 degrees
# -- confirmed empirically: 16-26 categories per panel, none previously
# rotated. -45 gives much more horizontal density per label with no
# information loss, unlike trimming the query's date range.
BARCHART_OPTIONS = {"xTickLabelRotation": -45}


def _panel(pid, spec, queries, x, y):
    name, title, ptype, w, h, desc, fc = spec
    if name not in queries:
        raise SystemExit(f"panel '{title}' references unknown query '{name}'")
    sql = queries[name]
    fc = dict(fc)
    # Reserved key: per-field overrides (fieldConfig.overrides), for a panel
    # where only ONE column needs special treatment (e.g. value-mapped cell
    # coloring) rather than every field sharing the same defaults.
    overrides = fc.pop("_overrides", [])
    # Skip only a field whose own override ALREADY sets displayName -- not one
    # that merely has some other property. Guarding on the field alone dropped
    # the label from every field carrying a unit or threshold (hours_stale,
    # pnl, win_rate, sharpe...), which is most of the ones that needed it.
    # Grafana applies each byName entry in turn, so a separate displayName
    # override alongside a unit override is fine.
    named = {o["matcher"]["options"] for o in overrides
             if o.get("matcher", {}).get("id") == "byName"
             and any(pr.get("id") == "displayName" for pr in o.get("properties", []))}
    overrides = overrides + [
        field_props_override(col, displayName=label)
        for col, label in DISPLAY_NAMES.get(name, {}).items()
        if col not in named
    ]
    overrides = overrides + [
        field_props_override(col, **{"custom.width": px})
        for col, px in COLUMN_WIDTHS.get(name, {}).items()
    ]
    defaults = {"custom": {}}
    defaults.update(fc)
    return {
        "id": pid,
        "title": title,
        "type": ptype,
        "description": desc,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": DS,
        "targets": [{
            "refId": "A",
            "datasource": DS,
            # Grafana's first-party Postgres datasource, not frser-sqlite.
            # The two take completely different target keys -- frser reads
            # queryText/queryType, this one reads rawSql/format -- so a panel
            # carrying the old keys renders empty rather than erroring, which
            # is the same silent-blank failure mode the SQLite plugin had for
            # a missing column.
            "format": "table",
            "rawQuery": True,
            "rawSql": sql,
            "editorMode": "code",
        }],
        "fieldConfig": {"defaults": defaults, "overrides": overrides},
        "options": (
            dict(PIECHART_WIDE_OPTIONS) if name in PIECHART_WIDE_QUERIES
            else dict(PIECHART_OPTIONS) if ptype == "piechart"
            else dict(STAT_MULTI_OPTIONS) if name in STAT_MULTI_QUERIES
            else dict(STAT_ALL_FIELDS_OPTIONS) if name in STAT_ALL_FIELDS_QUERIES
            else dict(BARCHART_OPTIONS) if ptype == "barchart"
            else {}
        ),
    }


def build(name, spec, queries):
    """Lay rows out top to bottom, asserting each one is well-formed.

    spec["panels"] is a list OF ROWS, each row a list of panel tuples. The
    previous version packed a flat list left-to-right and wrapped at x+w > 24,
    which silently left dead columns whenever a row's widths did not sum to 24
    and vertical voids whenever a row mixed heights -- three short rows and one
    mixed-height row were live on 2026-09-17. Declaring rows makes both a build
    failure instead of something noticed in a screenshot months later.
    """
    panels, pid, y = [], 1, 0
    for r, row in enumerate(spec["panels"]):
        widths = [p[3] for p in row]
        heights = {p[4] for p in row}
        titles = ", ".join(p[1] for p in row)
        if sum(widths) != 24:
            raise SystemExit(
                f"{name} row {r}: widths sum to {sum(widths)}, must be 24 ({titles})")
        if len(heights) != 1:
            raise SystemExit(
                f"{name} row {r}: mixed heights {sorted(heights)} ({titles})")
        x = 0
        for p in row:
            panels.append(_panel(pid, p, queries, x, y))
            x += p[3]
            pid += 1
        y += heights.pop()
    return {
        "uid": spec["uid"],
        "title": spec["title"],
        "tags": spec["tags"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "5m",
        "time": {"from": "now-90d", "to": "now"},
        "panels": panels,
    }


def main() -> int:
    queries = load_queries(QUERIES)
    for fname, spec in DASHBOARDS.items():
        doc = build(fname, spec, queries)
        (OUT_DIR / fname).write_text(
            json.dumps(doc, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {fname}: {len(doc['panels'])} panels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
