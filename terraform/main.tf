locals {
  name = "openshift-node-app"
  port = 8080 # probes, containerPort and Service targetPort all use this, as does PORT in the ConfigMap. The Route (openshift/app.yaml) targets 8080 too.

  labels = {
    app                         = local.name
    "app.kubernetes.io/part-of" = local.name
  }

  image_base = "image-registry.openshift-image-registry.svc:5000/${var.namespace}/${local.name}"
  image      = var.image_digest != "" ? "${local.image_base}@${var.image_digest}" : "${local.image_base}:${var.image_tag}"

  # Datadog. The Agent is a second container in the same pod, so the app reaches it on localhost.
  # None of this holds a secret: the API key goes only to the Agent, via the Secret below.
  dd_version = var.image_digest != "" ? substr(var.image_digest, 7, 12) : var.image_tag
  dd_config = var.datadog_enabled ? merge(
    {
      DD_TRACE_ENABLED = "true"
      DD_SERVICE       = local.name
      DD_ENV           = var.datadog_env
      DD_VERSION       = local.dd_version
      DD_SITE          = var.datadog_site
    },
    var.datadog_rum_application_id != "" && var.datadog_rum_client_token != "" ? {
      DD_RUM_APPLICATION_ID = var.datadog_rum_application_id
      DD_RUM_CLIENT_TOKEN   = var.datadog_rum_client_token
    } : {},
  ) : {}

  # The app alone used about 90 MB locally, most of the old 128Mi limit, and dd-trace adds to that.
  app_memory_limit = var.memory_limit != "" ? var.memory_limit : (var.datadog_enabled ? "256Mi" : "128Mi")
  app_cpu_limit    = var.cpu_limit != "" ? var.cpu_limit : "250m"
}

# Runtime settings the app reads from the environment (see src/main.ts).
resource "kubernetes_config_map_v1" "config" {
  metadata {
    name      = "${local.name}-config"
    namespace = var.namespace
    labels    = local.labels
  }

  data = merge(
    {
      NODE_ENV = "production"
      PORT     = tostring(local.port)
    },
    local.dd_config,
    var.extra_env,
  )
}

# Only the Agent container reads this. The app never sees the API key.
resource "kubernetes_secret_v1" "datadog" {
  count = var.datadog_enabled ? 1 : 0

  metadata {
    name      = "${local.name}-datadog"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    DD_API_KEY = var.datadog_api_key
  }

  lifecycle {
    precondition {
      condition     = var.datadog_api_key != ""
      error_message = "datadog_enabled needs datadog_api_key. Export it as TF_VAR_datadog_api_key."
    }
  }
}

resource "kubernetes_deployment_v1" "app" {
  metadata {
    name      = local.name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    replicas = var.replicas
    # Few old ReplicaSets: the Sandbox allows only 30 in the whole namespace, and rollback is by image digest anyway.
    revision_history_limit = 2

    selector {
      match_labels = { app = local.name }
    }

    template {
      metadata {
        labels = { app = local.name }
        # Pods read env only at start; a changed ConfigMap changes this hash and so rolls them.
        annotations = merge(
          {
            "checksum/config" = sha256(jsonencode(kubernetes_config_map_v1.config.data))
            # Pods read their secrets only at start. The version changes when the secret does.
            "checksum/app-secret" = kubernetes_secret_v1.app.metadata[0].resource_version
          },
          # Same idea for the Agent's API key, without putting a hash of the key itself in the cluster.
          # Absent when monitoring is off, so a default deploy leaves the pod template exactly as it was.
          var.datadog_enabled ? { "checksum/datadog-secret" = kubernetes_secret_v1.datadog[0].metadata[0].resource_version } : {},
        )
      }

      spec {
        container {
          name  = "app"
          image = local.image

          port {
            container_port = local.port
          }

          env_from {
            config_map_ref {
              name = kubernetes_config_map_v1.config.metadata[0].name
            }
          }

          # The database address (with its password) and the refresh key come from a Secret, not the ConfigMap.
          env_from {
            secret_ref {
              name = kubernetes_secret_v1.app.metadata[0].name
            }
          }

          readiness_probe {
            http_get {
              path = "/healthz"
              port = local.port
            }
          }

          liveness_probe {
            http_get {
              path = "/healthz"
              port = local.port
            }
          }

          resources {
            requests = { cpu = "50m", memory = "64Mi" }
            limits   = { cpu = local.app_cpu_limit, memory = local.app_memory_limit }
          }
        }

        # The Datadog Agent, only when monitoring is on. It takes traces and metrics from the app over localhost.
        dynamic "container" {
          for_each = var.datadog_enabled ? [1] : []

          content {
            name  = "datadog-agent"
            image = var.datadog_agent_image

            env {
              name = "DD_API_KEY"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.datadog[0].metadata[0].name
                  key  = "DD_API_KEY"
                }
              }
            }
            env {
              name  = "DD_SITE"
              value = var.datadog_site
            }
            env {
              name  = "DD_HOSTNAME"
              value = var.datadog_hostname
            }
            env {
              name  = "DD_APM_ENABLED"
              value = "true"
            }
            env {
              name  = "DD_LOGS_ENABLED"
              value = "false"
            }
            env {
              name  = "DD_PROCESS_CONFIG_PROCESS_COLLECTION_ENABLED"
              value = "false"
            }

            resources {
              requests = { cpu = "50m", memory = "128Mi" }
              limits   = { cpu = "200m", memory = "384Mi" }
            }
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "app" {
  metadata {
    name      = local.name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    selector = { app = local.name }

    port {
      port        = local.port
      target_port = local.port
    }
  }
}
