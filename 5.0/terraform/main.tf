# Trading Desk 5.0 - GCP Monitoring Infrastructure
#
# Creates uptime checks and alerting for Cloud Run service downtime.
# Alerts are sent to Telegram via webhook to the existing service.
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
