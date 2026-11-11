# The keeper: undoes the Developer Sandbox idler.
#
# The Sandbox scales every Deployment in the namespace to 0 once its pod has run for a fixed
# period (about 12 hours), whatever the traffic. The Idler object that sets the period is not
# readable, let alone editable, by the Sandbox user, so the only cure from inside the namespace is
# to scale back up. This CronJob does that: every few minutes it looks at the app and database
# Deployments and sets any that sit below `replicas` back to it. Its own pod lives a few seconds,
# so the idler never catches it. Downtime per idling is the schedule interval plus start-up.
#
# It runs under its own ServiceAccount with a namespace-scoped Role (the Sandbox allows those;
# cluster-scoped RBAC it does not). The Role only covers reading and scaling Deployments.

locals {
  keeper_name   = "${local.name}-keeper"
  keeper_labels = { app = local.keeper_name, "app.kubernetes.io/part-of" = local.name }

  # Database first: the app retries its connection for about a minute, so order barely matters,
  # but this gives the database a head start.
  keeper_deployments = [
    kubernetes_deployment_v1.db.metadata[0].name,
    kubernetes_deployment_v1.app.metadata[0].name,
  ]
}

resource "kubernetes_service_account_v1" "keeper" {
  count = var.keeper_enabled ? 1 : 0

  metadata {
    name      = local.keeper_name
    namespace = var.namespace
    labels    = local.keeper_labels
  }
}

resource "kubernetes_role_v1" "keeper" {
  count = var.keeper_enabled ? 1 : 0

  metadata {
    name      = local.keeper_name
    namespace = var.namespace
    labels    = local.keeper_labels
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments"]
    verbs      = ["get", "list", "patch"]
  }

  rule {
    api_groups = ["apps"]
    resources  = ["deployments/scale"]
    verbs      = ["get", "patch", "update"]
  }
}

resource "kubernetes_role_binding_v1" "keeper" {
  count = var.keeper_enabled ? 1 : 0

  metadata {
    name      = local.keeper_name
    namespace = var.namespace
    labels    = local.keeper_labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.keeper[0].metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.keeper[0].metadata[0].name
    namespace = var.namespace
  }
}

resource "kubernetes_cron_job_v1" "keeper" {
  count = var.keeper_enabled ? 1 : 0

  metadata {
    name      = local.keeper_name
    namespace = var.namespace
    labels    = local.keeper_labels
  }

  spec {
    schedule                      = var.keeper_schedule
    concurrency_policy            = "Forbid"
    starting_deadline_seconds     = 120
    successful_jobs_history_limit = 1
    failed_jobs_history_limit     = 1

    job_template {
      metadata {
        labels = local.keeper_labels
      }

      spec {
        backoff_limit              = 1
        active_deadline_seconds    = 120
        ttl_seconds_after_finished = 600

        template {
          metadata {
            labels = local.keeper_labels
          }

          spec {
            service_account_name = kubernetes_service_account_v1.keeper[0].metadata[0].name
            restart_policy       = "Never"

            container {
              name  = "keeper"
              image = var.keeper_image

              env {
                name  = "DEPLOYMENTS"
                value = join(" ", local.keeper_deployments)
              }
              env {
                name  = "REPLICAS"
                value = tostring(var.replicas)
              }

              # `oc` inside the pod signs in as the ServiceAccount and uses its namespace.
              command = ["/bin/sh", "-ec", <<-EOT
                for d in $DEPLOYMENTS; do
                  have=$(oc get deployment "$d" -o jsonpath='{.spec.replicas}')
                  if [ "$have" -lt "$REPLICAS" ]; then
                    echo "$d is at $have replicas (idled); scaling to $REPLICAS"
                    oc scale deployment "$d" --replicas="$REPLICAS"
                  else
                    echo "$d is at $have replicas; nothing to do"
                  fi
                done
              EOT
              ]

              resources {
                requests = { cpu = "10m", memory = "32Mi" }
                limits   = { cpu = "100m", memory = "64Mi" }
              }
            }
          }
        }
      }
    }
  }
}
