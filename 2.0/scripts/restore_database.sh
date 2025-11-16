#!/bin/bash
# Database Restore Script
# Restores ivcrush.db from automated backups

set -euo pipefail  # Exit on error, unset vars, pipeline failures

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

# Get absolute script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Use absolute paths
BACKUP_DIR="${PROJECT_ROOT}/backups"
DB_FILE="${PROJECT_ROOT}/data/ivcrush.db"

# Validate we're in the correct project structure
if [ ! -f "${PROJECT_ROOT}/trade.sh" ]; then
    echo -e "${RED}Error: Script not in expected location${NC}" >&2
    echo "Expected trade.sh at: ${PROJECT_ROOT}/trade.sh" >&2
    exit 1
fi

# Check if backups directory exists
if [ ! -d "$BACKUP_DIR" ]; then
    echo -e "${RED}Error: Backups directory not found${NC}"
    echo "Expected: $BACKUP_DIR"
    exit 1
fi

# Ensure backup directory is not a symlink
if [ -L "$BACKUP_DIR" ]; then
    echo -e "${RED}Error: Backup directory is a symlink (security risk)${NC}" >&2
    exit 1
fi

# List available backups
echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
echo -e "${BLUE}${BOLD}    Database Restore - IV Crush 2.0${NC}"
echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
echo ""

# Safer array population (bash 3.2+ compatible)
# Use temporary file to avoid process substitution (bash 4+ only)
backups=()
temp_file=$(mktemp)
trap 'rm -f "$temp_file"' EXIT

find "$BACKUP_DIR" -maxdepth 1 -name "ivcrush_*.db" -type f -print | sort -r > "$temp_file"

while IFS= read -r file; do
    backups+=("$file")
done < "$temp_file"

rm -f "$temp_file"

