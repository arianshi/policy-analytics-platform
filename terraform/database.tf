# PostgreSQL for the stored headlines: its own Deployment, with a volume so the data survives
# restarts and the Sandbox idler. The app finds it at the Service name below.

locals {
  db_name     = "${local.name}-db"
  db_user     = "headlines"
  db_database = "headlines"
  db_labels   = { app = local.db_name, "app.kubernetes.io/part-of" = local.name }
}

# Generated once and kept in the (git-ignored) Terraform state. Letters and digits only,
# so the password can sit inside DATABASE_URL without URL-encoding.
resource "random_password" "db" {
  length  = 32
  special = false
}

# Key for POST /api/news/refresh. Read it with: terraform output -raw refresh_api_key
resource "random_password" "refresh_api_key" {
  length  = 40
  special = false
}

# What the PostgreSQL container needs. The app never reads this one.
resource "kubernetes_secret_v1" "db" {
  metadata {
    name      = local.db_name
    namespace = var.namespace
    labels    = local.db_labels
  }

  data = {
    POSTGRESQL_USER     = local.db_user
    POSTGRESQL_PASSWORD = random_password.db.result
    POSTGRESQL_DATABASE = local.db_database
  }
}

# What the app needs to reach the database and to check the refresh key.
resource "kubernetes_secret_v1" "app" {
  metadata {
    name      = "${local.name}-secrets"
    namespace = var.namespace
    labels    = local.labels
  }

  data = merge(
    {
      DATABASE_URL    = "postgresql://${local.db_user}:${random_password.db.result}@${local.db_name}:5432/${local.db_database}"
      REFRESH_API_KEY = random_password.refresh_api_key.result
    },
    var.extra_secret_env,
  )
}

resource "kubernetes_persistent_volume_claim_v1" "db" {
  metadata {
    name      = local.db_name
    namespace = var.namespace
    labels    = local.db_labels
  }

  spec {
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = { storage = var.db_storage_size }
    }
  }

  # The Sandbox's default storage class binds the volume when the pod first uses it, not before.
  wait_until_bound = false
}

resource "kubernetes_deployment_v1" "db" {
  metadata {
    name      = local.db_name
    namespace = var.namespace
    labels    = local.db_labels
  }

  spec {
    replicas = var.replicas
    # Few old ReplicaSets: the Sandbox allows only 30 in the whole namespace.
    revision_history_limit = 2

    # One ReadWriteOnce volume: the old pod must stop before a new one can mount it.
    strategy {
      type = "Recreate"
    }

    selector {
      match_labels = { app = local.db_name }
    }

    template {
      metadata {
        labels = { app = local.db_name }
      }

      spec {
        container {
          name  = "postgresql"
          image = var.db_image

          port {
            container_port = 5432
          }

          env_from {
            secret_ref {
              name = kubernetes_secret_v1.db.metadata[0].name
            }
          }

          volume_mount {
            name       = "data"
            mount_path = "/var/lib/pgsql/data"
          }

          readiness_probe {
            exec {
              command = ["/bin/sh", "-c", "pg_isready -h 127.0.0.1 -p 5432"]
            }
            initial_delay_seconds = 5
            period_seconds        = 10
          }

          liveness_probe {
            exec {
              command = ["/bin/sh", "-c", "pg_isready -h 127.0.0.1 -p 5432"]
            }
            initial_delay_seconds = 30
            period_seconds        = 20
          }

          resources {
            requests = { cpu = "50m", memory = "128Mi" }
            limits   = { cpu = "250m", memory = "256Mi" }
          }
        }

        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim_v1.db.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service_v1" "db" {
  metadata {
    name      = local.db_name
    namespace = var.namespace
    labels    = local.db_labels
  }

  spec {
    selector = { app = local.db_name }

    port {
      port        = 5432
      target_port = 5432
    }
  }
}
