# Runs the policy-analytics-platform image in the openshift-node-app Deployment (same Service, Route and keeper).
# Build and deploy: see docs/DEPLOYMENT.md. Run Terraform from this folder. Apply with:
#   TF_VAR_extra_secret_env='{"ANTHROPIC_API_KEY":"..."}' terraform apply -var-file=policy-analytics.tfvars -var image_digest=sha256:...
# Back to stock-spike: terraform apply -var image_digest=<stock-spike digest>   (no var file)
# No secrets in this file.

extra_env = {
  LIVE_DATA          = "true"
  LLM_PROVIDER_ORDER = "anthropic,offline"
  ANTHROPIC_MODEL    = "claude-opus-5-5"
  ANTHROPIC_EFFORT   = "low"
  # Public URL with no login: keep spend bounded. Behind the router every visitor shares one client IP, so this
  # is effectively 3 analyses a minute for the whole site.
  RATE_LIMIT_PER_MIN      = "3"
  LLM_BUDGET_USD          = "0.30"
  MAX_CONCURRENT_ANALYSES = "1"
  ALLOW_FAULT_INJECTION   = "true" # the "simulate a failure" demo
  LOG_LEVEL               = "INFO"
  # The frontend is on Vercel at sgpolicyanalytics.com and calls this API cross-origin.
  CORS_ORIGINS = "https://sgpolicyanalytics.com,https://www.sgpolicyanalytics.com"
}

# pandas + scipy need far more than the stock-spike app's 128Mi.
memory_limit = "768Mi"
cpu_limit    = "1"
