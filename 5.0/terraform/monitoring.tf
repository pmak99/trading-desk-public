# GCP Monitoring: Uptime Checks, Notification Channels, and Alert Policies
#
# Architecture:
#   Uptime Check (every 60s)
#     -> Alert Policy (triggers after 2 failures)
#       -> Webhook Notification Channel
#         -> /alerts/ingest endpoint
#           -> Telegram message

# -----------------------------------------------------------------------------
# Uptime Check
# -----------------------------------------------------------------------------

resource "google_monitoring_uptime_check_config" "trading_desk_health" {
  display_name = "${var.service_name} - Health Check"
  timeout      = "10s"
  period       = "${var.uptime_check_period}s"

  http_check {
    path           = "/"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"

    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = replace(var.service_url, "https://", "")
    }
  }

  content_matchers {
    content = "healthy"
    matcher = "CONTAINS_STRING"
  }

  checker_type = "STATIC_IP_CHECKERS"
}

# -----------------------------------------------------------------------------
# Notification Channel (Webhook to Telegram forwarder)
# -----------------------------------------------------------------------------

# Using pubsub channel type which is more reliable than webhook
# The pubsub topic triggers a Cloud Run endpoint via push subscription
resource "google_monitoring_notification_channel" "telegram_webhook" {
  display_name = "${var.service_name} - Telegram Alerts"
  type         = "webhook_basicauth"

  labels = {
    url      = "${var.service_url}${var.alert_webhook_path}"
    username = "monitoring"
  }

  sensitive_labels {
    password = var.api_key
  }

  description = "Forwards GCP Monitoring alerts to Telegram via Cloud Run webhook"
}

# -----------------------------------------------------------------------------
# Alert Policy - Service Unavailable
# -----------------------------------------------------------------------------

resource "google_monitoring_alert_policy" "service_down" {
  display_name = "${var.service_name} - Service Unavailable"
  combiner     = "OR"

  conditions {
    display_name = "Uptime check failing"

    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id = \"${google_monitoring_uptime_check_config.trading_desk_health.uptime_check_id}\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "${var.alert_threshold_duration}s"

      trigger {
        count = 1
      }

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }

  notification_channels = [
    google_monitoring_notification_channel.telegram_webhook.id
  ]

  alert_strategy {
    auto_close = "604800s" # 7 days
  }

  documentation {
    content   = <<-EOT
      ## ${var.service_name} is DOWN

      The health check endpoint is not responding or returning errors.

      **Immediate Actions:**
      1. Check Cloud Run logs: `gcloud logs read --service=trading-desk --limit=50`
      2. Verify service status: `gcloud run services describe trading-desk --region=${var.region}`
      3. Check for recent deployments in Cloud Build

      **Service URL:** ${var.service_url}
    EOT
    mime_type = "text/markdown"
  }

  user_labels = {
    service  = "trading-desk"
    severity = "critical"
  }
}

# -----------------------------------------------------------------------------
# Alert Policy - High Error Rate (5xx errors)
# -----------------------------------------------------------------------------

resource "google_monitoring_alert_policy" "high_error_rate" {
  display_name = "${var.service_name} - High Error Rate"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run 5xx error rate > 10%"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"trading-desk\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.1 # 10% of requests
      duration        = "300s"

      trigger {
        count = 1
      }

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [
    google_monitoring_notification_channel.telegram_webhook.id
  ]

  alert_strategy {
    auto_close = "604800s"
  }

  documentation {
    content   = <<-EOT
      ## ${var.service_name} - High Error Rate

      More than 10% of requests are returning 5xx errors.

      **Check:**
      1. Recent deployments
      2. External API failures (Tradier, Alpha Vantage)
      3. Database issues
    EOT
    mime_type = "text/markdown"
  }

  user_labels = {
    service  = "trading-desk"
    severity = "warning"
  }
}

# -----------------------------------------------------------------------------
# Alert Policy - Container Crashes
# -----------------------------------------------------------------------------

resource "google_monitoring_alert_policy" "container_restarts" {
  display_name = "${var.service_name} - Container Crashes"
  combiner     = "OR"

  conditions {
    display_name = "Container instance count dropped"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"trading-desk\" AND metric.type = \"run.googleapis.com/container/instance_count\""
      comparison      = "COMPARISON_LT"
      threshold_value = 0
      duration        = "60s"

      trigger {
        count = 1
      }

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = [
    google_monitoring_notification_channel.telegram_webhook.id
  ]

  alert_strategy {
    auto_close = "604800s"
  }

  documentation {
    content   = "Container crashed or was terminated unexpectedly."
    mime_type = "text/markdown"
  }

  user_labels = {
    service  = "trading-desk"
    severity = "critical"
  }
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "uptime_check_id" {
  description = "ID of the uptime check"
  value       = google_monitoring_uptime_check_config.trading_desk_health.uptime_check_id
}

output "notification_channel_id" {
  description = "ID of the Telegram notification channel"
  value       = google_monitoring_notification_channel.telegram_webhook.id
}

output "alert_policy_ids" {
  description = "IDs of created alert policies"
  value = {
    service_down      = google_monitoring_alert_policy.service_down.id
    high_error_rate   = google_monitoring_alert_policy.high_error_rate.id
    container_crashes = google_monitoring_alert_policy.container_restarts.id
  }
}
