variable "namespace" {
  description = "OpenShift project (namespace) the app lives in."
  type        = string
  default     = "arian-shi-dev"
}

variable "kubeconfig_path" {
  description = "Kubeconfig written by `oc login`."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Kubeconfig context for the Developer Sandbox (namespace/cluster/user, as `oc config get-contexts` prints it)."
  type        = string
  default     = "arian-shi-dev/api-rm1-0a51-p1-openshiftapps-com:6443/arian-shi"
}

variable "replicas" {
  description = "Pod count. The Sandbox idler scales the Deployment to 0 after about 12 hours; the keeper (keeper.tf) and the next apply scale it back."
  type        = number
  default     = 1
}

variable "image_tag" {
  description = "ImageStream tag to run when image_digest is empty."
  type        = string
  default     = "latest"
}

variable "image_digest" {
  description = <<-EOT
    Image digest (sha256:...) to pin the Deployment to. Leave empty to follow :latest, but then a
    new build does not trigger a rollout and cannot be rolled back. Set it after every
    `oc start-build`; see docs/DEPLOYMENT.md.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.image_digest == "" || can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "image_digest must be empty or look like sha256:<64 hex characters>."
  }
}

# --- Datadog monitoring (off by default; see docs/DATADOG.md) ---

variable "datadog_enabled" {
  description = "Run a Datadog Agent next to the app and turn on tracing, feed metrics and browser monitoring. Needs datadog_api_key."
  type        = bool
  default     = false
}

variable "datadog_api_key" {
  description = "Datadog API key for the Agent. A secret: pass it as TF_VAR_datadog_api_key, never in a file. It ends up in the local, git-ignored Terraform state."
  type        = string
  default     = ""
  sensitive   = true
}

variable "datadog_site" {
  description = "Datadog site the account lives on (the address you log in at, without the app. prefix)."
  type        = string
  default     = "datadoghq.com"
}

variable "datadog_env" {
  description = "The env tag on every trace and metric."
  type        = string
  default     = "sandbox"
}

variable "datadog_hostname" {
  description = "Host name the Agent reports under. Fixed on purpose: a pod name would show up as a new billable host after every restart."
  type        = string
  default     = "openshift-node-app"
}

variable "datadog_agent_image" {
  description = "Datadog Agent image, pinned to a version."
  type        = string
  default     = "gcr.io/datadoghq/agent:7.84.6"
}

variable "datadog_rum_application_id" {
  description = "Application ID of the Datadog RUM application for the Angular page. Empty leaves browser monitoring off."
  type        = string
  default     = ""
}

variable "datadog_rum_client_token" {
  description = "Client token of that RUM application. Public by design: every visitor's browser receives it."
  type        = string
  default     = ""
}

variable "db_image" {
  description = "PostgreSQL image. The Sandbox mirrors Red Hat's images in the openshift project."
  type        = string
  default     = "image-registry.openshift-image-registry.svc:5000/openshift/postgresql:15-el9"
}

variable "db_storage_size" {
  description = "Size of the database volume."
  type        = string
  default     = "1Gi"
}

# --- The keeper (see keeper.tf) ---

variable "keeper_enabled" {
  description = "Run the CronJob that scales the app and database back up after the Sandbox idler stops them. Turn off on a cluster without an idler."
  type        = bool
  default     = true
}

variable "keeper_schedule" {
  description = "How often the keeper checks, as a cron expression. The interval is the longest the site stays down after each idling."
  type        = string
  default     = "*/5 * * * *"
}

variable "keeper_image" {
  description = "Image with the `oc` client for the keeper. The Sandbox mirrors Red Hat's cli image in the openshift project."
  type        = string
  default     = "image-registry.openshift-image-registry.svc:5000/openshift/cli:latest"
}

# --- Running a different image in this Deployment (e.g. policy-analytics; see policy-analytics.tfvars) ---
# All empty by default, so a plain apply leaves the stock-spike app exactly as it was.

variable "extra_env" {
  description = "Extra non-secret settings merged into the app ConfigMap."
  type        = map(string)
  default     = {}
}

variable "extra_secret_env" {
  description = "Extra secret settings merged into the app Secret (for example an LLM API key). Pass as TF_VAR_extra_secret_env, never in a file; they end up in the local, git-ignored Terraform state."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "memory_limit" {
  description = "Memory limit for the app container. Empty keeps the stock-spike default (128Mi, or 256Mi with Datadog)."
  type        = string
  default     = ""
}

variable "cpu_limit" {
  description = "CPU limit for the app container. Empty keeps 250m."
  type        = string
  default     = ""
}
