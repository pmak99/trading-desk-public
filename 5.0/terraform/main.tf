# Trading Desk 5.0 - GCP Infrastructure
#
# Creates:
#   - Cloud Scheduler job: calls /dispatch every 15 min to trigger timed jobs
#     (sentiment-scan at 06:30, morning-digest at 07:30, etc.)
#   - Uptime checks and alerting for Cloud Run service downtime.
#     Alerts are sent to Telegram via webhook to the existing service.
#
# Usage:
#   cd 5.0/terraform
#   terraform init
#   terraform plan
#   terraform apply

terraform {
  required_version = ">= 1.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # Optional: Store state in GCS bucket for team access
  # backend "gcs" {
  #   bucket = "trading-desk-terraform-state"
  #   prefix = "monitoring"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ─── Cloud Scheduler ──────────────────────────────────────────────────────────
# Calls /dispatch every 15 minutes. The dispatcher checks the current ET time
# and routes to the correct job (sentiment-scan, morning-digest, etc.) via a
# ±7 minute window. Most invocations return no_job immediately.

resource "google_cloud_scheduler_job" "dispatch" {
  name             = "trading-desk-dispatch"
  description      = "Trigger job dispatcher every 15 minutes"
  schedule         = "*/15 * * * *"
  time_zone        = "America/New_York"
  attempt_deadline = "320s"

  http_target {
    http_method = "POST"
    uri         = "${var.service_url}/dispatch"

    headers = {
      "Content-Type" = "application/json"
      "X-API-Key"    = var.api_key
    }

    body = base64encode("{}")
  }

  retry_config {
    retry_count          = 3
    min_backoff_duration = "5s"
    max_backoff_duration = "60s"
  }
}
