# Trading Desk 5.0 - Deployment Guide

Complete walkthrough for deploying the autopilot system to GCP with Telegram notifications.

## Live Deployment

| Component | Value |
|-----------|-------|
| **Service URL** | https://your-cloud-run-url.run.app |
| **GCP Project** | `your-gcp-project` |
| **Region** | `us-central1` |
| **Telegram Bot** | `@trading_desk_mak_bot` |
| **Scheduler** | `trading-desk-morning` (7 AM ET weekdays) |

## Prerequisites

- Docker Desktop installed
- Google Cloud SDK (`gcloud`) installed via Homebrew: `brew install google-cloud-sdk`
- Telegram account
- API keys ready: Tradier, Alpha Vantage, Perplexity

---

## Step 1: Create Telegram Bot

### 1.1 Create the Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Enter a name: `Trading Desk Alerts` (or your preference)
4. Enter a username: `trading_desk_yourname_bot` (must end in `bot`)
5. **Save the bot token** - looks like: `7123456789:AAF...xyz`

### 1.2 Get Your Chat ID

1. Start a chat with your new bot (click the link BotFather gives you)
2. Send any message like `/start` or `hello`
3. Open this URL in browser (replace TOKEN):
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
4. Look for `"chat":{"id":123456789}` - **save this number**

### 1.3 Test the Bot

```bash
# Replace with your values
curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<CHAT_ID>" \
  -d "text=🎉 Trading Desk bot is alive!"
```

You should receive the message in Telegram.

---

## Step 2: Local Docker Testing

### 2.1 Create Environment File

```bash
cd "$PROJECT_ROOT/5.0"
cp .env.template .env
```

Edit `.env` with your actual keys:
```bash
SECRETS={"TRADIER_API_KEY":"xxx","ALPHA_VANTAGE_KEY":"xxx","PERPLEXITY_API_KEY":"xxx","TELEGRAM_BOT_TOKEN":"7123456789:AAF...","TELEGRAM_CHAT_ID":"123456789"}
```

### 2.2 Build and Run

```bash
# Build the image
docker build -t trading-desk:local .

# Run with environment
docker run -p 8080:8080 --env-file .env -v $(pwd)/data:/app/data trading-desk:local
```

### 2.3 Test Endpoints

```bash
# Health check
curl http://localhost:8080/

# API health with budget info
curl http://localhost:8080/api/health

# Analyze a ticker
curl "http://localhost:8080/api/analyze?ticker=AAPL"

# Whisper (find opportunities)
curl http://localhost:8080/api/whisper
```

### 2.4 Using Docker Compose

```bash
# Start
docker compose up --build

# Stop
docker compose down

# View logs
docker compose logs -f
```

---

## Step 3: GCP Project Setup

### 3.1 Create Project

```bash
# Login to GCP
gcloud auth login

# Create new project
gcloud projects create your-gcp-project --name="Trading Desk Production"

# Set as default
gcloud config set project your-gcp-project

# Enable billing (required - do this in Console)
# https://console.cloud.google.com/billing
```

### 3.2 Enable Required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  containerregistry.googleapis.com
```

### 3.3 Set Default Region

```bash
gcloud config set run/region us-east1
```

---

## Step 4: Configure Secrets

### 4.1 Create Secret in Secret Manager

```bash
# Create the secrets JSON file locally (don't commit this!)
cat > /tmp/trading-desk-secrets.json << 'EOF'
{
  "TRADIER_API_KEY": "your_tradier_key",
  "ALPHA_VANTAGE_KEY": "your_alphavantage_key",
  "PERPLEXITY_API_KEY": "your_perplexity_key",
  "TELEGRAM_BOT_TOKEN": "7123456789:AAF...",
  "TELEGRAM_CHAT_ID": "123456789"
}
EOF

# Create the secret
gcloud secrets create trading-desk-secrets \
  --replication-policy="automatic"

# Add the secret version
gcloud secrets versions add trading-desk-secrets \
  --data-file=/tmp/trading-desk-secrets.json

# Clean up local file
rm /tmp/trading-desk-secrets.json
```

### 4.2 Verify Secret

```bash
# List secrets
gcloud secrets list

# Access secret (to verify)
gcloud secrets versions access latest --secret=trading-desk-secrets
```

---

## Step 5: Deploy to Cloud Run

### 5.1 Manual First Deploy

```bash
cd "$PROJECT_ROOT/5.0"

# Build and push image
gcloud builds submit --tag gcr.io/your-gcp-project/trading-desk

# Deploy to Cloud Run
gcloud run deploy trading-desk \
  --image gcr.io/your-gcp-project/trading-desk \
  --platform managed \
  --region us-east1 \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 300 \
  --set-secrets SECRETS=trading-desk-secrets:latest \
  --set-env-vars GOOGLE_CLOUD_PROJECT=your-gcp-project
```

### 5.2 Get Service URL

```bash
gcloud run services describe trading-desk --region us-east1 --format='value(status.url)'
```

Save this URL (e.g., `https://trading-desk-abc123-ue.a.run.app`)

