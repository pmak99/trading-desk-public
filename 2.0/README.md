# 2.0 Core Math Engine

VRP calculations, strategy generation, and the shared database layer. Every other subsystem (4.0, 5.0, 6.0) imports from here — none of them reimplement 2.0's math.

## Quick Start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e .
cp .env.example .env   # fill in API keys

./trade.sh NVDA 2026-02-10      # Single ticker
./trade.sh scan 2026-02-10      # All earnings on date
./trade.sh whisper              # Most anticipated earnings
./trade.sh harvest              # 45 DTE non-earnings premium candidates
./trade.sh sync-cloud           # Sync DB to GCS + Google Drive backup
./trade.sh health               # API connectivity + DB integrity
```

## Commands

| Command | Description |
|---------|-------------|
| `./trade.sh TICKER DATE` | Full analysis: VRP, liquidity, skew, term structure, strategies, sizing |
| `./trade.sh scan DATE` | Scan all tickers with earnings on a date |
| `./trade.sh whisper [DATE]` | Most anticipated earnings (highest expected VRP) |
| `./trade.sh harvest` | 30–45 DTE non-earnings premium candidates (IV rank + IV/HV gates) |
| `./trade.sh sync-cloud` | Sync local DB with GCS + Google Drive backup |
| `./trade.sh health` | System health check (API connectivity, DB integrity) |

Harvest output uses the Python logger (timestamped lines) — strip the prefix with `sed`, never `grep -v "^2026"` (it swallows data rows).

## Architecture

Domain-Driven Design. Business logic has no dependency on infrastructure.

```
src/
├── config/                     Configuration and scoring parameters
│   ├── config.py               Environment-based config (VRP thresholds, Kelly, ScoringWeights)
│   ├── scoring_config.py       A/B scoring presets — NOT the live scoring path
│   └── validation.py           Config validation
├── domain/                     Core domain models, value objects, enums
│   ├── types.py                TickerAnalysis, ImpliedMove, VRPResult, SizingContext
│   ├── enums.py                Recommendation, DirectionalBias, EarningsTiming
│   ├── errors.py               Result[T,E] type, AppError, ErrorCode
│   ├── vix_regime.py           VIX regime definitions
│   └── scoring/                Score aggregation domain logic
├── application/
│   ├── metrics/                Analysis calculators
│   │   ├── vrp.py              VRP ratio: implied move / historical mean |move|
│   │   ├── implied_move.py     Implied move from ATM options (straddle method)
│   │   ├── term_structure.py   Front vs ~30d-out ATM IV (event-vol multiple)
│   │   ├── liquidity_scorer.py 4-tier liquidity (OI, spread, volume) + liquidity/
│   │   ├── skew_enhanced.py    Polynomial IV skew, 7-level DirectionalBias
│   │   ├── consistency_enhanced.py  Historical move consistency scoring
│   │   ├── market_conditions.py     VIX regime detection (15-min TTL cache)
│   │   └── adaptive_thresholds.py   VIX-adjusted VRP thresholds
│   ├── services/
│   │   ├── analyzer.py         Main orchestration: VRP → skew → sizing → strategies
│   │   ├── scorer.py           Strategy scoring (POP 40%, Liquidity 22%, VRP Edge 17%, Kelly 13%, Greeks 8%)
│   │   ├── strategy/           Strategy generation package (generator, sizing, spread_builder, strike_selection, bias, metrics)
│   │   ├── calendar_spread.py  Calendar spread PILOT builder (max 10 contracts)
│   │   ├── backtest/           Backtest engine package
│   │   └── health.py           Health check service
│   ├── async_metrics/          Async variants for concurrent scanning
│   ├── filters/                Candidate filters
│   └── handlers/               Request handlers
├── infrastructure/
│   ├── api/                    API clients (Tradier, Finnhub, yfinance)
│   ├── data_sources/           Historical price sources
│   ├── database/               SQLite layer
│   │   ├── connection_pool.py  Thread-safe connection pooling
│   │   ├── migrations/         MigrationManager (schema v17, runs on startup)
│   │   └── repositories/       Repository pattern (analysis, earnings, prices)
│   ├── cache/                  In-memory LRU + DB hybrid cache
│   └── monitoring/             Runtime monitoring
├── utils/                      concurrent_scanner, rate_limiter, circuit_breaker, retry, market_hours
└── container.py                Dependency injection container

