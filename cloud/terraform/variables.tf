# Input variables for monitoring infrastructure

variable "project_id" {
  description = "GCP Project ID"
  type        = string
  default     = "your-gcp-project"
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "us-east1"
}

variable "service_url" {
  description = "Cloud Run service URL (without trailing slash)"
  type        = string
  default     = "https://your-cloud-run-url.run.app"
}

variable "service_name" {
  description = "Cloud Run service name for display"
  type        = string
  default     = "Trading Desk"
}

variable "alert_webhook_path" {
  description = "Path for alert webhook endpoint"
  type        = string
  default     = "/alerts/ingest"
}

variable "api_key" {
  description = "API key for authenticating webhook requests"
  type        = string
  sensitive   = true
}

variable "uptime_check_period" {
  description = "How often to check uptime (in seconds). Min 60."
  type        = number
  default     = 60
}

variable "alert_threshold_duration" {
  description = "How long service must be down before alerting (in seconds)"
  type        = number
  default     = 120 # 2 minutes - avoids flapping during cold starts
}
