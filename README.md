# Trading Desk

Production options trading system for one strategy: **IV Crush** — sell defined-risk spreads before earnings when implied volatility is elevated; profit from the volatility collapse after the announcement.

**Active strategies:** earnings spreads + 45 DTE non-earnings spreads (IRA active sleeve).
**Permanently banned (May 2026):** iron condors, strangles, CSPs/wheel, naked options — the wheel strategy was a lifetime net loser and is permanently retired.

**All-time validated (defined-risk only, May 2026):** 167 trades | 67% win rate

---

## Architecture

```
5.0 Cloud     — 24/7 Cloud Run + Telegram alerts
4.0 Sentiment — ARCHIVED May 2026 (sentiment tables migrated to ivcrush.db)
2.0 Core      — VRP math, strategy generation, shared library
──────────────────────────────────────────────────────────
ivcrush.db (2.0 + sentiment tables)
```

All subsystems share `2.0/venv/bin/python` and import 2.0 via `sys.path`. 5.0 never reimplements 2.0's math — it calls it. Shared logic lives in `common/` (enums, constants, direction, timezone, filters); `5.0/common/` is a shadow copy — update both when changing shared logic.

| Subsystem | Purpose | Tests |
|-----------|---------|------:|
| [2.0](2.0/) | Core VRP math, strategy generation, DB | 1,202 |
| [5.0](5.0/) | Cloud autopilot (Cloud Run + Telegram) | 545 |

Total: **1,747 active tests**. 4.0 (sentiment) and 6.0 (agent orchestration) are archived and not part of CI.

---

## Quick Start

```bash
# Single ticker analysis
cd 2.0/ && ./trade.sh NVDA 2026-02-10

# Scan all earnings on a date
cd 2.0/ && ./trade.sh scan 2026-02-10

# Find highest-VRP opportunities this week
cd 2.0/ && ./trade.sh whisper

# 45 DTE non-earnings premium candidates
cd 2.0/ && ./trade.sh harvest

# Cloud API
curl -H "X-API-Key: $KEY" https://your-service.a.run.app/api/whisper
```

---

## Claude Code Commands

22 slash commands for interactive analysis.

| Command | Purpose |
|---------|---------|
| `/analyze TICKER [--fresh-history]` | Deep dive — trade decision, strategies, sizing; `--fresh-history` adds multi-window VRP breakdown |
| `/council TICKER` | 7-source AI sentiment consensus (Perplexity + Finnhub + skew) |
| `/whisper` | Most anticipated earnings with VRP scoring |
| `/scan DATE` | Scan all earnings on a date |
| `/harvest` | 30–45 DTE non-earnings premium candidates (IV rank + IV/HV, no binary events) |
| `/ab-status` | Live forward-validation tracker (VRP-denominator + term-structure A/Bs) |
| `/prime` | Pre-cache sentiment for the upcoming week |
| `/alert` | Today's high-VRP trading alerts |
| `/history TICKER` | Historical earnings moves with pattern analysis |
| `/calendar [DATE]` | Weekly earnings calendar with TRR flags |
| `/pnl [PERIOD]` | P&L summary (week/month/ytd/year/quarter/N days) |
| `/positions [TICKER]` | Open positions and 30-day exposure |
| `/risk [DAYS]` | Portfolio risk (TRR, concentration, drawdown) |
| `/backtest [TICKER]` | Performance analysis from strategies DB |
| `/postmortem TICKER` | Predicted vs actual move analysis |
| `/backfill ARGS` | Record post-earnings outcomes for sentiment accuracy |
| `/collect TICKER` | Collect pre-earnings sentiment |
| `/journal FILE` | Parse Fidelity CSV/PDF into DB |
| `/export-report [MODE]` | Export to CSV/JSON |
| `/health` | System status (APIs, DB, cloud) |
| `/maintenance MODE` | sync, backup, backfill, track-iv, backfill-outcomes, cleanup, validate |
| `/deploy [FLAGS]` | Deploy 5.0 to Cloud Run |

---

## Directory Structure

```
Trading Desk/
├── 2.0/            Core math engine (VRP, liquidity, skew, strategies, DB)
├── 4.0/            AI sentiment layer — ARCHIVED May 2026
├── 5.0/            Cloud autopilot (FastAPI + Telegram + scheduled jobs)
├── archived/
│   └── 6.0/        Agent orchestration — ARCHIVED
├── common/         Shared code (enums, constants, direction, timezone, filters)
├── scripts/        Data pipeline scripts (journal import, backfill, sync)
└── docs/           Research docs, scan exports
```

---

## Databases

### ivcrush.db

`2.0/data/ivcrush.db` | GCS: `gs://your-gcs-bucket/ivcrush.db` | Schema v17

