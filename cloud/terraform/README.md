# Trading Desk cloud - GCP Monitoring Infrastructure

Terraform configuration for uptime monitoring and alerting.

## What Gets Created

| Resource | Purpose |
|----------|---------|
| **Uptime Check** | Pings `/` every 60 seconds, expects "healthy" in response |
| **Notification Channel** | Webhook to `/alerts/ingest` which forwards to Telegram |
| **Alert Policy: Service Down** | Triggers after 2+ minutes of failed uptime checks |
| **Alert Policy: High Error Rate** | Triggers when 5xx errors exceed 10% |
| **Alert Policy: Container Crashes** | Triggers when container instances drop unexpectedly |

## Prerequisites

1. **Terraform installed** (v1.0+)
   ```bash
   brew install terraform
   ```

2. **GCP CLI authenticated**
   ```bash
   gcloud auth application-default login
   ```

3. **Required GCP APIs enabled**
   ```bash
   gcloud services enable monitoring.googleapis.com
   ```

4. **Cloud Run service deployed** with the `/alerts/ingest` endpoint

## Setup

1. **Create tfvars file from template:**
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```

2. **Edit terraform.tfvars** with your API key:
   ```hcl
   api_key = "your_actual_api_key_here"
   ```

   Get your API key from Secret Manager:
   ```bash
   gcloud secrets versions access latest --secret=trading-desk-secrets | jq -r '.API_KEY'
   ```

3. **Initialize Terraform:**
   ```bash
   terraform init
   ```

4. **Preview changes:**
   ```bash
   terraform plan
   ```

5. **Apply:**
   ```bash
   terraform apply
   ```

## Verify Setup

After applying, verify the uptime check is working:

```bash
# List uptime checks
gcloud monitoring uptime-check-configs list

# View alert policies
gcloud monitoring policies list

# Check notification channels
gcloud monitoring channels list
```

## Test Alerting

To test the full alert pipeline:

1. **Temporarily break the service** (e.g., deploy with bad config)
2. **Wait 2-3 minutes** for uptime check to fail
3. **Check Telegram** for alert notification
4. **Fix the service** and verify "ALERT RESOLVED" message

Or test the webhook directly:

```bash
curl -X POST "https://your-cloud-run-url.run.app/alerts/ingest" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "incident": {
      "incident_id": "test-123",
      "policy_name": "Test Alert",
      "state": "open",
      "started_at": "2025-01-01T12:00:00Z",
      "summary": "This is a test alert",
      "url": "https://console.cloud.google.com/monitoring"
    }
  }'
```

## Customization

### Adjust Check Frequency

Edit `terraform.tfvars`:
```hcl
uptime_check_period = 120  # Check every 2 minutes instead of 1
```

### Change Alert Threshold

Edit `terraform.tfvars`:
```hcl
alert_threshold_duration = 300  # Alert after 5 minutes instead of 2
```

## Cleanup

To remove all monitoring resources:

```bash
terraform destroy
```

## Troubleshooting

### "Permission denied" errors

Grant yourself the Monitoring Admin role:
```bash
gcloud projects add-iam-policy-binding your-gcp-project \
  --member="user:your-email@example.com" \
  --role="roles/monitoring.admin"
```

### Alerts not triggering

1. Check uptime check is passing: `gcloud monitoring uptime-check-configs list`
2. Verify notification channel: `gcloud monitoring channels list`
3. Check Cloud Run logs for `/alerts/ingest` endpoint errors

### Telegram not receiving messages

1. Verify bot token and chat ID in Secret Manager
2. Test bot directly: `/health` command in Telegram
3. Check Cloud Run logs for Telegram API errors
