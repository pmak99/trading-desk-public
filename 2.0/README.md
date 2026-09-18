# 2.0 — the engine

VRP math, strategy generation, sizing, and the database layer. This is the only codebase;
"2.0" is now just the directory name.

Trading rules, sizing evidence, data caveats and incident lessons live in the
operator's own notes, which this repository does not carry. This file covers
**how the code is laid out and how to run it**.

## Setup

```bash
python3 -m venv venv && ./venv/bin/pip install -r ../requirements.txt
./venv/bin/pip install --no-deps -e .. -e .   # `common` (repo root) and `src` (here), editable
cp .env.example .env        # fill in TRADIER_API_KEY, FINNHUB_API_KEY, PG*
set -a && source .env && set +a
```

```bash
./trade.sh TICKER DATE      # full analysis
./trade.sh scan DATE        # every earnings on a date
./trade.sh whisper [DATE]   # most anticipated
./trade.sh harvest          # 30-45 DTE non-earnings candidates
./trade.sh trr TICKER ...   # live tail-risk ratio
./trade.sh health           # API connectivity + database
```

Harvest and scan write through the Python logger, so output carries timestamped prefixes.
Strip them with `sed`, never `grep -v "^2026"` — that swallows data rows too.

## Layout

Domain-driven: business logic has no dependency on infrastructure. Enforced since 2026-09-14 by
import-linter's contracts in the root `pyproject.toml` (CI runs `lint-imports`): application over
infrastructure over config over domain, so domain imports none of the other three and config imports
only domain; `common` imports nothing from `src`; neither imports anything from the `scripts` trees.

```
src/
├── config/
│   ├── config.py            AUTHORITATIVE thresholds, StrategyConfig, SIZING_* (from .env)
│   └── validation.py
├── domain/
│   ├── types.py             TickerAnalysis, ImpliedMove, VRPResult, SizingContext
│   ├── enums.py             Recommendation, DirectionalBias, EarningsTiming, StrategyType
│   ├── errors.py            Result[T,E], AppError, ErrorCode
│   ├── vix_regime.py        regime boundaries; market_conditions.py: the VIX level + regime value
│   ├── trading_expiration.py  calculate_expiration_date: the ONE trading expiration (min DTE)
│   ├── market_calendar.py   the ONE NYSE calendar: holidays, early closes, sessions, market status
│   └── scoring/             strategy_scorer.py: strategy score; weights.py: ScoringWeights (POP 40 / Liq 22 / VRP 17 / Kelly 13 / Greeks 8)
├── application/
│   ├── metrics/
│   │   ├── vrp.py                   implied move / mean |historical move|
│   │   ├── implied_move*.py         ATM straddle, plus the interpolated variant
│   │   ├── implied_move_expiration.py  the ONE expiration every path prices VRP at
│   │   ├── term_structure.py        front vs ~30d ATM IV — the event-vol multiple
│   │   ├── liquidity_scorer.py      4-tier liquidity (+ liquidity/)
│   │   ├── skew_enhanced.py         polynomial IV skew, 7-level DirectionalBias
│   │   ├── consistency_enhanced.py
│   │   └── market_conditions.py     VIX regime, 15-min TTL — display and analysis_log only
│   ├── services/
│   │   ├── analyzer.py              orchestration: VRP -> skew -> sizing -> strategies
│   │   ├── strategy/                generator, sizing, spread_builder, strike_selection, bias, metrics
│   │   ├── calendar_spread.py       pilot builder, hard cap 10 contracts
│   │   ├── earnings_date_validator.py
│   │   └── health.py
│   ├── formatters/
├── infrastructure/
│   ├── api/                 Tradier, Finnhub, yfinance
│   ├── data_sources/        Yahoo earnings dates, EarningsWhispers scraper
│   ├── database/
│   │   ├── pg_pool.py       pooled, liveness-checked connections
│   │   ├── init_schema.py
│   │   ├── migrations/      MigrationManager, on top of sql/baseline_schema.sql
│   │   └── repositories/    analysis, earnings, prices
│   ├── cache/               HybridCache: L1 memory + L2 Postgres whisper_cache
│   ├── notifications/
├── utils/                   concurrent_scanner, rate_limiter, serialization
└── container.py             DI container; migrates nothing (entry points call apply_migrations())

scripts/scan/                the live scan/whisper/harvest pipeline
├── workflows/               scan_mode, whisper_mode, harvest_mode, ticker_mode, vix
├── quality_scorer.py        LIVE ticker score: VRP 55 / liquidity 20 / move difficulty 25
├── harvest_live.py          Phase 1 — live IV/HV/IVR, reads no frozen snapshot
├── market_data.py           chain validation
└── constants.py             universe, MEGACAP_CLUSTER, weights
```

