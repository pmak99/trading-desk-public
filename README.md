# Trading Desk

Production options trading for one strategy: **IV Crush** — sell defined-risk spreads before
earnings when implied volatility is inflated; exit after it collapses.

**Index and earnings trades, closed through 2026-09-11:** 223 trades · 82.5% win
**Permanently banned:** iron condors, strangles, CSPs/wheel, naked options, covered calls on
a concentrated long. The wheel was a lifetime net loser and is permanently retired.

---

## Layout

```
Trading Desk/
├── 2.0/        The engine — VRP math, strategy generation, DB layer, scheduled jobs
├── common/     Shared package: constants, timezone, db, pgtesting, weekly-options filter
├── scripts/    Tooling: journal import, dashboards, ops, the `db` shell helper
├── docs/       Research write-ups, ops scripts and systemd units, plans/specs
└── .claude/    Slash commands (instruction-only .md files, not Python)
```

**One codebase.** The 4.0 / 5.0 / 6.0 subsystems are gone from the working tree, each
recoverable at a pushed tag (`pre-4.0-removal`, `pre-5.0-removal`, `pre-6.0-removal`).
Nothing imports them; the tag is what keeps them around.

## Quick start

```bash
# once per venv: `common` (this directory) and `src` (2.0/) as editable packages
2.0/venv/bin/pip install -r requirements.txt && 2.0/venv/bin/pip install --no-deps -e . -e 2.0/

set -a && source 2.0/.env && set +a       # plain `source` does not export

cd 2.0/ && ./trade.sh NVDA 2026-02-10     # analyse one ticker
cd 2.0/ && ./trade.sh scan 2026-02-10     # all earnings on a date
cd 2.0/ && ./trade.sh whisper             # most anticipated this week
cd 2.0/ && ./trade.sh harvest             # 45 DTE non-earnings candidates
cd 2.0/ && ./trade.sh trr TICKER          # live tail-risk ratio
cd 2.0/ && ./trade.sh health
```

Ad-hoc SQL: `scripts/db -c "SELECT ..."` (sources `2.0/.env`, execs `psql`).

## Slash commands

22 instruction-only files in `.claude/commands/`. They reach the database through
`scripts/db` — there is no Python behind them.

| | | |
|---|---|---|
| `/analyze TICKER` | deep dive: VRP, liquidity, skew, term structure, strategies, sizing | `--fresh-history` adds 4Q/8Q windows |
| `/whisper` | most anticipated earnings, ranked by VRP | |
| `/scan DATE` | every earnings on a date | |
| `/harvest` | 30–45 DTE non-earnings premium | live IV/HV + index IVR |
| `/taco` | SPX/SPY macro-event index options | frozen exit refs |
| `/ab-status` | live forward-validation of one open A/B (term structure); the VRP denominator was migrated 2026-09 | |
| `/council TICKER` | multi-source AI sentiment consensus | |
| `/journal FILE` | parse a Fidelity CSV/PDF into the database | |
| `/pnl` `/backtest` | performance and exposure | |
| `/calendar` `/prime` `/collect` `/backfill` | data and prep | |
| `/health` `/maintenance MODE` `/export-report` | operations | |

⚠️ `/backtest` analyses **closed trades**. It is not a simulator.

## The database

**`db.internal:5432/ivcrush`** — Postgres 18.6 in Docker, 16 tables and one view, baseline
`2.0/sql/baseline_schema.sql`. Migrated from SQLite 2026-09-12.

There is **one** database. The Mac + GCS + server three-copy arrangement and the
`sync-cloud` union merge that reconciled it are gone, along with the class of bug where a
deleted row came back on the next sync.

| Table | Rows (2026-09-15) | |
|---|--:|---|
| `earnings_calendar` | 30,585 | upcoming and past earnings dates |
| `historical_moves` | 13,802 | post-earnings moves — the VRP input. S&P500 + MidCap400, 1,352 tickers |
| `analysis_log` | 1,310 | one row per analyze/scan/whisper; feeds the live A/B evidence |
| `trade_journal` | 1,275 | raw Fidelity legs and fills |
| `position_limits` | 431 | contract caps + the ORATS snapshot, **frozen 2026-06-24** |
| `strategies` | 310 | normalised closed trades, a row per account (226 TAXABLE + 84 IRA) |
| `positions` (view) | 300 | one row per trade: a trade placed in both accounts is merged, size and P&L summed |
| `iv_history` | 234 | weekly Tradier ATM IV; self-built IVR at 52 weeks (~Jun 2027) |
| `bias_predictions` | 123 | Tradier-only skew + compound-risk columns |
| `sentiment_history` | 95 | pre-earnings sentiment vs outcome |
| `taco_log` / `taco_positions` | 24 / 10 | TACO runs and positions with frozen entry refs |
| `risk_metrics_snapshot` | 4 | precomputed Sharpe/Sortino — `net_campaigns()` netting is not a SQL aggregate |
| `job_status` | grows | one row per job per day; the claim is an atomic `ON CONFLICT` upsert |
| `whisper_cache` | grows | shared L2 cache — the Mac and the server stop scraping the same endpoint twice |
| `sentiment_cache` | grows | 3-hour TTL |

