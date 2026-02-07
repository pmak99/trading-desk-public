# Trading Desk

Production options trading system for **IV Crush** strategies - selling options before earnings when implied volatility is elevated, profiting from volatility collapse after announcements.

## Architecture

```
6.0 Agent Orchestration ──→ Parallel Claude Code agents for analysis
5.0 Cloud Autopilot     ──→ 24/7 Cloud Run + Telegram bot
4.0 AI Sentiment        ──→ Perplexity-powered sentiment layer
2.0 Core Math Engine    ──→ VRP/strategy calculations (shared library)
────────────────────────────────────────────────────────────────────
SQLite (ivcrush.db)     ──→ 15 tables, 6,861 historical moves
```

All subsystems import 2.0 as a shared library via `sys.path` injection. 4.0, 5.0, and 6.0 never duplicate 2.0's math.

| Subsystem | Purpose | Tests | Status |
|-----------|---------|------:|--------|
| [2.0](2.0/) | Core VRP math and strategy generation | 690 | Production |
| [4.0](4.0/) | AI sentiment layer (Perplexity) | 221 | Production |
| [5.0](5.0/) | Cloud autopilot (Cloud Run + Telegram) | 311 | Production |
| [6.0](6.0/) | Agent orchestration (parallel processing) | 48 | Production |

## Quick Start

```bash
# Local analysis (2.0)
cd 2.0/
./trade.sh NVDA 2026-02-10      # Single ticker
./trade.sh scan 2026-02-10      # All earnings on date
./trade.sh whisper              # Most anticipated earnings
./trade.sh health               # System health check

# Agent orchestration (6.0)
cd 6.0/
./agent.sh whisper              # Find opportunities (parallel)
./agent.sh analyze NVDA         # Deep dive

# Cloud API (5.0)
curl -H "X-API-Key: $KEY" https://your-cloud-run-url.run.app/api/whisper
```

## Claude Code Commands

All 13 slash commands for interactive analysis:

| Command | Purpose |
|---------|---------|
| `/whisper` | Find most anticipated earnings with VRP analysis |
| `/analyze TICKER` | Deep dive single ticker for trade decision |
| `/scan DATE` | Scan all earnings on a specific date |
| `/prime` | Pre-cache sentiment for the upcoming week |
| `/alert` | Today's high-VRP trading alerts |
| `/health` | System status (APIs, DB, budget) |
| `/maintenance MODE` | System maintenance (sync, backup, backfill, cleanup) |
| `/journal FILE` | Parse Fidelity CSV/PDF trade statements |
| `/backtest [TICKER]` | Performance analysis from strategies DB |
| `/history TICKER` | Historical earnings moves with pattern analysis |
| `/backfill ARGS` | Record post-earnings outcomes for sentiment accuracy |
| `/collect TICKER` | Collect and store pre-earnings sentiment |
| `/export-report [MODE]` | Export scan results, journal, or performance to CSV |

## Directory Structure

```
Trading Desk/
├── 2.0/            Core math engine (VRP, liquidity, strategies)
├── 4.0/            AI sentiment layer (Perplexity integration)
├── 5.0/            Cloud autopilot (FastAPI + Telegram)
├── 6.0/            Agent orchestration (parallel Claude Code)
├── scripts/        Root-level utilities and data pipelines
├── docs/           Research docs, trade records
├── mcp-servers/    Custom MCP server configs
└── .claude/        Claude Code commands and settings
```

## Databases

### ivcrush.db (2.0)

Primary database shared by all subsystems.

| Table | Records | Purpose |
|-------|--------:|---------|
| `historical_moves` | 6,861 | Post-earnings price movements for VRP |
| `earnings_calendar` | 6,762 | Upcoming and past earnings dates |
| `strategies` | 203 | Grouped trades with P&L tracking |
| `trade_journal` | 556 | Individual option legs |
| `position_limits` | 428 | TRR-based position sizing |
| `bias_predictions` | 28 | Directional bias predictions |

Plus: `analysis_log`, `cache`, `rate_limits`, `schema_migrations`, `backtest_runs`, `backtest_trades`, `iv_log`, `job_status`, `ticker_metadata`

**Locations:** `2.0/data/ivcrush.db` (local) | `gs://your-gcs-bucket/ivcrush.db` (cloud)

### sentiment_cache.db (4.0)

| Table | Records | Purpose |
|-------|--------:|---------|
| `sentiment_cache` | 0 | Short-lived sentiment cache (3hr TTL) |
| `api_budget` | 17 | Daily Perplexity API call tracking |
| `sentiment_history` | 27 | Permanent sentiment records for backtesting |

**Location:** `4.0/data/sentiment_cache.db`

## Environment Variables

```bash
TRADIER_API_KEY=xxx           # Options chains, Greeks, IV
ALPHA_VANTAGE_KEY=xxx         # Earnings calendar
TWELVE_DATA_KEY=xxx           # Historical prices
PERPLEXITY_API_KEY=xxx        # AI sentiment (4.0/5.0)
TELEGRAM_BOT_TOKEN=xxx        # Telegram notifications (5.0)
TELEGRAM_CHAT_ID=xxx          # Telegram chat (5.0)
```

## Testing

```bash
cd 2.0 && ./venv/bin/python -m pytest tests/ -v    # 690 tests
cd 4.0 && ../2.0/venv/bin/python -m pytest tests/  # 221 tests
cd 5.0 && ../2.0/venv/bin/python -m pytest tests/  # 311 tests
cd 6.0 && ../2.0/venv/bin/python -m pytest tests/  # 48 tests
```

Total: 1,270 tests across all subsystems.

## Scripts

Root-level utilities in `scripts/`:

| Script | Purpose |
|--------|---------|
| `parse_fidelity_csv.py` | Parse Fidelity CSV exports into trade_journal |
| `parse_trade_statements_v3.py` | Parse Fidelity PDF statements (legacy) |
| `export_scan_results.py` | Export scan output to CSV/JSON |
| `backtest_report.py` | Generate backtest performance reports |
| `backfill_strategies.py` | Backfill strategy trade_type/campaign fields |
| `backfill_earnings_dates.py` | Backfill missing earnings dates |
| `db_health_check.py` | Database integrity validation |
| `sync_databases.py` | Sync local and cloud databases |
| `strategy_grouper.py` | Group trade journal entries into strategies |
| `visualize_moves.py` | Historical earnings move visualization |

## Archived Systems

| Version | Status | Reason |
|---------|--------|--------|
| 1.0 | Deprecated | Superseded by 2.0's DDD architecture |
| 3.0 | Paused | ML direction prediction showed no edge |

---

**Disclaimer:** For research purposes only. Not financial advice. Options trading involves substantial risk.
