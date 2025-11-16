#!/bin/bash
# Database Restore Script
# Restores ivcrush.db from automated backups

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

# Change to project root
cd "$(dirname "$0")/.."

BACKUP_DIR="backups"
DB_FILE="data/ivcrush.db"

# Check if backups directory exists
if [ ! -d "$BACKUP_DIR" ]; then
    echo -e "${RED}Error: Backups directory not found${NC}"
    echo "Expected: $BACKUP_DIR"
    exit 1
fi

# List available backups
echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
echo -e "${BLUE}${BOLD}    Database Restore - IV Crush 2.0${NC}"
echo -e "${BLUE}${BOLD}═══════════════════════════════════════${NC}"
echo ""

backups=($(find "$BACKUP_DIR" -name "ivcrush_*.db" -type f | sort -r))

if [ ${#backups[@]} -eq 0 ]; then
    echo -e "${RED}No backups found in $BACKUP_DIR${NC}"
    echo ""
    echo "Backups are created automatically when you run ./trade.sh"
    echo "Run any analysis command first to create a backup."
    exit 1
fi

echo -e "${GREEN}Available backups:${NC}"
echo ""

# Display numbered list with timestamps and sizes
for i in "${!backups[@]}"; do
    backup="${backups[$i]}"
    filename=$(basename "$backup")
    timestamp=$(echo "$filename" | sed 's/ivcrush_\(.*\)\.db/\1/' | sed 's/_/ /')
    size=$(ls -lh "$backup" | awk '{print $5}')

    # Determine age
    if find "$backup" -mtime -1 2>/dev/null | grep -q .; then
        age="< 1 day"
    elif find "$backup" -mtime -7 2>/dev/null | grep -q .; then
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
read -p "Select backup number to restore (or 'q' to quit): " selection

# Handle quit
if [ "$selection" = "q" ] || [ "$selection" = "Q" ]; then
    echo "Restore cancelled."
    exit 0
fi

# Validate selection
if ! [[ "$selection" =~ ^[0-9]+$ ]] || [ "$selection" -lt 1 ] || [ "$selection" -gt ${#backups[@]} ]; then
    echo -e "${RED}Invalid selection${NC}"
    exit 1
fi

# Get selected backup
selected_backup="${backups[$((selection-1))]}"
backup_name=$(basename "$selected_backup")

echo ""
echo -e "${YELLOW}Selected: $backup_name${NC}"

# Confirm before proceeding
if [ -f "$DB_FILE" ]; then
    echo ""
    echo -e "${RED}${BOLD}WARNING:${NC} This will replace your current database!"
    echo ""
    read -p "Create safety backup before restore? (Y/n): " create_safety

    if [ "$create_safety" != "n" ] && [ "$create_safety" != "N" ]; then
        safety_backup="${DB_FILE}.pre-restore-$(date +%Y%m%d_%H%M%S)"
        echo "Creating safety backup: $safety_backup"
        cp "$DB_FILE" "$safety_backup"
        echo -e "${GREEN}✓ Safety backup created${NC}"
    fi

    echo ""
    read -p "Continue with restore? (y/N): " confirm

    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Restore cancelled."
        exit 0
    fi
fi

# Perform restore
echo ""
echo "Restoring database..."

# Create data directory if it doesn't exist
mkdir -p "$(dirname "$DB_FILE")"

# Copy backup to database location
if cp "$selected_backup" "$DB_FILE"; then
    echo -e "${GREEN}✓ Database file restored${NC}"
else
    echo -e "${RED}Failed to restore database${NC}"
    exit 1
fi

# Verify database integrity
echo "Verifying database integrity..."
if sqlite3 "$DB_FILE" "PRAGMA integrity_check;" | grep -q "ok"; then
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
