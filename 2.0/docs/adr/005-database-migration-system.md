# ADR-005: Formal Database Migration System

## Status
Accepted (November 2024)

## Context
The system had ad-hoc schema changes scattered across multiple files:
- `init_schema.py` - Initial schema creation
- `positions_schema.py` - Positions table schema
- `init_backtest_schema.py` - Backtest schema
- `hybrid_cache.py` - Manual column addition with `PRAGMA table_info()`

### Problems with Ad-Hoc Migrations
1. **No Version Tracking**: Can't tell what schema version is deployed
2. **Non-Repeatable**: Manual migrations might be missed or applied inconsistently
3. **No Rollback**: Can't undo problematic migrations
4. **Error-Prone**: Manual schema checks with `PRAGMA table_info()` are fragile
5. **No History**: Can't audit when/why schema changes were made
6. **Testing Issues**: Hard to test migrations in isolation

### Example of Manual Migration (Before)
```python
# From hybrid_cache.py
cursor.execute("PRAGMA table_info(cache)")
columns = [row[1] for row in cursor.fetchall()]
if 'expiration' not in columns:
    logger.info("Migrating cache schema: adding expiration column")
    conn.execute('ALTER TABLE cache ADD COLUMN expiration TEXT')
```

This approach:
- Duplicates schema inspection logic
- No centralized migration tracking
- Easy to miss when deploying to new environments

## Decision
**Implement a formal database migration system with version tracking and automatic application.**

### Architecture
```
src/infrastructure/database/
├── migrations/
│   ├── __init__.py
│   └── migration_manager.py  # Migration framework
└── connection_pool.py

scripts/
└── migrate.py  # CLI tool for manual migration management
```

### Key Components

#### 1. Migration Model
```python
@dataclass
class Migration:
    version: int  # Sequential version number
    name: str  # Descriptive name (e.g., "add_cache_expiration")
    sql_up: str  # SQL to apply migration
    sql_down: str | None  # SQL to rollback (optional)
```

#### 2. Migration Manager
```python
class MigrationManager:
    def migrate(self, target_version: int | None = None) -> int
    def rollback(self, target_version: int) -> int
    def get_current_version() -> int
    def get_pending_migrations() -> List[Migration]
    def status() -> dict
```

#### 3. Version Tracking Table
```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    checksum TEXT  -- Future: Verify migration integrity
)
```

#### 4. Container Integration
```python
class Container:
    def __init__(self, config: Config, run_migrations: bool = True):
        if run_migrations:
            self._run_migrations()  # Automatic on startup
```

#### 5. CLI Tool
```bash
python scripts/migrate.py status     # Show current version
python scripts/migrate.py migrate    # Apply pending migrations
python scripts/migrate.py rollback 1 # Rollback to version 1
python scripts/migrate.py create foo # Generate migration template
```

## Consequences

### Positive
✅ **Version Tracking**: Always know what schema version is deployed
✅ **Repeatable**: Same migrations applied in dev, staging, prod
✅ **Automatic**: Migrations run on container startup (opt-out with `run_migrations=False`)
✅ **Transaction-Safe**: Each migration in a transaction (all-or-nothing)
✅ **Audit Trail**: schema_migrations table shows when/what was applied
✅ **Rollback Support**: Can undo migrations (when rollback SQL provided)
✅ **Testable**: Migrations can be tested in isolated environments
✅ **CLI Tool**: Manual migration management for debugging/maintenance

### Negative
⚠️ **More Files**: Migration framework adds ~350 lines of code
⚠️ **Learning Curve**: Team needs to learn migration workflow
⚠️ **Startup Overhead**: ~50-100ms on app startup (negligible for current scale)

### Performance Impact
- **First Run**: Applies all migrations (~100-200ms)
- **Subsequent Runs**: Fast check (~10-20ms) - "already up to date"
- **Production**: Negligible - migrations only run once per version

## Migration Workflow