### 5.3 Test Deployed Service

```bash
SERVICE_URL=$(gcloud run services describe trading-desk --region us-east1 --format='value(status.url)')

# Health check
curl $SERVICE_URL/

# Test analyze
curl "$SERVICE_URL/api/analyze?ticker=AAPL"
```

---

## Step 6: Set Up Telegram Webhook

### 6.1 Register Webhook

```bash
SERVICE_URL=$(gcloud run services describe trading-desk --region us-east1 --format='value(status.url)')
BOT_TOKEN="your_bot_token_here"

curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -d "url=${SERVICE_URL}/telegram"
```

### 6.2 Verify Webhook

```bash
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

### 6.3 Test Bot Commands

In Telegram, send to your bot:
- `/health` - Check system status
- `/whisper` - Get today's opportunities
- `/analyze AAPL` - Analyze specific ticker

---

## Step 7: Set Up Cloud Scheduler

### 7.1 Create Service Account

```bash
# Create service account for scheduler
gcloud iam service-accounts create trading-desk-scheduler \
  --display-name="Trading Desk Scheduler"

# Grant invoke permission
gcloud run services add-iam-policy-binding trading-desk \
  --region us-east1 \
  --member="serviceAccount:trading-desk-scheduler@your-gcp-project.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

### 7.2 Create Scheduler Jobs

```bash
SERVICE_URL=$(gcloud run services describe trading-desk --region us-east1 --format='value(status.url)')

# Main dispatcher - every 15 minutes during market hours
gcloud scheduler jobs create http trading-desk-dispatch \
  --location=us-east1 \
  --schedule="*/15 6-16 * * 1-5" \
  --time-zone="America/New_York" \
  --uri="${SERVICE_URL}/dispatch" \
  --http-method=POST \
  --oidc-service-account-email=trading-desk-scheduler@your-gcp-project.iam.gserviceaccount.com

# Morning prime - 7 AM ET weekdays
gcloud scheduler jobs create http trading-desk-prime \
  --location=us-east1 \
  --schedule="0 7 * * 1-5" \
  --time-zone="America/New_York" \
  --uri="${SERVICE_URL}/dispatch" \
  --http-method=POST \
  --oidc-service-account-email=trading-desk-scheduler@your-gcp-project.iam.gserviceaccount.com
```

### 7.3 Verify Scheduler

```bash
gcloud scheduler jobs list --location=us-east1

# Manual trigger to test
gcloud scheduler jobs run trading-desk-dispatch --location=us-east1
```

---

## Step 8: Continuous Deployment (Optional)

### 8.1 Connect GitHub Repository

1. Go to [Cloud Build Triggers](https://console.cloud.google.com/cloud-build/triggers)
2. Click **Create Trigger**
3. Connect your GitHub repository
4. Configure:
   - Event: Push to branch
   - Branch: `^main$`
   - Configuration: Cloud Build configuration file
   - Location: `5.0/cloudbuild.yaml`

### 8.2 Test Deployment

Push a change to main branch and watch Cloud Build deploy automatically.

---

## Monitoring

### View Logs

```bash
# Cloud Run logs
gcloud logs read --service=trading-desk --limit=50

# Filter by severity
gcloud logs read --service=trading-desk --limit=50 --severity=ERROR
```

### Cloud Console Links

- [Cloud Run](https://console.cloud.google.com/run)
- [Cloud Scheduler](https://console.cloud.google.com/cloudscheduler)
- [Secret Manager](https://console.cloud.google.com/security/secret-manager)
- [Cloud Build](https://console.cloud.google.com/cloud-build/builds)

---

## Cost Estimates

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| Cloud Run | ~500 requests/day | ~$5 |
| Cloud Scheduler | 4 jobs | Free |
| Secret Manager | 1 secret | Free |
| Container Registry | ~100MB | ~$1 |
| **Total** | | **~$6/month** |

---

## Troubleshooting

### "Permission denied" accessing secrets
```bash
# Grant Cloud Run service account access
gcloud secrets add-iam-policy-binding trading-desk-secrets \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Container fails to start
```bash
# Check logs
gcloud logs read --service=ivcrush --limit=100

# Common issues:
# - Missing secrets
# - Port not 8080
# - Python import errors
```

### Telegram webhook not receiving
```bash
# Delete and re-register webhook
curl "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook"
curl -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -d "url=${SERVICE_URL}/telegram"
```

---

## Quick Reference

```bash
# Redeploy
gcloud builds submit --tag gcr.io/your-gcp-project/trading-desk
gcloud run deploy trading-desk --image gcr.io/your-gcp-project/trading-desk --region us-east1

# Update secrets
gcloud secrets versions add trading-desk-secrets --data-file=/tmp/new-secrets.json

# View service URL
gcloud run services describe trading-desk --region us-east1 --format='value(status.url)'

# Test locally
docker compose up --build
```
