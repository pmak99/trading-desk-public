#!/usr/bin/env python3
"""
Migrate secrets from .env file to macOS Keychain.

This script reads API keys from your .env file and stores them
securely in macOS Keychain using python-keyring.

Usage:
    python scripts/migrate_secrets.py

What it does:
1. Reads secrets from .env file
2. Stores them in macOS Keychain
3. Creates .env.backup
4. Optionally removes secrets from .env

After migration:
- Secrets stored in Keychain (secure, encrypted)
- Code automatically uses Keychain first, .env as fallback
- You can view/edit secrets in Keychain Access app
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from src.core.secrets_manager import get_manager, KEYRING_AVAILABLE

# Load .env file
env_file = project_root / '.env'
load_dotenv(env_file)


def main():
    """Migrate secrets from .env to OS keychain."""
    print("=" * 70)
    print("MIGRATE SECRETS TO MACOS KEYCHAIN")
    print("=" * 70)
    print()

    # Check if keyring is available
    if not KEYRING_AVAILABLE:
        print("❌ ERROR: python-keyring not installed")
        print()
        print("Install it with:")
        print("  pip install keyring")
        print()
        sys.exit(1)

    # Check if .env exists
    if not env_file.exists():
        print(f"❌ ERROR: .env file not found at {env_file}")
        print()
        print("Create .env file with your API keys first.")
        print("You can copy .env.example and fill in your keys.")
        print()
        sys.exit(1)

    print(f"✓ Found .env file: {env_file}")
    print()

    # List of secrets to migrate
    secrets_to_migrate = [
        ('PERPLEXITY_API_KEY', 'Perplexity AI API key'),
        ('GOOGLE_API_KEY', 'Google Gemini API key'),
        ('ALPHA_VANTAGE_API_KEY', 'Alpha Vantage API key'),
        ('TRADIER_ACCESS_TOKEN', 'Tradier API token'),
        ('TRADIER_ENDPOINT', 'Tradier API endpoint'),
        ('REDDIT_CLIENT_ID', 'Reddit client ID'),
        ('REDDIT_CLIENT_SECRET', 'Reddit client secret'),
    ]

    # Get manager
    manager = get_manager()

    # Check which secrets are in .env
    found_secrets = []
    for key, description in secrets_to_migrate:
        value = os.getenv(key)
        if value and value != f'your_{key.lower()}_here' and value.strip():
            found_secrets.append((key, description, value))

    if not found_secrets:
        print("❌ No secrets found in .env file")
        print()
        print("Make sure your .env file contains actual API keys,")
        print("not placeholder values like 'your_api_key_here'.")
        print()
        sys.exit(1)

    print(f"Found {len(found_secrets)} secrets to migrate:")
    print()
    for key, description, _ in found_secrets:
        # Show partial value for verification
        value = os.getenv(key)
        masked = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
        print(f"  ✓ {description:30s} ({key})")
        print(f"    Value: {masked}")
    print()

    # Confirm migration
    response = input("Migrate these secrets to macOS Keychain? [y/N]: ").strip().lower()
    if response not in ['y', 'yes']:
        print("Migration cancelled.")
        sys.exit(0)

    print()
    print("Migrating secrets...")
    print()

    # Migrate each secret
    migrated = []
    failed = []

    for key, description, value in found_secrets:
        try:
            if manager.set(key, value):
                migrated.append((key, description))
                print(f"  ✓ Migrated {description}")
            else:
                failed.append((key, description))
                print(f"  ✗ Failed to migrate {description}")
        except Exception as e:
            failed.append((key, description))
            print(f"  ✗ Failed to migrate {description}: {e}")

    print()
    print("=" * 70)
    print("MIGRATION COMPLETE")
    print("=" * 70)
    print()
    print(f"Successfully migrated: {len(migrated)} secrets")
    if failed:
        print(f"Failed to migrate: {len(failed)} secrets")
    print()

    if migrated:
        print("Migrated secrets:")
        for key, description in migrated:
            print(f"  ✓ {description} ({key})")
        print()

        # Create backup of .env
        backup_file = env_file.parent / f'.env.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}'

        print(f"Creating backup: {backup_file.name}")
        with open(env_file, 'r') as f:
            content = f.read()
        with open(backup_file, 'w') as f:
            f.write(content)
        print()

        # Ask if user wants to remove secrets from .env
        print("Your secrets are now in macOS Keychain!")
        print()
        print("Options:")
        print("  1. Keep .env file as-is (secrets remain in both places)")
        print("  2. Comment out migrated secrets in .env (recommended)")
        print("  3. Delete migrated secrets from .env (most secure)")
        print()
        choice = input("Your choice [1/2/3]: ").strip()

        if choice == '2':
            # Comment out migrated secrets
            with open(env_file, 'r') as f:
                lines = f.readlines()

            with open(env_file, 'w') as f:
                f.write(f"# Migrated to macOS Keychain on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("# Use scripts/migrate_secrets.py to manage keychain secrets\n")
                f.write("\n")

                for line in lines:
                    # Check if line contains migrated secret
                    is_migrated = False
                    for key, _ in migrated:
                        if line.startswith(f"{key}="):
                            is_migrated = True
                            break

                    if is_migrated:
                        f.write(f"# {line}")  # Comment out
                    else:
                        f.write(line)

            print()
            print(f"✓ Commented out migrated secrets in {env_file.name}")

        elif choice == '3':
            # Delete migrated secrets
            with open(env_file, 'r') as f:
                lines = f.readlines()

            with open(env_file, 'w') as f:
                f.write(f"# Migrated to macOS Keychain on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("# Use scripts/migrate_secrets.py to manage keychain secrets\n")
                f.write("\n")

                for line in lines:
                    # Check if line contains migrated secret
                    is_migrated = False
                    for key, _ in migrated:
                        if line.startswith(f"{key}="):
                            is_migrated = True
                            break

                    if not is_migrated:
                        f.write(line)

            print()
            print(f"✓ Removed migrated secrets from {env_file.name}")

        else:
            print()
            print(f"✓ Kept .env file unchanged")

        print()
        print("=" * 70)
        print("NEXT STEPS")
        print("=" * 70)
        print()
        print("1. Your secrets are now in macOS Keychain (encrypted)")
        print("2. View them in: Keychain Access app → Search for 'trading-desk'")
        print("3. Your code will automatically use Keychain first, .env as fallback")
        print("4. Backup file saved: " + backup_file.name)
        print()
        print("To add new secrets:")
        print("  from src.core.secrets_manager import set_secret")
        print("  set_secret('NEW_KEY', 'value')")
        print()

    if failed:
        print()
        print("Failed migrations:")
        for key, description in failed:
            print(f"  ✗ {description} ({key})")
        print()
        print("These secrets remain in .env file only.")


if __name__ == '__main__':
    main()