### Creating New Migration
```python
# 1. Add to MigrationManager._load_migrations()
self.migrations.append(Migration(
    version=3,
    name="add_user_preferences_table",
    sql_up="""
        CREATE TABLE user_preferences (
            user_id INTEGER PRIMARY KEY,
            theme TEXT DEFAULT 'dark',
            timezone TEXT DEFAULT 'UTC'
        )
    """,
    sql_down="DROP TABLE user_preferences"
))
```

### Applying Migrations
Automatic on startup:
```python
container = Container(config)  # Runs migrations automatically
```

Manual via CLI:
```bash
python scripts/migrate.py migrate  # Apply all pending
python scripts/migrate.py migrate 5  # Apply up to version 5
```

### Rollback (Emergency)
```bash
python scripts/migrate.py rollback 2  # Rollback to version 2
```

## Migration Best Practices

### 1. Incremental Changes
```python
# Good: Small, focused migrations
Migration(version=3, name="add_user_id_index", ...)
Migration(version=4, name="add_email_column", ...)

# Bad: Large, multi-concern migrations
Migration(version=3, name="refactor_entire_schema", ...)
```

### 2. Always Provide Rollback
```python
# Good: Rollback SQL provided
sql_down="ALTER TABLE users DROP COLUMN email"

# Acceptable: Rollback not possible
sql_down=None  # Data loss would occur
```

### 3. Test Migrations
```python
def test_migration_003():
    manager = MigrationManager(":memory:")
    manager.migrate(target_version=3)
    # Verify schema changes
    assert manager.get_current_version() == 3
```

## Code Metrics

### Lines of Code
- MigrationManager: 298 lines
- CLI tool (migrate.py): 192 lines
- Total: 490 lines

### Files Modified/Created
- `src/infrastructure/database/migrations/__init__.py` (NEW)
- `src/infrastructure/database/migrations/migration_manager.py` (NEW)
- `scripts/migrate.py` (NEW - CLI tool)
- `src/container.py` (MODIFIED - added _run_migrations)

## Migration Path

### Phase 1: Bootstrap (Completed)
1. ✅ Create MigrationManager class
2. ✅ Create CLI tool
3. ✅ Integrate with Container
4. ✅ Migrate existing ad-hoc migrations to formal system

### Phase 2: Convert Existing Schemas (Future)
1. ⏳ Create migration for all tables in init_schema.py
2. ⏳ Create migration for positions_schema.py
3. ⏳ Create migration for backtest_schema.py
4. ⏳ Remove old schema initialization code

### Phase 3: Production Deployment
1. ⏳ Backup production database
2. ⏳ Test migrations in staging
3. ⏳ Deploy with migrations enabled
4. ⏳ Monitor for migration failures

## Alternatives Considered

### 1. Alembic (SQLAlchemy Migrations)
**Rejected**: Too heavyweight for SQLite-only system, requires SQLAlchemy

### 2. Django Migrations
**Rejected**: Requires Django framework, overkill for current needs

### 3. Liquibase/Flyway
**Rejected**: Java-based, adds external dependency

### 4. Keep Ad-Hoc Migrations
**Rejected**: Doesn't solve version tracking, repeatability, or audit issues

### 5. Manual SQL Scripts
**Rejected**: No automatic application, easy to miss in deployment

## Future Enhancements
1. **Migration Files**: Load migrations from SQL files instead of Python
2. **Checksums**: Verify migrations haven't been modified after application
3. **Dry Run**: Preview migrations without applying
4. **Migration Dependencies**: Handle complex migration ordering
5. **Multi-Database Support**: Extend to other databases beyond SQLite

## References
- Implementation: `src/infrastructure/database/migrations/migration_manager.py`
- CLI Tool: `scripts/migrate.py`
- Container Integration: `src/container.py:455-478`
- Migration 001: Create schema_migrations table
- Migration 002: Add cache expiration column

## Deployment Checklist
- [x] MigrationManager created
- [x] CLI tool created
- [x] Container integration complete
- [x] Initial migrations applied
- [ ] All existing schema init code converted to migrations
- [ ] Production database backed up
- [ ] Migrations tested in staging
- [ ] Team trained on migration workflow