scripts/scan/                   Live scan/whisper/harvest pipeline
├── workflows/                  scan_mode, whisper_mode, harvest_mode, ticker_mode, vix
├── quality_scorer.py           LIVE ticker score: VRP 55 pts, Liquidity 20, Move difficulty 25
├── filters.py                  Scan filters
└── constants.py                Scan constants (MEGACAP_CLUSTER, weights)
```

**Scoring paths (authoritative in code):**
- Ticker score for scan/whisper (LIVE): `scripts/scan/quality_scorer.py` — VRP 55 / Liquidity 20 / Move difficulty 25.
- Strategy score: `config.py ScoringWeights` — POP 40%, Liquidity 22%, VRP Edge 17%, Kelly Edge 13%, Greeks 8%.
- `scoring_config.py` presets are an A/B framework used only by tests — not production behavior.

## Analysis Pipeline

`TickerAnalyzer.analyze()` runs these steps in order:

1. Find nearest available option expiration
2. Calculate implied move (ATM straddle)
3. Fetch historical earnings moves (12 quarters)
4. Calculate VRP ratio + recommendation (also `vrp_close_ratio`, the gap-inclusive A/B variant)
5. Apply adaptive VIX-regime thresholds
6. Run polynomial IV skew analysis (Tradier)
7. Run historical consistency scoring
8. Load `position_limits` snapshot → build `SizingContext` (TRR + contract caps)
9. Fuse Tradier skew with `r_slp_30` → final `DirectionalBias` (ORATS disabled: Tradier `slope_atm` proxy at confidence 0.3)
10. Generate trade strategies (SINGLE + SPREAD)
11. Compute IV term structure (front vs ~30d-out ATM IV); when elevated tail risk coincides with ≥1.10x backwardation, build the calendar spread pilot candidate (max 10 contracts, defined risk)
12. Log to `analysis_log` — includes `vrp_close_ratio` and `term_slope_ratio` for live forward validation

## Sizing Rules

Applied in `SizingContext`, enforced by the strategy package:

| Signal | Rule | Status |
|--------|------|--------|
| TRR HIGH (>2.5x) | Hard cap: 50 contracts | Active |
| Compound tail risk (≥2 of TRR HIGH / sizing alarm / bearish fused skew) | 25 contracts or skip | Active |
| `fcst_ern_iv_effect` ≥ 2.0x (Rule A) | Reduce size 50% | **Inactive** — requires ORATS |
| `iee_earn_effect / fcst` ≥ 1.5x (Rule B) | Reduce size 50% | **Inactive** — requires ORATS |

Rules A and B never compounded — only one 50% IV cut. TRR cap stacks with either. While ORATS is disabled, sizing relies on VRP tier + TRR + compound-risk skew.

## Skew Fusion

`analyzer._fuse_skew_signals()` combines Tradier with `r_slp_30`:

```
fused = (tradier_numeric × tradier_conf + rslp_numeric × rslp_conf) / (tradier_conf + rslp_conf)
result = clamp(round(fused), -3, +3) → DirectionalBias
```

- With a live ORATS `r_slp_30`: confidence 0.5.
- ORATS disabled (current state): `compute_tradier_r_slp30_proxy(slope_atm)` substitutes at confidence 0.3 — direction preserved, magnitude softened. Proxy: `RSLP30_MEAN - (slope_atm / 150) × 2 × RSLP30_STD`.
- The fused result is **not persisted** — `bias_predictions` stores Tradier-only skew; the fused value is computed fresh each run.

## Term Structure & Calendar Pilot (Jun 2026)

`src/application/metrics/term_structure.py` compares front-expiry ATM IV to a ~30-day-out expiry (Tradier chains, no extra data cost). `slope_ratio` = front IV / back IV — the event-vol multiple. ≥1.30 = STEEP_BACKWARDATION (crush-favorable); ≤1.00 = FLAT_OR_CONTANGO (⚠️ high VRP here is persistent risk, not event risk). Observational: displayed in `/analyze` and logged to `analysis_log`, not yet a gate. Academic basis: Xie (Columbia) term-structure SLOPE factor.

`src/application/services/calendar_spread.py` builds the PILOT calendar candidate (sell earnings-week ATM, buy same strike ~30d out; put calendar on bearish fused skew) only when (TRR HIGH or sizing alarm) AND slope ≥1.10x. Hard cap 10 contracts (`PILOT_MAX_CONTRACTS`); max loss = net debit; exit next trading day. Displayed as a separate section, never auto-recommended over spreads.

Track both live A/Bs (VRP denominator + term structure) with `/ab-status`.

## ORATS — Disabled

`ORATS_ENABLED=false` by default. The `position_limits` ORATS snapshot is **frozen at 2026-06-24** — the staleness warnings in `/analyze`/`/harvest` mean "historical snapshot", not "run a refresh". Historical backfill data in `historical_moves` (`ern_iv_effect`, `pre_earnings_straddle_pct`) is permanent and unaffected.

| Script | Status |
|--------|--------|
| `refresh_orats_snapshots.py` | Inactive — last run 2026-06-24 |
| `fetch_orats_ticker.py` | Inactive — no-op while `ORATS_ENABLED=false` |
| `backfill_orats_straddle.py`, `backfill_orats_ivrank.py`, `backfill_orats_cores_extra.py` | Completed one-time backfills (May 2026); data permanent |
| `track_iv_weekly.py` | **ACTIVE** — Tradier, not ORATS (see below) |

These scripts are kept in the repo for when ORATS is re-enabled.

## IV History (self-built IVR)

`scripts/track_iv_weekly.py` snapshots near-30-DTE ATM IV from Tradier for the 9 harvest tickers (SPY/QQQ/IWM/AAPL/MSFT/NVDA/GOOGL/META/AMZN) into `iv_history` every Sunday (`/maintenance track-iv`). First snapshot 2026-06-15; self-built IV rank becomes computable after 52 weeks (~June 2027), replacing the frozen ORATS `iv_rank_1y` for the harvest gate.

## Scripts

| Script | Purpose |
|--------|---------|
| `track_iv_weekly.py` | Weekly Tradier ATM IV snapshot → `iv_history` (Sundays) |
| `expand_universe.py` | Add new tickers from S&P500+MidCap400 lists |
| `sync_earnings_calendar.py` | Refresh upcoming earnings from AlphaVantage |
| `store_bias_prediction.py` | Save a Tradier-only skew prediction to `bias_predictions` |
| `validate_bias_predictions.py` | Check prediction accuracy against outcomes |
| `backfill_historical.py` | Backfill historical moves for a ticker (signed open→close `intraday_move_pct`) |
| `ab_test_vrp_baseline.py` | VRP denominator A/B (intraday vs close-to-close baseline) |
| `ab_test_scoring_factors.py` | Scoring factor A/B backtests |
| `scoring_ab_test.py` | Compare scoring preset performance |
| `health_check.py` | Standalone health check (API + DB) |
| `scan.py` / `scan_async.py` | Scan pipeline entry points (called by `trade.sh`) |

## Database

`data/ivcrush.db` | Schema v17 | migrations run automatically on startup via `MigrationManager` (`src/infrastructure/database/migrations/`)

Key tables used directly by 2.0:

| Table | Purpose |
|-------|---------|
| `historical_moves` | Post-earnings moves; VRP calculation input. `intraday_move_pct` = signed open→close on the reaction day (single convention since Jun 2026 re-backfill; consumers take abs()) |
| `earnings_calendar` | Upcoming earnings dates |
| `position_limits` | TRR, contract caps, ORATS snapshot (frozen 2026-06-24) |
| `iv_history` | Weekly Tradier ATM IV snapshots for harvest IVR |
| `strategies` | Trade outcomes (normalized, no inverted dates; column is `expiration`, no `max_loss`) |
| `analysis_log` | Per-run snapshots written by every analyze/scan/whisper; includes `vrp_close_ratio` (migration 016) and `term_slope_ratio` (migration 017) |
| `bias_predictions` | Tradier-only skew predictions + compound risk columns (migrations 013–014) |

## Configuration

```bash
# Required
TRADIER_API_KEY=xxx       # Options chains (primary)
FINNHUB_API_KEY=xxx       # Earnings calendar fallback
DB_PATH=data/ivcrush.db