# Validate and filter backups
valid_backups=()
if [ ${#backups[@]} -gt 0 ]; then
    for backup in "${backups[@]}"; do
        filename=$(basename "$backup")

        # SECURITY: Validate filename matches expected pattern exactly
        # Format: ivcrush_YYYYMMDD_HHMMSS.db
        if [[ "$filename" =~ ^ivcrush_[0-9]{8}_[0-9]{6}\.db$ ]]; then
            valid_backups+=("$backup")
        else
            echo -e "${YELLOW}Warning: Skipping invalid backup file: $filename${NC}" >&2
        fi
    done
fi

if [ ${#valid_backups[@]} -eq 0 ]; then
    echo -e "${RED}Error: No valid backups found in $BACKUP_DIR${NC}"
    echo ""
    echo "Backups are created automatically when you run ./trade.sh"
    echo "Run any analysis command first to create a backup."
    exit 1
fi

echo -e "${GREEN}Available backups:${NC}"
echo ""

# Display numbered list with timestamps and sizes
for i in "${!valid_backups[@]}"; do
    backup="${valid_backups[$i]}"
    filename=$(basename "$backup")

    # Use parameter expansion instead of sed (safer)
    timestamp="${filename#ivcrush_}"  # Remove prefix
    timestamp="${timestamp%.db}"      # Remove suffix
    timestamp="${timestamp//_/ }"     # Replace first underscore with space

    # Get file size portably (macOS and Linux)
    if stat -f%z "$backup" >/dev/null 2>&1; then
        # macOS
        size_bytes=$(stat -f%z "$backup")
    else
        # Linux
        size_bytes=$(stat -c%s "$backup")
    fi

    # Convert to human-readable
    if [ "$size_bytes" -lt 1024 ]; then
        size="${size_bytes}B"
    elif [ "$size_bytes" -lt 1048576 ]; then
        size="$((size_bytes / 1024))KB"
    else
        size="$((size_bytes / 1048576))MB"
    fi

    # Determine age based on modification time
    file_mtime=$(stat -f%m "$backup" 2>/dev/null || stat -c%Y "$backup" 2>/dev/null)
    current_time=$(date +%s)
    age_seconds=$((current_time - file_mtime))

    if [ "$age_seconds" -lt 86400 ]; then
        age="< 1 day"
    elif [ "$age_seconds" -lt 604800 ]; then
        age="< 7 days"
    else
        age="> 7 days"
    fi

    printf "${BOLD}%2d)${NC} %s  ${YELLOW}%s${NC}  ${BLUE}(%s)${NC}\n" $((i+1)) "$timestamp" "$size" "$age"
done

echo ""
echo -e "${YELLOW}Current database:${NC}"
if [ -f "$DB_FILE" ]; then
    current_size=$(ls -lh "$DB_FILE" | awk '{print $5}')
    current_modified=$(ls -l "$DB_FILE" | awk '{print $6, $7, $8}')
    echo "  $DB_FILE - $current_size (modified: $current_modified)"
else
    echo "  No database found (will be created from backup)"
fi

echo ""
read -r -p "Select backup number to restore (or 'q' to quit): " selection

# Sanitize input - allow only alphanumeric and q/Q
selection=$(echo "$selection" | tr -cd '[:alnum:]')

# Handle quit
if [ "$selection" = "q" ] || [ "$selection" = "Q" ]; then
    echo "Restore cancelled."
    exit 0
fi

# Validate selection is a positive integer
if ! [[ "$selection" =~ ^[0-9]+$ ]]; then
    echo -e "${RED}Error: Please enter a number or 'q'${NC}"
    exit 1
fi

# Check range
if [ "$selection" -lt 1 ] || [ "$selection" -gt ${#valid_backups[@]} ]; then
    echo -e "${RED}Error: Selection out of range (1-${#valid_backups[@]})${NC}"
    exit 1
fi

# Get selected backup
selected_backup="${valid_backups[$((selection-1))]}"
backup_name=$(basename "$selected_backup")

# Additional validation: ensure selected file still exists and is a regular file
if [ ! -f "$selected_backup" ]; then
    echo -e "${RED}Error: Selected backup no longer exists${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Selected: $backup_name${NC}"

# Confirm before proceeding
if [ -f "$DB_FILE" ]; then
    echo ""
    echo -e "${RED}${BOLD}WARNING:${NC} This will replace your current database!"
    echo ""
    read -r -p "Create safety backup before restore? (Y/n): " create_safety

    # Sanitize response
    create_safety=$(echo "$create_safety" | tr -cd '[:alpha:]')

    if [ "$create_safety" != "n" ] && [ "$create_safety" != "N" ]; then
        # Use UTC timestamp for safety backup
        safety_backup="${DB_FILE}.pre-restore-$(TZ=UTC date +%Y%m%d_%H%M%S_UTC)"
        echo "Creating safety backup: $safety_backup"

        if cp "$DB_FILE" "$safety_backup"; then
            echo -e "${GREEN}✓ Safety backup created${NC}"
        else
            echo -e "${RED}Error: Failed to create safety backup${NC}"
            exit 1
        fi
    fi

    echo ""
    read -r -p "Continue with restore? (y/N): " confirm

    # Sanitize response
    confirm=$(echo "$confirm" | tr -cd '[:alpha:]')

    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Restore cancelled."
        exit 0
    fi
fi

# Perform restore with atomic operation
echo ""
echo "Restoring database..."

# Create data directory if it doesn't exist
mkdir -p "$(dirname "$DB_FILE")"

# Use temporary file for atomic restore
temp_restore="${DB_FILE}.tmp.$$"
trap 'rm -f "$temp_restore" 2>/dev/null' EXIT ERR

# Copy to temporary location first
if ! cp "$selected_backup" "$temp_restore"; then
    echo -e "${RED}Error: Failed to copy backup file${NC}"
    exit 1
fi

# Verify temporary file integrity before committing
echo "Verifying backup integrity..."
if ! sqlite3 "$temp_restore" "PRAGMA integrity_check;" 2>/dev/null | grep -q "ok"; then
    echo -e "${RED}Error: Backup file is corrupted${NC}"
    echo "Consider selecting a different backup."
    rm -f "$temp_restore"
    exit 1
fi

# Verify file sizes match
if stat -f%z "$selected_backup" >/dev/null 2>&1; then
    # macOS
    orig_size=$(stat -f%z "$selected_backup")
    temp_size=$(stat -f%z "$temp_restore")
else
    # Linux
    orig_size=$(stat -c%s "$selected_backup")
    temp_size=$(stat -c%s "$temp_restore")
fi

if [ "$orig_size" != "$temp_size" ]; then
    echo -e "${RED}Error: File size mismatch (copy incomplete)${NC}"
    rm -f "$temp_restore"
    exit 1
fi

# Atomic move to final location
if mv "$temp_restore" "$DB_FILE"; then
    echo -e "${GREEN}✓ Database file restored${NC}"
    trap - EXIT ERR
else
    echo -e "${RED}Error: Failed to restore database${NC}"
    exit 1
fi

# Final integrity check
echo "Verifying database integrity..."
if sqlite3 "$DB_FILE" "PRAGMA integrity_check;" 2>/dev/null | grep -q "ok"; then
    echo -e "${GREEN}✓ Database integrity check passed${NC}"
else
    echo -e "${RED}⚠️  Database integrity check failed${NC}"
    echo "Database may be corrupted. Consider restoring a different backup."
    exit 1
fi

# Show restored database info
echo ""
echo -e "${BLUE}${BOLD}Restore Complete${NC}"
echo ""
echo "Database restored from: $backup_name"
echo ""

# Show table counts
echo -e "${GREEN}Database contents:${NC}"
sqlite3 "$DB_FILE" <<EOF
.mode column
.headers on
SELECT
    'historical_moves' as table_name,
    COUNT(*) as rows
FROM historical_moves
UNION ALL
SELECT
    'earnings_calendar' as table_name,
    COUNT(*) as rows
FROM earnings_calendar;
EOF

echo ""
echo -e "${GREEN}✓ Restore successful${NC}"
echo ""
echo "You can now use ./trade.sh normally."
echo ""
