#!/usr/bin/env bash
# Roll the policy-analytics image out to OpenShift (replaces stock-spike in the openshift-node-app Deployment).
# Usage: terraform/apply.sh [image-digest]   (default: the latest build in the openshift-node-app ImageStream)
# The Anthropic key is read from ../.env and never printed.
set -euo pipefail
cd "$(dirname "$0")"

digest="${1:-$(oc get is openshift-node-app -o jsonpath='{.status.tags[0].items[0].image}')}"
key="$(grep '^ANTHROPIC_API_KEY=' ../.env | cut -d= -f2-)"
[ -n "$key" ] || { echo "ANTHROPIC_API_KEY is missing from .env" >&2; exit 1; }

echo "Deploying image $digest"
TF_VAR_extra_secret_env="{\"ANTHROPIC_API_KEY\":\"$key\"}" \
  terraform apply -auto-approve -var-file=policy-analytics.tfvars -var "image_digest=$digest"