**Which scorer is live** — this trips people up:

| | Where | Weights |
|---|---|---|
| Ticker (scan/whisper) | `scripts/scan/quality_scorer.py` | VRP 55 pts (continuous, uncapped), liquidity 20, move difficulty 25 |
| Strategy | `src/domain/scoring/weights.py ScoringWeights` | POP 40% · liquidity 22% · VRP edge 17% · Kelly 13% · Greeks 8% |

## Analysis pipeline

`TickerAnalyzer.analyze()`:

1. Trading expiration — the caller computes it with `calculate_expiration_date`, which enforces
   the 3-DTE minimum (**every path must route through it**); snapped to a listed expiration
2. Implied move at `implied_move_expiration()` — the first listed expiration containing the
   reaction (BMO: the report date; otherwise the next day). Scan, whisper and the bot use the
   same function; pricing at the trading expiration inflated /analyze's VRP until 2026-09-14
3. Historical moves (up to 12 quarters)
4. VRP ratio (`vrp_close_ratio`, close-to-close, graded since the 2026-09 migration) +
   recommendation; `vrp_intraday_ratio` is logged alongside it as a reference only, never graded.
   No VIX adjustment on any path (a pre-registered test could not support one, 2026-09-14)
5. Polynomial IV skew from Tradier
6. Historical consistency
7. `position_limits` → `SizingContext` (TRR + caps)
8. Fuse Tradier skew with `r_slp_30` → final `DirectionalBias`
9. Generate strategies (SINGLE + SPREAD) on the trading expiration
10. Term structure; when elevated tail risk meets ≥1.10 backwardation, add the calendar pilot
11. Log to `analysis_log`, including `vrp_close_ratio` (graded), `vrp_intraday_ratio` (reference),
    `vrp_convention` and `term_slope_ratio`

## Sizing

Enforced by `SizingContext`, tunable through `SIZING_*` in `.env`
(`SizingContext.from_strategy_config()`).

| Signal | Effect | |
|---|---|---|
| Compound tail risk (≥2 of TRR HIGH / sizing alarm / bearish fused skew) | 25 contracts, or skip | active |
| TRR HIGH (>2.5) | hard cap 50 | active |
| MARGINAL VRP or WARNING liquidity | one 50% cut | active |
| Base cap | 100, never exceeded | active |
| `fcst_ern_iv_effect` ≥ 2.0 (Rule A) · `iee/fcst` ≥ 1.5 (Rule B) | 50% cut | **dormant** — ORATS input died Jul 2026 |

Rules A and B never compounded — one IV cut only, and the TRR cap stacks with it.

⚠️ `MAX_CONTRACTS` binds **only** on the fallback path where no `SizingContext` is passed.
`SIZING_DEFAULT_CAP` (50) is the knob that moves no-flag sizing; `SIZING_BASE_CAP` (100) only
bounds it. And a contract cap is not a risk cap —
`MAX_LOSS_WARN_THRESHOLD` ($50,000) holds every engine size to a dollar figure of worst-case
(natural-price) max loss and warns above it; it blocks nothing sized by hand. Size on max loss.

