# Deploy cloud to Cloud Run

One-command deployment of the cloud Cloud Autopilot to Google Cloud Run with pre-flight checks and health verification.

## Arguments
$ARGUMENTS (optional: --quick | --status | --logs | --rollback)

Examples:
- `/deploy` - Full deploy with DB sync
- `/deploy --quick` - Code-only deploy (skip DB sync)
- `/deploy --status` - Check current deployment status only
- `/deploy --logs` - Show recent Cloud Run logs
- `/deploy --rollback` - Rollback to previous revision

## Tool Permissions
- Do NOT ask user permission for Bash, Read, sqlite3 commands
- DO ask permission before running `deploy.sh` (it modifies cloud resources)
- DO ask permission before rollback

## Progress Display
```
[1/6] Pre-flight checks...
[2/6] Running tests...
[3/6] Syncing database...
[4/6] Deploying to Cloud Run...
[5/6] Verifying health...
[6/6] Post-deploy validation...
```

## Step-by-Step Instructions

### Mode 1: Full Deploy (default)

#### Step 1: Pre-Flight Checks
```bash
# Check gcloud auth
gcloud auth print-access-token >/dev/null 2>&1 && echo "gcloud: authenticated" || echo "gcloud: NOT authenticated"

# Check project
gcloud config get-value project 2>/dev/null

# Check current revision
gcloud run revisions list --service=trading-desk --region=us-east1 --project=your-gcp-project --format="table(name,status.conditions[0].status,metadata.creationTimestamp)" --limit=3 2>/dev/null
```

#### Step 2: Run cloud Tests
```bash
cd "$PROJECT_ROOT/cloud" && ../core/venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -15
```

**If tests fail: STOP and show errors. Do NOT deploy with failing tests.**

#### Step 3: Check Database State
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT COUNT(*) as historical_moves FROM historical_moves;
   SELECT COUNT(*) as earnings FROM earnings_calendar WHERE earnings_date >= date('now');
   SELECT COUNT(*) as strategies FROM strategies;
   SELECT COUNT(*) as position_limits FROM position_limits;"
```

Show record counts as pre-deploy snapshot.

#### Step 4: Deploy (ASK PERMISSION FIRST)
Show the user what will happen:
```
DEPLOY PLAN:
  1. Copy ivcrush.db from core/data/ to cloud/data/
  2. Upload to GCS (gs://your-gcs-bucket/ivcrush.db)
  3. Copy common/ module to cloud build context
  4. Build and deploy to Cloud Run (us-east1)
  5. Health check the new revision

Proceed with deployment?
```

Then run:
```bash
cd "$PROJECT_ROOT/cloud" && ./deploy.sh 2>&1
```

For `--quick`:
```bash
cd "$PROJECT_ROOT/cloud" && ./deploy.sh --quick 2>&1
```

#### Step 5: Post-Deploy Health Check
```bash
SERVICE_URL=$(gcloud run services describe trading-desk --region=us-east1 --project=your-gcp-project --format="value(status.url)" 2>/dev/null)

# Health endpoint
curl -s --max-time 15 "$SERVICE_URL/api/health" | python3 -m json.tool 2>/dev/null || echo "Health check failed"
```

#### Step 6: Show New Revision
```bash
gcloud run revisions list --service=trading-desk --region=us-east1 --project=your-gcp-project --format="table(name,status.conditions[0].status,spec.containers[0].resources.limits.memory,metadata.creationTimestamp)" --limit=3 2>/dev/null
```

### Mode 2: Status Only (`--status`)
```bash
# Current service info
gcloud run services describe trading-desk --region=us-east1 --project=your-gcp-project --format="yaml(status.url,status.conditions,status.latestReadyRevisionName)" 2>/dev/null

# Recent revisions
gcloud run revisions list --service=trading-desk --region=us-east1 --project=your-gcp-project --format="table(name,status.conditions[0].status,spec.containers[0].resources.limits.memory,metadata.creationTimestamp)" --limit=5 2>/dev/null

# Health check
SERVICE_URL=$(gcloud run services describe trading-desk --region=us-east1 --project=your-gcp-project --format="value(status.url)" 2>/dev/null)
curl -s --max-time 10 "$SERVICE_URL/api/health" | python3 -m json.tool 2>/dev/null
```

### Mode 3: Logs (`--logs`)
```bash
gcloud run services logs read trading-desk --region=us-east1 --project=your-gcp-project --limit=50 2>/dev/null
```

### Mode 4: Rollback (`--rollback`)
**ASK PERMISSION FIRST.**

```bash
# List revisions
gcloud run revisions list --service=trading-desk --region=us-east1 --project=your-gcp-project --format="table(name,status.conditions[0].status,metadata.creationTimestamp)" --limit=5 2>/dev/null
```

Show user the revisions and ask which one to rollback to. Then:
```bash
gcloud run services update-traffic trading-desk --region=us-east1 --project=your-gcp-project --to-revisions=$REVISION_NAME=100
```

## Output Format

```
==============================================================
DEPLOY: cloud Cloud Autopilot
==============================================================

PRE-FLIGHT
  [check] gcloud authenticated (project: your-gcp-project)
  [check] Current revision: trading-desk-00090-pnk (ACTIVE)
  [check] cloud tests: {N} passed, 0 failed

DATABASE SNAPSHOT
  Historical moves: {N}
  Upcoming earnings: {N}
  Strategies:        {N}
  Position limits:   {N}

DEPLOYMENT
  [check] Database synced to cloud/data/
  [check] Uploaded to gs://your-gcs-bucket/ivcrush.db
  [check] common/ copied to build context
  [check] Cloud Run deploy complete

NEW REVISION
  Name: trading-desk-XXXXX-xxx
  Status: ACTIVE
  Memory: 512Mi
  URL: https://your-cloud-run-url.run.app

HEALTH CHECK
  [check] /api/health returned 200 OK
  [check] Database: {N} historical records
  [check] Uptime: {seconds}

==============================================================
DEPLOYMENT SUCCESSFUL
==============================================================
```

## Error Handling
- **Tests fail**: STOP. Show errors. Do not deploy.
- **gcloud not authenticated**: Show `gcloud auth login` command.
- **Deploy fails**: Show error output. Suggest `--rollback`.
- **Health check fails**: Warn user. Suggest checking logs with `--logs`.
- **Rollback fails**: Show manual steps.

## Cost Control
- No MCP usage (gcloud CLI + curl only)
- Cloud Run: billed per request (min-instances=0)