**Cut at the migration**, not carried over: `cache`, `rate_limits`, `backtest_runs`,
`backtest_trades`, `ticker_metadata`, `iv_log`. No readers, no writers — but the old schema
loader recreated all six on every database it touched, which is why "dead" and "deleted"
were different things until then.

**Working with it:**

- Long-lived processes use `2.0/src/infrastructure/database/pg_pool.py`; one-shot CLI uses
  `common/db.py`. Neither takes a path.
- Rows are `namedtuple_row`: `row[0]` and `row.col` work, **`row['col']` raises**, and it is
  `row._asdict()`, not `dict(row)`.
- A naive `datetime` written to `timestamptz` is read in the **session** timezone (UTC),
  landing an ET wall clock four hours early. Use `common.timezone.now_et()`.
- Schema changes go through `MigrationManager` on the baseline. ⚠️ A `git pull` does not
  apply one — migrations run in the weekly maintenance step and at the start of each entry
  point that writes (`apply_migrations()`: the bot, the jobs, scan/analyze), never in
  `Container()`. An applied migration file is checksummed and must never be edited; write
  a new one.

**The only SQLite file left**, one per host: `2.0/data/ivcrush.db.pre-postgres-2026-09-12`,
frozen `444` as the cutover rollback artifact. `2.0/backups/` is empty on both hosts —
the server held six superseded pre-cutover snapshots there until 2026-09-12, because the
migration cleaned one host while a note claimed it had cleaned both.

## Operations

Everything scheduled runs on a small Linux host under systemd user timers (`Linger=yes`),
against this same engine. The units and the host-side scripts are operator
infrastructure and live outside this repository; what they run is all here.

| Unit | When | What |
|---|---|---|
| `trading-digest.timer` | 07:30 ET weekdays | Telegram morning digest |
| `trading-telegram-bot.service` | always on | inbound `/whisper`, `/analyze TICKER` |
| `trading-maintenance.timer` | Mon 03:00 UTC | pull + migrate + `/maintenance` + reaper + monthly restore-verify |
| `trading-pg-backup.timer` | 02:30 ET nightly | `pg_dump -Fc` → local + a backup host + Drive |

**Backups.** One dump, three destinations (local 7 days, backup host 30, Drive 90), nightly, each
with a manifest of per-table row counts and content digests — and verified by *restoring* into
a throwaway database monthly and matching every table against that manifest. A count alone
cannot see a changed value. `2.0/.env` is backed up
separately and encrypted to a public key, so the server cannot read what it uploads.

**Dashboards.** Grafana at `https://grafana.internal`, reading Postgres through a SELECT-only
role. `2.0/dashboards/*.json` are **generated** from `queries.sql` by
`python -m scripts.build_dashboards`, with every panel's SQL tested in CI. Hand edits are
silently overwritten.

**Monitoring.** Three Uptime Kuma monitors (digest freshness, Postgres reachability,
container liveness), each with a Telegram notification *and* a resend interval — a monitor
without both is decoration.

## Data sources

| Source | Role | Status |
|---|---|---|
| Tradier | chains, Greeks, IV, term structure, weekly IV snapshots | **primary** |
| Finnhub | earnings calendar (primary), analyst data, news | active |
| Twelve Data | historical prices, 800 calls/day free | active |
| Yahoo Finance | earnings-date corroboration and history | active |
| Perplexity | AI sentiment | active |
| ORATS | live IV and sizing signals | **retired Jul 2026** — snapshot frozen 2026-06-24; historical backfill columns are permanent |
| Alpha Vantage | earnings calendar | **dead** — zero code references |

Configuration: copy `2.0/.env.example` → `2.0/.env`. It is the only `.env`.

## Testing

```bash
set -a && source 2.0/.env && set +a
cd 2.0 && ./venv/bin/python -m pytest tests/ -q                                   # 1,532
cd "$(git rev-parse --show-toplevel)" && 2.0/venv/bin/python -m pytest scripts/tests/ tests/ -q   # 177
```

**1,709 active, 0 skipped**, across three roots — `2.0/tests/`, `scripts/tests/`, and
`tests/journal/`. The latter two live outside the engine tree and were each missed by CI at
some point; all three run there now. Database tests run against a real Postgres: `common/pgtesting.py`
builds a throwaway schema per test from the real baseline, so a test cannot pass against a
schema production does not have. ⚠️ CI has Postgres but an *empty* database — a test whose
precondition is data must skip on an empty table, not on an unreachable server.

---

*For research purposes only. Not financial advice. Options trading involves substantial risk.*