## Skew fusion

`analyzer._fuse_skew_signals()`:

```
fused  = (tradier_numeric × tradier_conf + rslp_numeric × rslp_conf) / (tradier_conf + rslp_conf)
result = clamp(round(fused), -3, +3) → DirectionalBias
```

The formula lives in `metrics/skew_enhanced.fuse_skew_bias`. The only independent second
source is ORATS `r_slp_30` (confidence 0.5). Post-ORATS there is nothing to fuse and the
Tradier bias passes through unchanged. Until Sep 2026 a `slope_atm`-derived proxy was
blended in at confidence 0.3; it was the same fit counted twice and could only move the
reading, so it was deleted. ⚠️ **Single-source** — the compound-risk calibration from the
two-source era may not transfer.

The Tradier reading is a quadratic fit of OTM IV against delta (10Δ–50Δ, at least 5 points,
both wings reaching 25Δ): `skew_atm` is the 25Δ risk reversal as a percent of ATM IV
(positive = puts richer), and confidence is the fit's adjusted R². The classification levels
(5/10/20%) are provisional until calibrated against outcomes.

`bias_predictions` stores both: `directional_bias` is the Tradier reading and `fused_bias`
the fused one (written by `scripts/store_bias_prediction.py` through the same
`fuse_skew_bias`; every row since 2026-06-03 carries it). Since 2026-09-14 every analysis
path writes it — scan, whisper, ticker mode, the Telegram bot and the morning digest
reuse their own skew reading, `./trade.sh TICKER DATE` runs the script for DATE — and it keeps one row
per event: the first, taken before the reaction session opens, for an event in
`earnings_calendar`.

## Term structure and the calendar pilot

`metrics/term_structure.py` compares front-expiry ATM IV to a ~30-day-out expiry from the
same Tradier chains. `slope_ratio` = front / back: ≥1.30 is event-concentrated (most room
for crush), ≤1.00 means the elevated IV is **persistent risk, not event risk** — a high VRP
there will not crush.

Observational, logged to `analysis_log`, not a gate. `services/calendar_spread.py` builds the
pilot only when (TRR HIGH or sizing alarm) **and** slope ≥1.10: sell earnings-week ATM, buy
the same strike ~30d out, puts on bearish skew. `PILOT_MAX_CONTRACTS = 10`, halved by the
spreads' one 50% cut (a WARNING leg — both legs are graded — MARGINAL VRP), and no pilot on a
REJECT leg; max loss = net debit, exit next trading day. Shown as a separate section, never
auto-recommended over a spread. Track the live A/B evidence with `/ab-status`.

## Database

`db.internal:5432/ivcrush` — Postgres 18.6. Nothing takes a path; everything reads `PG*`
from `.env`. Long-lived processes use `infrastructure/database/pg_pool.py`; one-shot CLI uses
`common/db.py`; ad-hoc SQL uses `scripts/db` from the repo root.

