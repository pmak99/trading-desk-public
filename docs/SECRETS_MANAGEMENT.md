# Secrets Management Guide

**Date**: November 9, 2025
**Feature**: Secure API key storage using macOS Keychain

---

## Overview

The Trading Desk now supports secure secret storage using **macOS Keychain** via `python-keyring`. This provides better security than storing API keys in plaintext `.env` files.

### Benefits

- ✅ **Encrypted storage** - Secrets stored in macOS Keychain (encrypted)
- ✅ **No plaintext files** - API keys not in plaintext on disk
- ✅ **Backward compatible** - Automatic fallback to `.env` if needed
- ✅ **Easy migration** - One command to migrate existing secrets
- ✅ **OS integration** - Use Keychain Access app to manage secrets

---

## Quick Start

### Option 1: Use Existing .env File (Current Method)

Nothing changes! Your existing `.env` file continues to work:

```bash
# Your .env file
TRADIER_ACCESS_TOKEN=your_token_here
PERPLEXITY_API_KEY=your_key_here
```

Code automatically checks Keychain first, then falls back to `.env`.

### Option 2: Migrate to Keychain (Recommended)

**Step 1: Install keyring**
```bash
pip install keyring
```

**Step 2: Migrate your secrets**
```bash
python scripts/migrate_secrets.py
```

This script will:
1. Read secrets from your `.env` file
2. Store them securely in macOS Keychain
3. Create a backup of your `.env`
4. Optionally remove/comment out secrets from `.env`

**Step 3: Verify migration**

Open Keychain Access app:
```bash
open "/Applications/Utilities/Keychain Access.app"
```

Search for "trading-desk" to see your secrets.

---

## Usage

### Get Secrets in Code

The `SecretsManager` provides a simple API:

```python
from src.core.secrets_manager import get_secret

# Get a secret (checks Keychain → .env → None)
api_key = get_secret('TRADIER_ACCESS_TOKEN')

# Get with default value
endpoint = get_secret('TRADIER_ENDPOINT', default='https://api.tradier.com')
```

### Set Secrets Programmatically

```python
from src.core.secrets_manager import set_secret

# Store a new secret in Keychain
set_secret('NEW_API_KEY', 'abc123xyz')
```

### Delete Secrets

```python
from src.core.secrets_manager import get_manager

manager = get_manager()
manager.delete('OLD_API_KEY')
```

### List Available Secrets

```python
from src.core.secrets_manager import get_manager

manager = get_manager()
keys = manager.list_keys()
print(f"Configured secrets: {keys}")
```

---

## Migration Guide

### Before Migration

Your secrets are in `.env`:
```bash
# .env (plaintext on disk)
TRADIER_ACCESS_TOKEN=abc123
PERPLEXITY_API_KEY=xyz789
```

### After Migration

Secrets are in Keychain:
```bash
# .env (optional, can be empty or commented out)
# Migrated to macOS Keychain on 2025-11-09
# TRADIER_ACCESS_TOKEN=abc123  # Now in Keychain
# PERPLEXITY_API_KEY=xyz789    # Now in Keychain
```

### Migration Options

When running `scripts/migrate_secrets.py`, you can choose:

1. **Keep .env as-is** - Secrets in both places (less secure, easier testing)
2. **Comment out secrets** - Secrets remain in .env but commented (recommended)
3. **Delete from .env** - Secrets only in Keychain (most secure)

---

## How It Works

### Priority Order

1. **macOS Keychain** (via python-keyring) - Most secure
2. **Environment variables** (from `.env` file) - Fallback
3. **None** (with warning) - Not configured

### Code Example

```python
# Old way (still works)
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv('TRADIER_ACCESS_TOKEN')

# New way (recommended)
from src.core.secrets_manager import get_secret

api_key = get_secret('TRADIER_ACCESS_TOKEN')
```

Both work! The new way checks Keychain first, then falls back to `.env`.

---

## Security Best Practices

### DO

