# IV Crush 2.0 - Deployment Guide

**Version:** 2.0
**Last Updated:** 2025-11-12
**Target Environment:** Production

This guide covers deploying IV Crush 2.0 to a production environment.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Environment Setup](#environment-setup)
3. [Configuration](#configuration)
4. [Database Setup](#database-setup)
5. [Deployment Steps](#deployment-steps)
6. [Health Checks](#health-checks)
7. [Monitoring](#monitoring)
8. [Rollback Procedures](#rollback-procedures)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements

- **OS:** Linux (Ubuntu 20.04+ or similar)
- **Python:** 3.11 or higher
- **Memory:** 2GB minimum, 4GB recommended
- **Disk Space:** 1GB minimum for application + database
- **Network:** Outbound HTTPS access to:
  - api.tradier.com
  - www.alphavantage.co

### Required Accounts

- **Tradier:** Active brokerage or sandbox account with API access
- **Alpha Vantage:** API key (free or premium tier)

### Dependencies

```bash
# System packages
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip sqlite3

# Python packages (installed via pip)
- pandas
- requests
- python-dotenv
- pytest (for testing)
```

---

## Environment Setup

### 1. Create Application User

```bash
# Create dedicated user for the application
sudo useradd -r -m -s /bin/bash ivcrush
sudo usermod -aG sudo ivcrush  # Optional: if user needs sudo

# Switch to application user
sudo su - ivcrush
```

### 2. Clone Repository

```bash
cd /home/ivcrush
git clone https://github.com/yourusername/trading-desk.git
cd trading-desk/2.0
```

### 3. Create Virtual Environment

```bash
python3.11 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

### 4. Create Directory Structure

```bash
# Create data directory
mkdir -p data

# Create log directory
mkdir -p logs

# Set permissions
chmod 750 data logs
```

---

## Configuration

### 1. Create Environment File

Create `.env` file in the project root:

```bash
# Copy template
cp .env.example .env

# Edit with production values
nano .env
```

### 2. Required Environment Variables

```ini
# API Configuration
TRADIER_API_KEY=your_production_tradier_key_here
ALPHA_VANTAGE_KEY=your_alphavantage_key_here
TRADIER_BASE_URL=https://api.tradier.com/v1

# Database Configuration
DATABASE_PATH=./data/ivcrush.db

# Cache Configuration (recommended production values)
CACHE_L1_TTL_SECONDS=30
CACHE_L2_TTL_SECONDS=300
CACHE_L1_MAX_SIZE=1000

# Rate Limiting (Tradier standard: 120 req/min, AV: 5 req/min)
TRADIER_RATE_LIMIT=120
TRADIER_RATE_PERIOD=60
ALPHA_VANTAGE_RATE_LIMIT=5
ALPHA_VANTAGE_RATE_PERIOD=60

# Resilience Configuration
RETRY_MAX_ATTEMPTS=3
RETRY_BASE_DELAY=1.0
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT=60
MAX_CONCURRENT_REQUESTS=10

# VRP Thresholds
VRP_THRESHOLD_EXCELLENT=1.5
VRP_THRESHOLD_GOOD=1.2

# Logging
LOG_LEVEL=INFO
LOG_FILE=./logs/ivcrush.log
```

### 3. Validate Configuration

```bash
# Run configuration validation
python -c "from src.config.config import Config; from src.config.validation import validate_configuration; config = Config.from_env(); errors = validate_configuration(config); print('✓ Configuration valid' if not errors else f'✗ Errors: {errors}')"
```

---

## Database Setup

### 1. Initialize Database Schema

```bash
# Initialize SQLite database with schema
python -c "from src.infrastructure.database.init_schema import initialize_database; initialize_database('./data/ivcrush.db')"
```

### 2. Verify Database Creation

```bash
# Check database exists and has tables
sqlite3 ./data/ivcrush.db ".tables"

# Expected output:
# cache           historical_moves
```

### 3. Enable WAL Mode (Production Optimization)

```bash
# Enable Write-Ahead Logging for better concurrency
sqlite3 ./data/ivcrush.db "PRAGMA journal_mode=WAL;"

# Verify WAL mode
sqlite3 ./data/ivcrush.db "PRAGMA journal_mode;"
# Expected output: wal
```

### 4. Set Database Permissions

```bash
# Restrict database access to application user
chmod 640 ./data/ivcrush.db
```

---

## Deployment Steps

### Step 1: Pre-Deployment Checks

```bash
# Activate virtual environment
source venv/bin/activate

# Run full test suite
pytest tests/unit/ tests/performance/ -v

# Expected: All 172 tests pass
```

### Step 2: Run Health Checks

```bash
# Check external API connectivity
python scripts/health_check.py

# Expected output:
# ✓ Tradier API: Healthy
# ✓ Database: Healthy
# ✓ Cache: Healthy
```

### Step 3: Deploy Application

```bash
# Application is now ready for use
# Test with a single ticker
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01

# Expected: JSON output with analysis results
```

### Step 4: Configure Scheduled Execution (Recommended for Production)

The IV Crush system is designed as a one-off batch analysis tool that runs on a schedule. Configure it using systemd service + timer for scheduled execution.

#### Prerequisites

1. Create an earnings calendar CSV file with upcoming earnings dates:

```bash
# Create earnings calendar file
cat > /home/ivcrush/trading-desk/2.0/earnings.csv << 'EOF'
ticker,earnings_date,expiration_date
AAPL,2025-01-31,2025-02-01
MSFT,2025-02-15,2025-02-21
GOOGL,2025-02-20,2025-02-21
TSLA,2025-03-10,2025-03-14
EOF
```

2. Create a tickers file (optional - if not using earnings calendar for all tickers):

```bash
# Create tickers file
cat > /home/ivcrush/trading-desk/2.0/tickers.txt << 'EOF'
AAPL
MSFT
GOOGL
TSLA
EOF
```

#### Create Systemd Service File

```bash
# Create service file
sudo nano /etc/systemd/system/ivcrush.service
```

Add the following configuration:

```ini
[Unit]
Description=IV Crush 2.0 Options Trading Analysis
Documentation=https://github.com/yourusername/trading-desk
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ivcrush
Group=ivcrush
WorkingDirectory=/home/ivcrush/trading-desk/2.0
Environment="PATH=/home/ivcrush/trading-desk/2.0/venv/bin"
EnvironmentFile=/home/ivcrush/trading-desk/2.0/.env

# Batch analysis with earnings calendar
ExecStart=/home/ivcrush/trading-desk/2.0/venv/bin/python scripts/analyze_batch.py --file /home/ivcrush/trading-desk/2.0/tickers.txt --earnings-file /home/ivcrush/trading-desk/2.0/earnings.csv --continue-on-error

# Alternative: Analyze single ticker
# ExecStart=/home/ivcrush/trading-desk/2.0/venv/bin/python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01

StandardOutput=append:/home/ivcrush/trading-desk/2.0/logs/analysis.log
StandardError=append:/home/ivcrush/trading-desk/2.0/logs/analysis_error.log

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/home/ivcrush/trading-desk/2.0/data /home/ivcrush/trading-desk/2.0/logs

[Install]
WantedBy=multi-user.target
```

#### Create Systemd Timer File

```bash
# Create timer file
sudo nano /etc/systemd/system/ivcrush.timer
```

Add the following configuration:

```ini
[Unit]
Description=IV Crush 2.0 Analysis Timer
Documentation=https://github.com/yourusername/trading-desk
Requires=ivcrush.service

[Timer]
# Run every weekday at 9:30 AM ET (after market open)
OnCalendar=Mon-Fri *-*-* 09:30:00

# Run on boot if missed (e.g., system was down)
Persistent=true

# Randomize start time by up to 5 minutes to avoid thundering herd
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

**Alternative Timer Schedules:**

```ini
# Every day at 9:30 AM
OnCalendar=*-*-* 09:30:00

# Every Monday at 10:00 AM
OnCalendar=Mon *-*-* 10:00:00

# Every hour during market hours (9:30 AM - 4:00 PM ET, Mon-Fri)
OnCalendar=Mon-Fri *-*-* 09,10,11,12,13,14,15,16:30:00

# Every 4 hours
OnCalendar=*-*-* 00,04,08,12,16,20:00:00
```

#### Enable and Start Timer

```bash
# Reload systemd to recognize new service and timer
sudo systemctl daemon-reload

# Enable timer to start on boot
sudo systemctl enable ivcrush.timer

# Start the timer
sudo systemctl start ivcrush.timer

# Check timer status
sudo systemctl status ivcrush.timer

# List all timers to verify schedule
sudo systemctl list-timers ivcrush.timer

# View timer logs
sudo journalctl -u ivcrush.timer
```

#### Test Service Manually

Before relying on the timer, test the service manually:

```bash
# Run service once manually
sudo systemctl start ivcrush.service

# Check status
sudo systemctl status ivcrush.service

# View logs
sudo journalctl -u ivcrush.service -n 50
tail -n 100 /home/ivcrush/trading-desk/2.0/logs/analysis.log
```

#### Service and Timer Management Commands

```bash
# Timer commands
sudo systemctl start ivcrush.timer      # Start timer
sudo systemctl stop ivcrush.timer       # Stop timer
sudo systemctl restart ivcrush.timer    # Restart timer
sudo systemctl status ivcrush.timer     # Check timer status
sudo systemctl list-timers ivcrush.*    # Show next run time

# Service commands (manual execution)
sudo systemctl start ivcrush.service    # Run analysis now
sudo systemctl status ivcrush.service   # Check last run status

# View logs
sudo journalctl -u ivcrush.service --since today    # Today's service logs
sudo journalctl -u ivcrush.timer --since today      # Today's timer logs
sudo journalctl -u ivcrush.* -f                     # Follow all logs

# Disable/remove
sudo systemctl stop ivcrush.timer
sudo systemctl disable ivcrush.timer
sudo rm /etc/systemd/system/ivcrush.{service,timer}
sudo systemctl daemon-reload
```

**Note:** The systemd timer approach is more robust than cron because:
- Better logging integration with journalctl
- Can catch up on missed runs (Persistent=true)
- Integrated service dependencies
- Better security isolation

### Step 5: Schedule Backfill (Optional)

If you need historical data:

```bash
# Backfill historical data for specific tickers
python scripts/backfill.py --tickers AAPL,MSFT,GOOGL --days 90

# Or backfill from a file
python scripts/backfill.py --file tickers.txt --days 90
```

---

## Health Checks

### Manual Health Check

```bash
# Run comprehensive health check
python scripts/health_check.py

# Check individual components
python -c "from src.application.services.health import HealthCheckService; from src.container import Container; container = Container(); service = HealthCheckService(container.options_provider(), container.database_path(), container.hybrid_cache()); import asyncio; print(asyncio.run(service.check_all()))"
```

### Automated Monitoring

Create a cron job for periodic health checks:

```bash
# Edit crontab
crontab -e

# Add health check every 5 minutes
*/5 * * * * /home/ivcrush/trading-desk/2.0/venv/bin/python /home/ivcrush/trading-desk/2.0/scripts/health_check.py >> /home/ivcrush/trading-desk/2.0/logs/health.log 2>&1
```

### Health Check Endpoints

If deploying as a service:

- **Database:** Check `./data/ivcrush.db` exists and is writable
- **Cache:** Check `./data/cache.db` exists (L2 cache)
- **APIs:** Call Tradier `/v1/markets/quotes?symbols=SPY` and Alpha Vantage time series
- **Logs:** Check `./logs/ivcrush.log` for errors

---

## Monitoring

### Key Metrics to Monitor

#### 1. Performance Metrics

```bash
# Check performance statistics
python -c "from src.utils.performance import get_monitor; monitor = get_monitor(); stats = monitor.get_all_stats(); print(stats)"
```

Monitor:
- Average response time per ticker
- P95/P99 latency
- Slow operations (>threshold)

#### 2. Cache Hit Rates

```bash
# Check cache statistics
python -c "from src.container import Container; c = Container(); cache = c.hybrid_cache(); print(cache.stats())"
```

Monitor:
- L1 cache hit rate (target: >80%)
- L2 cache hit rate (target: >60%)
- Cache size growth

#### 3. API Rate Limits

Monitor logs for rate limit warnings:

```bash
# Check for rate limit errors
grep "rate limit" ./logs/ivcrush.log

# Expected: No rate limit errors in production
```

#### 4. Database Size

```bash
# Monitor database size
du -h ./data/ivcrush.db

# If database grows too large (>1GB), consider cleanup:
sqlite3 ./data/ivcrush.db "DELETE FROM cache WHERE created_at < datetime('now', '-7 days');"
sqlite3 ./data/ivcrush.db "VACUUM;"
```

#### 5. Error Rates

```bash
# Check error frequency
grep "ERROR" ./logs/ivcrush.log | wc -l

# Review specific errors
grep "ERROR" ./logs/ivcrush.log | tail -20
```

### Log Rotation

Setup logrotate to prevent log files from growing too large:

```bash
# Create logrotate config
sudo nano /etc/logrotate.d/ivcrush
```

```
/home/ivcrush/trading-desk/2.0/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    missingok
    copytruncate
}
```

---

## Rollback Procedures

### Rollback to Previous Version

```bash
# 1. Stop any running processes
pkill -f "python.*analyze.py"

# 2. Switch to previous git tag
git fetch --tags
git checkout v1.x.x  # Replace with desired version

# 3. Reinstall dependencies
source venv/bin/activate
pip install -r requirements.txt

# 4. Run health checks
python scripts/health_check.py

# 5. Verify functionality
pytest tests/unit/ -v
```

### Database Rollback

```bash
# 1. Stop application
pkill -f "python.*analyze.py"

# 2. Restore from backup
cp ./data/ivcrush.db.backup ./data/ivcrush.db

# 3. Verify database integrity
sqlite3 ./data/ivcrush.db "PRAGMA integrity_check;"

# 4. Restart application
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01
```

### Emergency Shutdown

```bash
# Stop all Python processes
pkill -9 -f "python.*ivcrush"

# Check for running processes
ps aux | grep python | grep ivcrush

# Verify no orphaned connections
lsof -i :8000  # If running as API service
```

---

## Troubleshooting

### Common Issues

#### 1. Configuration Errors

**Symptom:** Application fails to start with configuration validation errors

```bash
# Check configuration
python -c "from src.config.config import Config; Config.from_env()"

# Common fixes:
# - Verify .env file exists and is readable
# - Check API keys are valid
# - Ensure database directory is writable
```

#### 2. API Connection Errors

**Symptom:** "Connection refused" or "Timeout" errors

```bash
# Test API connectivity
curl -H "Authorization: Bearer $TRADIER_API_KEY" https://api.tradier.com/v1/markets/quotes?symbols=SPY

# Common fixes:
# - Check network connectivity
# - Verify API keys are valid
# - Check firewall rules allow outbound HTTPS
```

#### 3. Database Lock Errors

**Symptom:** "Database is locked" errors

```bash
# Check for WAL mode
sqlite3 ./data/ivcrush.db "PRAGMA journal_mode;"

# Enable WAL if not set
sqlite3 ./data/ivcrush.db "PRAGMA journal_mode=WAL;"

# Check for stale lock files
ls -la ./data/ivcrush.db*
rm ./data/ivcrush.db-shm ./data/ivcrush.db-wal  # If safe to do so
```

#### 4. Memory Issues

**Symptom:** Application crashes or slows down

```bash
# Check memory usage
ps aux | grep python | grep ivcrush

# Monitor cache size
python -c "from src.container import Container; c = Container(); print(c.hybrid_cache().stats())"

# Clear cache if needed
python -c "from src.container import Container; c = Container(); c.hybrid_cache().clear()"
```

#### 5. Rate Limit Errors

**Symptom:** "Rate limit exceeded" errors

```bash
# Check current rate limits in .env
grep RATE_LIMIT .env

# Adjust if needed:
# - Reduce TRADIER_RATE_LIMIT (standard: 120/min)
# - Reduce ALPHA_VANTAGE_RATE_LIMIT (free: 5/min, premium: 75/min)
# - Increase RATE_PERIOD for more conservative limiting
```

### Debug Mode

Enable detailed logging for troubleshooting:

```bash
# Set LOG_LEVEL to DEBUG in .env
sed -i 's/LOG_LEVEL=INFO/LOG_LEVEL=DEBUG/' .env

# Run with verbose output
python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01

# Remember to set back to INFO after debugging
sed -i 's/LOG_LEVEL=DEBUG/LOG_LEVEL=INFO/' .env
```

---

## Backup and Disaster Recovery

### Database Backup

```bash
# Create backup script
cat > scripts/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR=/home/ivcrush/backups
DATE=$(date +%Y%m%d_%H%M%S)
sqlite3 /home/ivcrush/trading-desk/2.0/data/ivcrush.db ".backup '$BACKUP_DIR/ivcrush_$DATE.db'"
# Keep only last 7 days of backups
find $BACKUP_DIR -name "ivcrush_*.db" -mtime +7 -delete
EOF

chmod +x scripts/backup.sh

# Schedule daily backups
crontab -e
# Add: 0 2 * * * /home/ivcrush/trading-desk/2.0/scripts/backup.sh
```

### Configuration Backup

```bash
# Backup .env file (excluding secrets)
cp .env .env.backup

# Store in version control (without sensitive data)
git add .env.example
git commit -m "Update .env template"
```

---

## Production Checklist

Before going live, verify:

- [ ] All tests pass (172/172)
- [ ] Configuration validated
- [ ] Database initialized with WAL mode
- [ ] API keys configured and tested
- [ ] Health checks passing
- [ ] Log rotation configured
- [ ] Backup system in place
- [ ] Monitoring alerts configured
- [ ] Rate limits tested under load
- [ ] Documentation reviewed

---

## Support and Resources

- **Documentation:** `/docs/` directory
- **Tests:** `/tests/` directory (164 unit + 8 load tests)
- **Progress Tracker:** `PROGRESS.md`
- **Operational Guide:** `RUNBOOK.md`

---

**End of Deployment Guide**