# Optional
VRP_THRESHOLD_MODE=BALANCED   # CONSERVATIVE | BALANCED | AGGRESSIVE
TWELVE_DATA_KEY=xxx           # Historical prices (800 calls/day free)
ALPHA_VANTAGE_KEY=xxx         # Earnings calendar

# Optional (disabled by default)
ORATS_API_KEY=xxx             # Kept for provenance
ORATS_ENABLED=false           # Branched logic preserved for future use
```

Source with `set -a && source .env && set +a` so Python subprocesses see the keys.

## How Other Subsystems Import 2.0

All subsystems use `sys.path` injection to access 2.0 as a shared library:

```python
import sys
sys.path.insert(0, "/path/to/2.0")
from src.container import get_container
```

**5.0** ports domain/application/infrastructure layers for cloud deployment.
**4.0** (archived) added a sentiment modifier on top of 2.0's VRP score.
**6.0** (archived) wrapped 2.0's container in `Container2_0`.

## Database Backup and Restore

`trade.sh sync-cloud` creates a timestamped local backup in `backups/` (30-day retention), pushes to `gs://your-gcs-bucket/ivcrush.db`, and syncs to Google Drive.

**Warning:** sync-cloud merges by adding rows missing on either side — a row deleted only locally is resurrected from GCS on the next sync. To delete permanently: delete locally, then `gsutil cp data/ivcrush.db gs://your-gcs-bucket/ivcrush.db`.

**Restore from local backup:**
```bash
cp data/ivcrush.db data/ivcrush.db.before-restore
cp backups/ivcrush_YYYYMMDD_HHMMSS_UTC.db data/ivcrush.db
sqlite3 data/ivcrush.db "PRAGMA integrity_check;"
```

**Restore from GCS:**
```bash
gsutil cp gs://your-gcs-bucket/ivcrush.db data/ivcrush.db
sqlite3 data/ivcrush.db "PRAGMA integrity_check;"
```

## Testing

```bash
./venv/bin/python -m pytest tests/ -v           # 1,202 tests
./venv/bin/python -m pytest tests/unit/ -v      # Unit tests
./venv/bin/python -m pytest tests/integration/  # Integration tests (requires live DB)
./venv/bin/python -m pytest tests/ --cov=src    # With coverage
```

---

*For research purposes only. Not financial advice.*
