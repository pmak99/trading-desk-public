#!/bin/bash
# Deploy 5.0 to Cloud Run with database sync
#
# Usage:
#   ./deploy.sh          # Full deploy with DB sync
#   ./deploy.sh --quick  # Deploy without DB sync (faster)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT="trading-desk-prod"
REGION="us-east1"
SERVICE="trading-desk"
GCS_BUCKET="gs://your-gcs-bucket"

cd "$SCRIPT_DIR"

# Parse args
QUICK_MODE=false
if [[ "$1" == "--quick" ]]; then
    QUICK_MODE=true
elif [[ "$1" == "--help" || "$1" == "-h" ]]; then
    echo "Usage: ./deploy.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --quick    Skip database sync (faster deploys)"
    echo "  --help     Show this help message"
    echo ""
    echo "Examples:"
    echo "  ./deploy.sh          # Full deploy with DB sync"
    echo "  ./deploy.sh --quick  # Code-only deploy"
    exit 0
fi

echo "============================================================"
echo "Deploying 5.0 to Cloud Run"
echo "============================================================"

# Step 1: Sync database (unless --quick)
if [[ "$QUICK_MODE" == false ]]; then
    echo ""
    echo "[1/5] Syncing database from 2.0..."

    SOURCE_DB="$ROOT_DIR/2.0/data/ivcrush.db"
    TARGET_DB="$SCRIPT_DIR/data/ivcrush.db"

    if [[ ! -f "$SOURCE_DB" ]]; then
        echo "ERROR: Source database not found: $SOURCE_DB"
        exit 1
    fi

    # Create data directory if needed
    mkdir -p "$SCRIPT_DIR/data"

    # 2.0's ivcrush.db runs in WAL mode -- committed data can live in a
    # sidecar -wal file, not yet folded into the main .db file. A plain
    # `cp` of just the main file produces an incomplete snapshot, and if a
    # stale -wal/-shm pair is left behind at TARGET_DB from earlier local
    # testing (e.g. a Python script against 5.0/data/ivcrush.db), SQLite
    # will try to replay that mismatched WAL against the freshly-copied
    # main file on next open -- "database disk image is malformed"
    # (reproduced live in prod 2026-07-27, revision trading-desk-00141-ckr,
    # shipped straight into the container image via `gcloud run deploy
    # --source .`, since .gcloudignore does not exclude .db-wal/.db-shm).
    # Checkpoint the source into a fully self-contained main file, then
    # clear any stale sidecar files at the target so nothing mismatched
    # rides along into the build context.
    sqlite3 "$SOURCE_DB" "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null
    cp "$SOURCE_DB" "$TARGET_DB"
    rm -f "$TARGET_DB-wal" "$TARGET_DB-shm"
    echo "  Copied: $SOURCE_DB -> $TARGET_DB"

    # Show record counts as sanity check
    TOTAL_MOVES=$(sqlite3 "$TARGET_DB" "SELECT COUNT(*) FROM historical_moves" 2>/dev/null || echo "0")
    TOTAL_TICKERS=$(sqlite3 "$TARGET_DB" "SELECT COUNT(DISTINCT ticker) FROM historical_moves" 2>/dev/null || echo "0")
    echo "  Historical moves: $TOTAL_MOVES records, $TOTAL_TICKERS tickers"

    # Step 2: Upload to GCS
    echo ""
    echo "[2/5] Uploading database to GCS..."
    gsutil cp "$TARGET_DB" "$GCS_BUCKET/ivcrush.db"
    echo "  Uploaded to: $GCS_BUCKET/ivcrush.db"
else
    echo ""
    echo "[1/5] Skipping database sync (--quick mode)"
    echo "[2/5] Skipping GCS upload (--quick mode)"
fi

# Copy shared module into 5.0/ build context
echo ""
echo "Copying common/ module into build context..."
rm -rf "$SCRIPT_DIR/common"
cp -r "$ROOT_DIR/common" "$SCRIPT_DIR/common"
echo "  Copied: $ROOT_DIR/common -> $SCRIPT_DIR/common"

# Step 3: Deploy to Cloud Run
echo ""
echo "[3/5] Deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
    --source . \
    --region "$REGION" \
    --project "$PROJECT" \
    --allow-unauthenticated \
    --timeout=300 \
    --memory=512Mi \
    --min-instances=0 \
    --max-instances=1

# Step 4: Verify the new revision actually took traffic.
#
# `gcloud run deploy` printing "revision X has been deployed and is
# serving 100 percent of traffic" is not proof of that -- reproduced live
# 2026-08-06: it built and readied a new revision (trading-desk-00144-xgg)
# while status.traffic stayed pinned at the previous one
# (trading-desk-00143-2hf, from 2026-07-27) for reasons that never
# surfaced as an error anywhere in the deploy output. The old step 4 here
# only checked HTTP 200 on the service's stable URL, which is identical
# whichever revision answers it -- so a stuck traffic migration looked
# exactly like a successful deploy. Compare the revision Cloud Run says
# was just created against the one actually serving traffic, and treat a
# mismatch as fixable-but-not-silent: force it once, then fail loudly if
# that doesn't resolve it rather than reporting success either way.
echo ""
echo "[4/5] Verifying revision traffic..."

LATEST_CREATED=$(gcloud run services describe "$SERVICE" \
    --region "$REGION" \
    --project "$PROJECT" \
    --format="value(status.latestCreatedRevisionName)")

SERVING_REVISION=$(gcloud run services describe "$SERVICE" \
    --region "$REGION" \
    --project "$PROJECT" \
    --format="value(status.traffic[0].revisionName)")

if [[ "$SERVING_REVISION" != "$LATEST_CREATED" ]]; then
    echo "  WARNING: traffic is on '$SERVING_REVISION', not the just-deployed '$LATEST_CREATED'"
    echo "  Forcing traffic to latest..."
    gcloud run services update-traffic "$SERVICE" \
        --region "$REGION" \
        --project "$PROJECT" \
        --to-latest > /dev/null

    SERVING_REVISION=$(gcloud run services describe "$SERVICE" \
        --region "$REGION" \
        --project "$PROJECT" \
        --format="value(status.traffic[0].revisionName)")

    if [[ "$SERVING_REVISION" != "$LATEST_CREATED" ]]; then
        echo "  ERROR: still serving '$SERVING_REVISION' after forcing traffic to latest."
        echo "  Deploy did NOT go live. Check 'gcloud run revisions describe $LATEST_CREATED --region $REGION' for why."
        exit 1
    fi
    echo "  Fixed: '$SERVING_REVISION' is now serving 100% of traffic"
else
    echo "  OK: '$SERVING_REVISION' is serving 100% of traffic"
fi

# Step 5: Connectivity check
echo ""
echo "[5/5] Verifying connectivity..."
SERVICE_URL=$(gcloud run services describe "$SERVICE" \
    --region "$REGION" \
    --project "$PROJECT" \
    --format="value(status.url)")

echo "  Service URL: $SERVICE_URL"

# Quick health check (root endpoint, no auth required)
echo "  Testing connectivity..."
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$SERVICE_URL/")
if [[ "$HEALTH" == "200" ]]; then
    echo "  Connectivity: OK"
else
    echo "  Connectivity: WARNING (HTTP $HEALTH)"
fi

echo ""
echo "============================================================"
echo "Deployment complete!"
echo "============================================================"
echo ""
echo "Test commands:"
echo "  curl -H 'X-API-Key: \$API_KEY' '$SERVICE_URL/api/health'"
echo "  curl -H 'X-API-Key: \$API_KEY' '$SERVICE_URL/api/analyze?ticker=AAPL'"