Schema is **version 6**; migration 5 added the `positions` view, one row per trade where
`strategies` holds one per account. `sql/baseline_schema.sql` is the *current* schema — every test
fixture is built from it — and each change is a numbered file in `sql/migrations/` plus the
same edit to the baseline plus `BASELINE_VERSION` in `migration_manager.py`, in one commit
(a test fails otherwise). ⚠️ Migrations run in the weekly maintenance step and in
`apply_migrations()`, which each entry point that writes calls once before its work — the
Telegram bot at start, `morning_digest`, `record_moves --execute`, `scan.py`, `analyze.py`,
`store_bias_prediction.py` — and **a `git pull` applies nothing**. Constructing a `Container()`
touches no database (since 2026-09-14), so a test that builds one leaves `PGDATABASE` alone.
⚠️ **An applied migration file is never edited**: its sha256 is stored when it is applied,
and every `migrate()` (so every entry point's `apply_migrations()`) refuses to run while a
file differs. Two migrators at
once serialise on an advisory lock; there is no rollback — undo with a new migration.

Rows come back as `namedtuple_row`: `row[0]` and `row.col` work, `row['col']` raises, and it
is `row._asdict()` rather than `dict(row)`. Money columns are `numeric(14,2)` but arrive as
`float` (`common.db.configure_types`); flags are `boolean`, so bind `True`/`False`, never
`1`/`0`; `historical_moves`' three move percentages are generated from the prices and
cannot be written.

**Backup and restore:**

| | |
|---|---|
| host-side `pg-backup` (outside this repo) | nightly `pg_dump -Fc` → local + a backup host + Drive |
| host-side `pg-restore-verify` (outside this repo) | monthly proof: restore into a throwaway database, match every table's count and content digest against the dump's manifest |
| `scripts/restore_database.sh` | the one you reach for when you actually need data back |

`restore_database.sh` **defaults to a side-by-side database** (`ivcrush_restored`) and
leaves production alone; overwriting live `ivcrush` needs `--into-live` plus a typed
confirmation. `--list` shows what dumps are available. It refuses `ivcrush_test`, where the
pg-marked tests build their schemas, and `ivcrush_scratch`, a working copy people use. Run as
`trading` it reports one harmless error: the `pg_stat_statements` extension needs the
superuser, and a restored copy works without it.

## Caching

`HybridCache` is two tiers with two clocks, deliberately: L1 is in-process memory (elapsed
time, 30s), L2 is the Postgres `whisper_cache` table (absolute `expires_at`, written by
whichever host stored it). That is what makes it shareable — a row the server writes expires at
the same instant for the Mac, which halves the traffic against the undocumented
EarningsWhispers endpoint.

Every L2 key carries `CACHE_VERSION` (`v1:whisper_tickers:2026-09-14`): hosts on different
revisions share the table, so a payload or serialization change bumps it and old rows read as
misses. A week's EarningsWhispers list is cached for the week; a database fallback list for an
hour.

`MemoryCache` fronts Tradier (`CachedOptionsDataProvider`) with a TTL per entry and LRU
eviction: quotes 30 s, chains 60 s, expirations 1 h. A cached quote is injected into the next
chain fetch as its spot, which prices the straddle and places the strikes, so its TTL is the
most the spot can lag the option quotes. It is read after the chain's rate-limit slot is
taken (`TradierAPI.admit()`), so the limiter's wait cannot age it; with no fresh quote left the
slot buys one (since 2026-09-14). `CACHE_L1_TTL` is only the default for an entry stored
without one (until 2026-09-14 it overrode them all).

Serialization is JSON with `__type__` tags, never pickle, and reconstruction is limited to
whitelisted domain types. A non-finite float is not cacheable in L2 — `jsonb` is a strict
parser — so it is logged and skipped; L1 still holds it. **Any L2 failure degrades to
L1-only and never raises**, since L2 is now a network hop.

## Rate limiting

All Tradier calls in a process share **one** sliding-window limiter (at most 120 calls in any
60 seconds; `TRADIER_PER_MINUTE`) that waits up to 120s at the HTTP call site, and the client
retries 429 and 5xx responses, waiting for Retry-After or Tradier's `X-Ratelimit-Expiry` —
except an option chain, which it sends once: a 429 (after the window), a 5xx or failed
connection (after Retry-After or 2 s) or a timeout comes back with `AppError.retryable`, and
`CachedOptionsDataProvider` re-reads the quote on a new slot and resends it, at most
`RESENDS_PER_CHAIN` (3) times, instead of the session resending the chain on its old spot.
Tradier counts per minute from the first request, which a token bucket cannot respect — the
old one (burst 150) admitted ~270 calls a minute.

⚠️ **Do not add a second throttle layer.** A per-ticker gate in `ConcurrentScanner` was
removed precisely because it stacked on the bucket, throttling dispatch rather than request
rate, and silently dropped tickers under load. Scan code must fetch through
`container.cached_options_provider` (chains 60s, expirations 1h, prices 30s), never raw
`container.tradier` — raw call sites were the source of 2–3× duplicate calls.

## ORATS — retired Jul 2026

Subscription ended ~Jun 25 2026; the key died Jul 2. `ORATS_ENABLED=false`, and unset is
false too since 2026-09-14 (`config.api.orats_enabled`, the one reader). The
`position_limits` snapshot is **frozen at 2026-06-24**, so `/analyze`'s staleness warning
means "historical snapshot", not "refresh me". Historical backfill columns in
`historical_moves` (`ern_iv_effect`, `pre_earnings_straddle_pct`) are permanent.

Still live and unaffected: skew fusion via the Tradier proxy; `/harvest` Phase 1 (live
IV30 from Tradier, HV20 from yfinance, TRR from `historical_moves`, real CBOE index IVR);
`scripts/track_iv_weekly.py`, which is Tradier, not ORATS.

⚠️ **Single-name IVR has no live source** until `iv_history` matures (~Jun 2027). Those names
show `n/a` in `/harvest` and pass on IV/HV + TRR + earnings only — a real gap in a hard gate,
so keep the Sunday `/maintenance track-iv` job running.

The refresh/fetch scripts were deleted; the one-time backfills are done and their data is
permanent.

## Scripts

| | |
|---|---|
| `track_iv_weekly.py` | Sunday Tradier ATM IV snapshot → `iv_history`. The self-built IVR. |
| `backfill_historical.py` | Backfill moves from Twelve Data (signed open→close). The only path for new tickers. |
| `sync_earnings_calendar.py` | Refresh upcoming earnings. Chunked Finnhub bulk fetch cross-validated against Yahoo; a write that would **change** a confirmed date needs both sources to agree or it is flagged and skipped. |
| `cleanup_duplicate_moves.py` | Dedupe adjacent-date rows. Auto-resolves only identical-value pairs; everything else is reported ambiguous. ⚠️ Audit at `<=15` days, and never widen the scan window without tightening the 90-day quarter window — real quarters are ~90 days apart. |
| `resolve_ambiguous_moves.py` | Resolves what the above refuses: Yahoo `get_earnings_date_near()` is the authority, stored price bars are a **veto**, not a second vote. |
| `compute_live_trr.py` | Live TRR, identical methodology to the engine. Behind `./trade.sh trr`. |
| `refresh_risk_metrics.py` (in `../scripts/`) | Weekly Sharpe/Sortino → `risk_metrics_snapshot`. |
| `ab_evaluate_live.py` | The live A/B criteria. Behind `/ab-status`. Rank-based, not threshold-based. |
| `research/events.py` | The one shared event core for offline research — point-in-time by construction. |
| `migrate_to_postgres.py`, `generate_pg_ddl.py` | The 2026-09-12 migration. Kept as provenance. |

⚠️ Research figures are a **short-straddle proxy net of costs**, not spread P&L — no
historical option chains are stored. Conservative (a straddle's loss is uncapped), but never
cite them as what the strategy earned. Results: [`../docs/RESEARCH_RESULTS_2026-09.md`](../docs/RESEARCH_RESULTS_2026-09.md).

## Testing

```bash
set -a && source .env && set +a
./venv/bin/python -m pytest tests/ -q                     # 2,545, 0 skipped
./venv/bin/python -m pytest tests/unit/ -q
./venv/bin/python -m pytest tests/integration/ -q
```

Database tests run against a **real Postgres**. The `pg_schema` fixture
(`common/pgtesting.py`) creates a throwaway schema per test **in the `ivcrush_test`
database**, never in production, and builds its tables by extracting CREATE statements from
`sql/baseline_schema.sql` — so a test cannot pass against a schema production does not have.

---

*For research purposes only. Not financial advice.*