| Table | Rows (Jul 2026) | Notes |
|-------|-----:|-------|
| `historical_moves` | 13,489 | Post-earnings price movements; `ern_iv_effect` (10,780+ rows), `pre_earnings_straddle_pct` (10,800+ rows) — S&P500+MidCap400 universe, 1,353 tickers. `intraday_move_pct` = signed open→close (single convention since Jun 2026; consumers take abs()) |
| `earnings_calendar` | 23,093 | Upcoming and past earnings dates |
| `strategies` | 285 TAXABLE + 59 IRA | Normalized: acquired=open, sale=close. No inverted dates. |
| `trade_journal` | 794 TAXABLE + 258 IRA | Raw Fidelity legs/fills |
| `position_limits` | 431 | TRR + contract limits + ORATS snapshot (**frozen 2026-06-24** — ORATS retired) |
| `iv_history` | grows weekly | Weekly Tradier ATM IV snapshots for 9 harvest tickers; self-built IVR after 52 weeks (~June 2027) |
| `bias_predictions` | 74 | Tradier-only skew plus compound risk columns (`compound_risk_active`, `r_slp_30`, `fused_bias`, `trr_level`, `sizing_alarm`) |
| `analysis_log` | grows | Analysis snapshots — `implied_move_pct`, `vrp_ratio`, `vix_level`, `recommendation`; since Jun 2026 also `vrp_close_ratio` (gap-inclusive VRP live A/B) and `term_slope_ratio` (IV term-structure) |
| `sentiment_history` | 81 | Pre-earnings AI sentiment + post-earnings outcomes (migrated from 4.0 May 2026) |
| `sentiment_cache` | grows | 3-hour TTL sentiment cache (migrated from 4.0 May 2026) |
| `iv_log` | 16 | Legacy scan-time IV snapshots (Dec 2025) — superseded by `analysis_log` |

Empty placeholders: `cache`, `rate_limits`, `backtest_runs`, `backtest_trades`, `job_status`, `ticker_metadata`

**Known quirks:**
- `trade_journal` inverted dates (sale < acquired): Fidelity's convention for credit trades — not bugs
- `strategies` is normalized — no inverted dates
- `sync-cloud` merges by adding rows missing on either side — a row deleted only locally is resurrected from the GCS copy on the next sync; permanent deletions must overwrite the cloud copy directly

### sentiment_cache.db — ARCHIVED

`4.0/data/sentiment_cache.db` preserved for reference only. Both tables migrated to `ivcrush.db` (schema migration 015, May 2026).

---

## Data Sources

| Source | Role | Status |
|--------|------|--------|
| Tradier | Options chains, Greeks, IV, term structure, weekly IV snapshots | **Primary** |
| Alpha Vantage | Earnings calendar | Active |
| Finnhub | Analyst data + news (5.0 council), earnings calendar fallback | Active |
| Twelve Data | Historical prices (800 calls/day free tier) | Active |
| Perplexity | AI sentiment | Active |
| ORATS | Live IV / sizing signals | **RETIRED Jul 2026** — subscription ended; historical backfill data (`ern_iv_effect`, `pre_earnings_straddle_pct`) is permanent; `position_limits` snapshot frozen at 2026-06-24 |

```bash
TRADIER_API_KEY=xxx
ALPHA_VANTAGE_KEY=xxx
TWELVE_DATA_KEY=xxx
PERPLEXITY_API_KEY=xxx
FINNHUB_API_KEY=xxx
ORATS_API_KEY=xxx         # dead — kept for provenance only
ORATS_ENABLED=false       # retired Jul 2 2026
DB_PATH=data/ivcrush.db
```

GCS access requires: `gcloud auth application-default login`

---

## Testing

```bash
cd 2.0 && ./venv/bin/python -m pytest tests/ -v    # 1,202 tests
cd 5.0 && ../2.0/venv/bin/python -m pytest tests/  # 545 tests
# 4.0 and 6.0 archived — not part of active CI
```

---

## IV Crush Trade Rules (Summary)

Key thresholds — tune these based on your own backtesting:

| VRP Tier | Ratio | Action |
|----------|-------|--------|
| EXCELLENT | ≥1.8x | Full size |
| GOOD | ≥1.4x | Full size |
| MARGINAL | ≥1.2x | 50% size |
| SKIP | <1.2x | Do not trade |

| TRR Level | Ratio | Max Contracts |
|-----------|-------|:-------------:|
| LOW | <1.5x | 100 |
| NORMAL | 1.5–2.5x | 100 |
| HIGH | >2.5x | **50** |

**Strategy preference:** SPREAD (defined-risk, default on all earnings) > SINGLE (liquid large-caps only when spread OI is thin) | IRON CONDOR / STRANGLE / CSP / WHEEL permanently banned

**DTE minimum:** 3 days — no exceptions (0–2 DTE showed materially worse loss severity in backtesting; win rates were comparable across the two ranges)

**Sizing:** 50 contracts default, 100 max ever, one position per ticker per earnings event. Compound tail risk (≥2 of TRR HIGH / sizing alarm / bearish fused skew) → 25 contracts or skip.

**Exits:** spreads out next trading day; never roll (0% success); never repair (20% win rate).

---

*For research purposes only. Not financial advice. Options trading involves substantial risk.*
