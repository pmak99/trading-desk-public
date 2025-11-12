# IV Crush 2.0 - Operational Runbook

**Version:** 2.0
**Last Updated:** 2025-11-12
**Purpose:** Day-to-day operations and common tasks

This runbook covers routine operational tasks, monitoring, and troubleshooting for IV Crush 2.0.

---

## Table of Contents

1. [Daily Operations](#daily-operations)
2. [Common Tasks](#common-tasks)
3. [Monitoring and Alerts](#monitoring-and-alerts)
4. [Performance Tuning](#performance-tuning)
5. [Troubleshooting Guide](#troubleshooting-guide)
6. [Maintenance Procedures](#maintenance-procedures)
7. [Emergency Procedures](#emergency-procedures)

---

## Daily Operations

### Morning Checklist

```bash
# 1. Check system health
python scripts/health_check.py

# 2. Review overnight logs
tail -100 logs/ivcrush.log | grep -E "ERROR|WARNING"

# 3. Check database size
du -h data/ivcrush.db

# 4. Verify cache performance
python -c "from src.container import Container; c = Container(); print(c.hybrid_cache().stats())"
```

### Pre-Market Analysis (Before 9:30 AM ET)

```bash
# Analyze tickers with earnings today
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01

# Bulk analysis
python scripts/analyze.py --file tickers_earnings_today.txt
```

### Post-Market Review (After 4:00 PM ET)

```bash
# Review performance metrics
python -c "from src.utils.performance import get_monitor; m = get_monitor(); import json; print(json.dumps(m.get_all_stats(), indent=2))"

# Check for slow operations
python -c "from src.utils.performance import get_monitor; m = get_monitor(); slow = m.get_slow_operations(); print(f'Slow ops: {len(slow)}')"

# Archive today's logs
DATE=$(date +%Y%m%d)
cp logs/ivcrush.log logs/archive/ivcrush_$DATE.log
```

---

## Common Tasks

### Analyzing a Single Ticker

```bash
# Basic analysis
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01

# With verbose output
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01 --verbose

# Save output to file
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01 > results/aapl_analysis.json
```

### Bulk Ticker Analysis

```bash
# Create ticker list file
cat > tickers.txt << EOF
AAPL
MSFT
GOOGL
TSLA
META
EOF

# Analyze all tickers
python scripts/analyze.py --file tickers.txt --output results/bulk_analysis.json

# Analyze with concurrency limit
python scripts/analyze.py --file tickers.txt --max-concurrent 10
```

### Backfilling Historical Data

```bash
# Backfill 90 days for specific tickers
python scripts/backfill.py --tickers AAPL,MSFT --days 90

# Backfill from file
python scripts/backfill.py --file tickers.txt --days 90

# Check backfill progress
python -c "from src.infrastructure.database.repositories.prices_repository import PricesRepository; repo = PricesRepository('./data/ivcrush.db'); count = repo.count_historical_moves('AAPL'); print(f'AAPL moves: {count}')"
```

### Cache Management

```bash
# View cache statistics
python -c "from src.container import Container; c = Container(); stats = c.hybrid_cache().stats(); print(f'L1: {stats[\"l1_size\"]} items, L2: {stats[\"l2_size\"]} items')"

# Clear L1 cache (memory)
python -c "from src.container import Container; c = Container(); c.hybrid_cache().clear_l1(); print('L1 cache cleared')"

# Clear all cache
python -c "from src.container import Container; c = Container(); c.hybrid_cache().clear(); print('All cache cleared')"

# Clean expired L2 entries
python -c "from src.container import Container; c = Container(); c.hybrid_cache().cleanup_expired(); print('Expired entries cleaned')"
```

### Database Operations

```bash
# Check database integrity
sqlite3 data/ivcrush.db "PRAGMA integrity_check;"

# View table sizes
sqlite3 data/ivcrush.db "SELECT name, (SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=m.name) as row_count FROM sqlite_master m WHERE type='table';"

# Optimize database
sqlite3 data/ivcrush.db "VACUUM;"

# Export historical data
sqlite3 data/ivcrush.db "SELECT * FROM historical_moves WHERE ticker='AAPL';" > aapl_history.csv
```

---

## Monitoring and Alerts

### Key Performance Indicators (KPIs)

#### 1. Response Time

```bash
# Check average response time
python -c "from src.utils.performance import get_monitor; m = get_monitor(); stats = m.get_stats('ImpliedMoveCalculator.calculate'); print(f'Avg: {stats.get(\"avg\", 0):.3f}s')"

# Alert threshold: >2.0s average
```

#### 2. Error Rate

```bash
# Count errors in last hour
grep "ERROR" logs/ivcrush.log | grep "$(date +%Y-%m-%d\ %H)" | wc -l

# Alert threshold: >10 errors/hour
```

#### 3. Cache Hit Rate

```bash
# Calculate cache hit rate
python -c "
from src.container import Container
c = Container()
stats = c.hybrid_cache().stats()
l1_hits = stats.get('l1_hits', 0)
l1_misses = stats.get('l1_misses', 0)
total = l1_hits + l1_misses
hit_rate = (l1_hits / total * 100) if total > 0 else 0
print(f'L1 hit rate: {hit_rate:.1f}%')
"

# Alert threshold: <70%
```

#### 4. API Rate Limit Usage

```bash
# Check rate limit warnings
grep "rate limit" logs/ivcrush.log | tail -10

# Alert threshold: Any rate limit warnings
```

### Automated Monitoring Setup

Create monitoring script (`scripts/monitor.sh`):

```bash
#!/bin/bash

LOG_FILE="logs/monitor.log"
DATE=$(date "+%Y-%m-%d %H:%M:%S")

# Check health
HEALTH=$(python scripts/health_check.py 2>&1)
if [[ $? -ne 0 ]]; then
    echo "$DATE - ALERT: Health check failed" >> $LOG_FILE
    # Send alert (email, Slack, etc.)
fi

# Check error rate
ERROR_COUNT=$(grep "ERROR" logs/ivcrush.log | grep "$(date +%Y-%m-%d\ %H)" | wc -l)
if [[ $ERROR_COUNT -gt 10 ]]; then
    echo "$DATE - ALERT: High error rate ($ERROR_COUNT errors/hour)" >> $LOG_FILE
fi

# Check cache performance
CACHE_STATS=$(python -c "from src.container import Container; c = Container(); print(c.hybrid_cache().stats())")
echo "$DATE - Cache stats: $CACHE_STATS" >> $LOG_FILE
```

Schedule in crontab:

```bash
*/15 * * * * /home/ivcrush/trading-desk/2.0/scripts/monitor.sh
```

---

## Performance Tuning

### Optimizing Cache Settings

```bash
# Test different L1 TTL values
# Edit .env:
CACHE_L1_TTL_SECONDS=30  # Default
CACHE_L1_TTL_SECONDS=60  # For less volatile data
CACHE_L1_TTL_SECONDS=15  # For highly volatile data

# Test different L2 TTL values
CACHE_L2_TTL_SECONDS=300   # Default (5 minutes)
CACHE_L2_TTL_SECONDS=600   # For stable data
CACHE_L2_TTL_SECONDS=180   # For volatile data

# Test different cache sizes
CACHE_L1_MAX_SIZE=1000  # Default
CACHE_L1_MAX_SIZE=2000  # For more caching
CACHE_L1_MAX_SIZE=500   # For memory constraints
```

### Optimizing Concurrency

```bash
# Adjust concurrent request limit
# Edit .env:
MAX_CONCURRENT_REQUESTS=10  # Default
MAX_CONCURRENT_REQUESTS=20  # For more throughput
MAX_CONCURRENT_REQUESTS=5   # For rate limit issues

# Test with different concurrency levels
python scripts/analyze.py --file tickers.txt --max-concurrent 5
python scripts/analyze.py --file tickers.txt --max-concurrent 10
python scripts/analyze.py --file tickers.txt --max-concurrent 20
```

### Database Optimization

```bash
# Enable query optimization
sqlite3 data/ivcrush.db "PRAGMA optimize;"

# Create indexes for common queries
sqlite3 data/ivcrush.db "CREATE INDEX IF NOT EXISTS idx_ticker_date ON historical_moves(ticker, event_date);"

# Monitor query performance
sqlite3 data/ivcrush.db "EXPLAIN QUERY PLAN SELECT * FROM historical_moves WHERE ticker='AAPL';"
```

### Rate Limit Tuning

```bash
# Conservative (avoid rate limits)
TRADIER_RATE_LIMIT=100
ALPHA_VANTAGE_RATE_LIMIT=4

# Aggressive (maximize throughput)
TRADIER_RATE_LIMIT=120
ALPHA_VANTAGE_RATE_LIMIT=5

# Burst protection
TRADIER_RATE_PERIOD=60  # Spread over full minute
```

---

## Troubleshooting Guide

### Issue: Slow Response Times

**Symptoms:**
- Analysis takes >5 seconds per ticker
- Users report slow performance

**Diagnosis:**

```bash
# Check performance statistics
python -c "from src.utils.performance import get_monitor; m = get_monitor(); import json; print(json.dumps(m.get_all_stats(), indent=2))"

# Identify slow operations
python -c "from src.utils.performance import get_monitor; m = get_monitor(); slow = m.get_slow_operations(threshold_multiplier=2.0); for op in slow[:10]: print(f'{op[\"name\"]}: {op[\"duration\"]:.3f}s')"
```

**Solutions:**

1. **Clear cache:**
   ```bash
   python -c "from src.container import Container; Container().hybrid_cache().clear()"
   ```

2. **Check network latency:**
   ```bash
   time curl -H "Authorization: Bearer $TRADIER_API_KEY" https://api.tradier.com/v1/markets/quotes?symbols=SPY
   ```

3. **Optimize database:**
   ```bash
   sqlite3 data/ivcrush.db "VACUUM; PRAGMA optimize;"
   ```

### Issue: High Error Rates

**Symptoms:**
- Many ERROR log entries
- Failed analyses

**Diagnosis:**

```bash
# Check error types
grep "ERROR" logs/ivcrush.log | cut -d' ' -f5- | sort | uniq -c | sort -rn | head -10

# Check circuit breaker state
python -c "from src.utils.circuit_breaker import CircuitBreaker; # Check state in logs"
```

**Solutions:**

1. **API errors:** Check API keys and connectivity
2. **Database errors:** Check file permissions and disk space
3. **Rate limit errors:** Reduce concurrency or rate limits
4. **Timeout errors:** Increase retry attempts or timeout values

### Issue: Cache Performance Problems

**Symptoms:**
- Low cache hit rate
- High memory usage

**Diagnosis:**

```bash
# Check cache statistics
python -c "from src.container import Container; c = Container(); import json; print(json.dumps(c.hybrid_cache().stats(), indent=2))"

# Monitor cache size
watch -n 5 'python -c "from src.container import Container; c = Container(); s = c.hybrid_cache().stats(); print(f\"L1: {s[\"l1_size\"]}, L2: {s[\"l2_size\"]}\")""
```

**Solutions:**

1. **Low hit rate:** Increase cache TTL or size
2. **High memory:** Reduce L1_MAX_SIZE
3. **Stale data:** Reduce TTL values

### Issue: Database Lock Errors

**Symptoms:**
- "Database is locked" errors
- Concurrent access failures

**Diagnosis:**

```bash
# Check journal mode
sqlite3 data/ivcrush.db "PRAGMA journal_mode;"

# Check for WAL files
ls -la data/ivcrush.db*
```

**Solutions:**

```bash
# Enable WAL mode
sqlite3 data/ivcrush.db "PRAGMA journal_mode=WAL;"

# Clear stale locks
pkill -f python
rm -f data/ivcrush.db-shm data/ivcrush.db-wal
```

---

## Maintenance Procedures

### Weekly Maintenance

```bash
#!/bin/bash
# Run every Sunday at 2 AM

# 1. Optimize database
sqlite3 data/ivcrush.db "VACUUM; PRAGMA optimize;"

# 2. Clean old cache entries
python -c "from src.container import Container; Container().hybrid_cache().cleanup_expired()"

# 3. Archive old logs
find logs/ -name "*.log" -mtime +30 -exec gzip {} \;

# 4. Clean old backups
find backups/ -name "*.db" -mtime +30 -delete

# 5. Generate weekly report
python scripts/weekly_report.py > reports/weekly_$(date +%Y%m%d).txt
```

### Monthly Maintenance

```bash
#!/bin/bash
# Run first Sunday of month

# 1. Full database backup
sqlite3 data/ivcrush.db ".backup 'backups/monthly_$(date +%Y%m).db'"

# 2. Clean historical data older than 1 year
sqlite3 data/ivcrush.db "DELETE FROM historical_moves WHERE event_date < date('now', '-365 days');"

# 3. Analyze performance trends
python scripts/monthly_analysis.py

# 4. Review and update rate limits
grep RATE_LIMIT .env

# 5. Update dependencies
pip list --outdated
```

### Quarterly Maintenance

```bash
# 1. Review all configuration
cat .env | grep -v "KEY"

# 2. Performance audit
python scripts/performance_audit.py

# 3. Update documentation
git log --since="3 months ago" --oneline

# 4. Disaster recovery test
# - Restore from backup
# - Verify functionality
# - Document results
```

---

## Emergency Procedures

### Service Down

**Immediate Actions:**

```bash
# 1. Check process status
ps aux | grep python | grep ivcrush

# 2. Check logs for errors
tail -100 logs/ivcrush.log

# 3. Restart service
pkill -f "python.*ivcrush"
source venv/bin/activate
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01
```

### Database Corruption

**Immediate Actions:**

```bash
# 1. Stop all processes
pkill -f python

# 2. Check database integrity
sqlite3 data/ivcrush.db "PRAGMA integrity_check;"

# 3. If corrupted, restore from backup
cp data/ivcrush.db data/ivcrush.db.corrupt
cp backups/latest.db data/ivcrush.db

# 4. Verify restoration
sqlite3 data/ivcrush.db "SELECT COUNT(*) FROM historical_moves;"
```

### API Key Compromised

**Immediate Actions:**

```bash
# 1. Rotate API keys immediately
# - Log into Tradier/Alpha Vantage
# - Generate new keys
# - Update .env file

# 2. Stop service
pkill -f python

# 3. Update configuration
sed -i 's/TRADIER_API_KEY=.*/TRADIER_API_KEY=NEW_KEY/' .env

# 4. Restart and verify
python scripts/health_check.py
```

### Disk Space Critical

**Immediate Actions:**

```bash
# 1. Check disk usage
df -h

# 2. Identify large files
du -h data/ logs/ | sort -rh | head -20

# 3. Clean cache
python -c "from src.container import Container; Container().hybrid_cache().clear()"

# 4. Compress old logs
gzip logs/*.log

# 5. Archive old backups
tar -czf backups/archive_$(date +%Y%m).tar.gz backups/*.db
rm backups/*.db
```

---

## Contact and Escalation

### Support Levels

**Level 1 (Self-Service):**
- Check this runbook
- Review logs
- Run health checks

**Level 2 (Team Lead):**
- Configuration issues
- Performance problems
- Non-critical errors

**Level 3 (Engineering):**
- System failures
- Data corruption
- Security incidents

### Escalation Criteria

**Immediate Escalation:**
- Service completely down
- Database corruption
- API key compromised
- Security breach

**Next Business Day:**
- Performance degradation
- High error rates
- Cache problems

---

## Useful Commands Reference

### Quick Reference

```bash
# Health check
python scripts/health_check.py

# Analyze ticker
python scripts/analyze.py TICKER --earnings-date YYYY-MM-DD --expiration YYYY-MM-DD

# View cache stats
python -c "from src.container import Container; print(Container().hybrid_cache().stats())"

# View performance
python -c "from src.utils.performance import get_monitor; import json; print(json.dumps(get_monitor().get_all_stats(), indent=2))"

# Check database
sqlite3 data/ivcrush.db "PRAGMA integrity_check;"

# View logs
tail -f logs/ivcrush.log

# Clear cache
python -c "from src.container import Container; Container().hybrid_cache().clear()"
```

---

**End of Operational Runbook**