- ✅ Use Keychain for production secrets
- ✅ Keep `.env.backup` files secure (they contain plaintext secrets)
- ✅ Use `.env` for development/testing (easier to change)
- ✅ Review secrets periodically in Keychain Access app
- ✅ Rotate API keys regularly

### DON'T

- ❌ Commit `.env` file to git (already in `.gitignore`)
- ❌ Share `.env` files (use `.env.example` template)
- ❌ Store secrets in code or config files
- ❌ Use same API keys across multiple projects

---

## Troubleshooting

### Keyring Not Available

```
ERROR: python-keyring not installed
```

**Solution:**
```bash
pip install keyring
```

### macOS Permission Denied

If Keychain Access asks for permission:
1. Click "Always Allow" for the Python app
2. Enter your macOS password when prompted

### Secrets Not Found

```python
# Debug: Check where secret is coming from
from src.core.secrets_manager import get_manager

manager = get_manager()
value = manager.get('TRADIER_ACCESS_TOKEN')

if value:
    print("Secret found!")
else:
    print("Secret not in Keychain or .env")
```

### Fallback to .env Not Working

Make sure `.env` file exists and `python-dotenv` is installed:
```bash
pip install python-dotenv
```

---

## Advanced Usage

### Custom Service Name

By default, secrets are stored under "trading-desk" in Keychain. To use a different name:

```python
from src.core.secrets_manager import SecretsManager

# Use custom service name
manager = SecretsManager()
# Note: Modify SERVICE_NAME constant in secrets_manager.py
```

### Programmatic Migration

```python
from src.core.secrets_manager import get_manager
import os
from dotenv import load_dotenv

load_dotenv()
manager = get_manager()

# Migrate specific keys
keys_to_migrate = ['TRADIER_ACCESS_TOKEN', 'PERPLEXITY_API_KEY']

for key in keys_to_migrate:
    value = os.getenv(key)
    if value:
        manager.set(key, value)
        print(f"Migrated {key}")
```

---

## Viewing Secrets in Keychain Access

1. Open Keychain Access:
   ```bash
   open "/Applications/Utilities/Keychain Access.app"
   ```

2. Search for "trading-desk"

3. Double-click a secret to view:
   - Click "Show password"
   - Enter your macOS password
   - View/edit/delete as needed

---

## Backup & Recovery

### Export Secrets from Keychain

```bash
# Run migration script to create backup
python scripts/migrate_secrets.py
```

This creates `.env.backup.YYYYMMDD_HHMMSS` with all secrets.

### Restore from Backup

1. Copy backup to `.env`:
   ```bash
   cp .env.backup.20251109_203000 .env
   ```

2. Re-run migration:
   ```bash
   python scripts/migrate_secrets.py
   ```

---

## Integration with CI/CD

For GitHub Actions, CircleCI, etc., continue using environment variables:

```yaml
# .github/workflows/test.yml
env:
  TRADIER_ACCESS_TOKEN: ${{ secrets.TRADIER_ACCESS_TOKEN }}
  PERPLEXITY_API_KEY: ${{ secrets.PERPLEXITY_API_KEY }}
```

The code automatically falls back to environment variables when Keychain is unavailable.

---

## Performance

- **Keychain access**: ~1-5ms per secret
- **Cache**: Secrets cached in memory after first access
- **Fallback**: Instant (already in environment)

No noticeable performance impact.

---

## Summary

| Feature | .env File | macOS Keychain |
|---------|-----------|----------------|
| Security | Plaintext | Encrypted |
| Access Control | File permissions | OS-managed |
| Audit Trail | None | OS logs |
| GUI Management | Text editor | Keychain Access |
| Backup | Manual copy | Time Machine |
| Rotation | Manual edit | GUI or API |
| CI/CD Support | ✅ Easy | Requires env vars |

**Recommendation**: Use Keychain for local development, environment variables for CI/CD.

---

## Support

For issues or questions:
1. Check this guide
2. Run migration script with verbose logging
3. Check macOS Keychain Access app
4. Review `src/core/secrets_manager.py` code

---

**Generated**: November 9, 2025
**Last Updated**: November 9, 2025
