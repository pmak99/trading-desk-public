# IV Crush 2.0 - Production-Grade System

This directory will contain the complete rewrite of the IV Crush trading system with enterprise-grade architecture.

## Status: 🟢 Foundation Complete (Session 1)

**Progress**: MVP Week 0 complete - Foundation scaffolding and skeleton in place

The 2.0 system is being built incrementally following:
- `docs/2.0_OVERVIEW.md` - System architecture and timeline
- `docs/2.0_IMPLEMENTATION.md` - Complete implementation guide
- `PROGRESS.md` - **Session-to-session progress tracking**

## Key Improvements Over 1.0

1. **Clean Architecture**: Domain-driven design with clear separation of concerns
2. **Production Resilience**: Circuit breakers, retry logic, correlation ID tracing
3. **Async Performance**: 20x faster processing with concurrent operations
4. **Type Safety**: Immutable domain types with Result[T, Error] pattern
5. **Comprehensive Testing**: 80%+ coverage with unit, integration, and performance tests
6. **Production Operations**: Health checks, hybrid caching, performance monitoring

## Timeline

- **Days 1-21**: MVP system (core metrics, API clients, basic infrastructure)
- **Days 22-28**: Phase 1 - Critical Resilience (async, retry, circuit breaker, health checks)
- **Days 29-35**: Phase 2 - Data Persistence & Operations (hybrid cache, config validation)
- **Days 36-42**: Phase 3 - Production Deployment (edge cases, load testing, runbook)
- **Days 43-46**: Phase 4 - Algorithmic Optimization (enhanced skew, consistency, interpolation)

**Total**: 46 days to production-ready system

## Quick Start

### Setup
```bash
# From project root
cd 2.0/

# Install dependencies
pip install -e .

# Install dev dependencies
pip install -e ".[dev]"

# Initialize database
python -c "from src.infrastructure.database.init_schema import init_database; from src.config.config import get_config; init_database(get_config().database.path)"

# Run tests
pytest tests/ -v
```

### Usage
```bash
# Analyze a ticker (requires TRADIER_API_KEY in .env)
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01
```

## Development

Implementation follows the detailed guide in `docs/2.0_IMPLEMENTATION.md` with:
- ✅ Dependency injection container
- ✅ Clean domain models (Money, Percentage, Strike, OptionChain, etc.)
- ✅ Repository pattern for data access
- ✅ Service layer for business logic (ImpliedMove, VRP calculators)
- ✅ Infrastructure layer for external integrations (Tradier API)
- ⏳ Full test coverage (in progress)

## Current Components

### Completed (Session 1)
- **Domain Layer**: Types, errors, protocols, enums
- **Config Layer**: Environment-based configuration with validation
- **Infrastructure**: API clients (Tradier), database schema, cache (memory)
- **Application**: Core metrics (ImpliedMove, VRP calculators)
- **Container**: Dependency injection with lazy loading
- **Testing**: Framework setup with basic unit tests

### Next Steps (Session 2+)
- Complete historical data backfill
- Implement enrichment metrics (Consistency, Skew, Term Structure)
- Add CLI scripts (scan, backfill)
- Increase test coverage to 80%+

---

**Note**: The existing 1.0 system is preserved in the `1.0/` directory.
