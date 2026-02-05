#!/bin/bash
# Deploy 5.0 to Cloud Run with database sync
#
# Usage:
#   ./deploy.sh          # Full deploy with DB sync
#   ./deploy.sh --quick  # Deploy without DB sync (faster)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT="your-gcp-project"
REGION="us-east1"
SERVICE="trading-desk"
GCS_BUCKET="gs://your-gcs-bucket"

cd "$SCRIPT_DIR"

# Parse args
QUICK_MODE=false
if [[ "$1" == "--quick" ]]; then
    QUICK_MODE=true
fi

echo "============================================================"
echo "Deploying 5.0 to Cloud Run"
echo "============================================================"

# Step 1: Sync database (unless --quick)
if [[ "$QUICK_MODE" == false ]]; then
    echo ""
    echo "[1/4] Syncing database from 2.0..."

    SOURCE_DB="$ROOT_DIR/2.0/data/ivcrush.db"
    TARGET_DB="$SCRIPT_DIR/data/ivcrush.db"

    if [[ ! -f "$SOURCE_DB" ]]; then
        echo "ERROR: Source database not found: $SOURCE_DB"
        exit 1
    fi

    # Create data directory if needed
    mkdir -p "$SCRIPT_DIR/data"

    # Copy database
    cp "$SOURCE_DB" "$TARGET_DB"
    echo "  Copied: $SOURCE_DB -> $TARGET_DB"

    # Show RDDT quarters as sanity check
    RDDT_COUNT=$(sqlite3 "$TARGET_DB" "SELECT COUNT(*) FROM historical_moves WHERE ticker = 'RDDT'" 2>/dev/null || echo "0")
    echo "  RDDT quarters: $RDDT_COUNT"

    # Step 2: Upload to GCS
    echo ""
    echo "[2/4] Uploading database to GCS..."
    gsutil cp "$TARGET_DB" "$GCS_BUCKET/ivcrush.db"
    echo "  Uploaded to: $GCS_BUCKET/ivcrush.db"
else
    echo ""
    echo "[1/4] Skipping database sync (--quick mode)"
    echo "[2/4] Skipping GCS upload (--quick mode)"
fi

# Step 3: Deploy to Cloud Run
echo ""
echo "[3/4] Deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
    --source . \
    --region "$REGION" \
    --project "$PROJECT" \
    --allow-unauthenticated \
    --timeout=300 \
    --memory=512Mi \
    --min-instances=0 \
    --max-instances=1

# Step 4: Verify deployment
echo ""
echo "[4/4] Verifying deployment..."
SERVICE_URL=$(gcloud run services describe "$SERVICE" \
    --region "$REGION" \
    --project "$PROJECT" \
    --format="value(status.url)")

echo "  Service URL: $SERVICE_URL"

# Quick health check
echo "  Testing health endpoint..."
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "$SERVICE_URL/")
if [[ "$HEALTH" == "200" ]]; then
    echo "  Health check: OK"
else
    echo "  Health check: WARNING (HTTP $HEALTH)"
fi

echo ""
echo "============================================================"
echo "Deployment complete!"
echo "============================================================"
echo ""
echo "Test commands:"
echo "  curl -H 'X-API-Key: \$API_KEY' '$SERVICE_URL/api/health'"
echo "  curl -H 'X-API-Key: \$API_KEY' '$SERVICE_URL/api/analyze?ticker=AAPL'"
