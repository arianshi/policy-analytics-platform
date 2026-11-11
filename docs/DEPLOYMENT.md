# Deploying: frontend on Vercel, backend on OpenShift

| Part | Where | URL |
|---|---|---|
| Frontend (Next.js) | Vercel | https://sgpolicyanalytics.com |
| Backend (FastAPI + agents) | OpenShift Developer Sandbox, `arian-shi-dev` | the `openshift-node-app` Route |

The backend cannot run on Vercel: analyses run in the background for minutes after the API returns, the live agent
feed is a WebSocket server, and history lives in SQLite on disk - none of which serverless functions provide.

## Backend on OpenShift

The platform runs in the `arian-shi-dev` project in place of the stock-spike app (`openshift-node-app`), reusing that
app's pipeline: the in-cluster image build (`oc start-build openshift-node-app`) and a copy of its Terraform in
[`terraform/`](../terraform), which owns the ConfigMap, Secret, Deployment and Service. The state file is copied
from `openshift-node-app/terraform` (git-ignored here: it holds the database password and API keys); run Terraform
from this folder only, so the two copies never fight over the same objects. The Route, the keeper
CronJob (which undoes the Sandbox idler) and the URL stay as they are.

### What runs

One container built from the root [`Dockerfile`](../Dockerfile) on Red Hat UBI images:

| | |
|---|---|
| Process | `uvicorn` on port 8080 (`PORT`), serving the API, the WebSocket (`/ws/...`) and the static Next.js export (`/`) |
| Probes | `GET /healthz` |
| Frontend | Built with `NEXT_OUTPUT=export` and an empty `NEXT_PUBLIC_API_URL`, so the page calls its own origin |
| Storage | SQLite and the HTTP cache in `/tmp/policy-analytics`: **lost when the pod restarts** (history and cache only; every analysis can be re-run) |
| Settings | [`terraform/policy-analytics.tfvars`](../terraform/policy-analytics.tfvars) (no secrets) |
| Secret | `ANTHROPIC_API_KEY`, passed at apply time as `TF_VAR_extra_secret_env`, stored in the `openshift-node-app-secrets` Secret |

**Cost guard.** The URL is public and has no login. The settings cap it at 3 analyses a minute for the whole site
(behind the router all visitors share one client IP), one analysis at a time, and $0.30 of LLM spend per analysis.
Set a monthly spend limit on the Anthropic key as well.

### Deploy

```bash
oc login --web --server=https://api.rm1.0a51.p1.openshiftapps.com:6443
oc project arian-shi-dev

# 1. Note the image running now, for rollback.
oc get deploy openshift-node-app -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'

# 2. Build the image in the cluster from a clean copy of this repo.
cd ~/AI-OS/Projects/policy-analytics-platform
S=$(mktemp -d) && rsync -a --exclude .git --exclude .env --exclude node_modules --exclude .next --exclude out \
  --exclude .venv --exclude var --exclude coverage --exclude __pycache__ --exclude "*.pdf" ./ "$S"/
ls -a "$S" | grep -x .env && echo "STOP: .env was staged"   # the keys must never reach the build
oc start-build openshift-node-app --from-dir="$S" --follow
DIGEST=$(oc get is openshift-node-app -o jsonpath='{.status.tags[0].items[0].image}')

# 3. Roll it out with the stock-spike Terraform.
cd ~/AI-OS/Projects/policy-analytics-platform/terraform
export TF_VAR_extra_secret_env="{\"ANTHROPIC_API_KEY\":\"$(grep '^ANTHROPIC_API_KEY=' ~/AI-OS/Projects/policy-analytics-platform/.env | cut -d= -f2-)\"}"
terraform plan  -var-file=policy-analytics.tfvars -var "image_digest=$DIGEST"
terraform apply -var-file=policy-analytics.tfvars -var "image_digest=$DIGEST"

# 4. Check it.
HOST=$(oc get route openshift-node-app -o jsonpath='{.spec.host}')
curl -s "https://$HOST/healthz"; curl -s "https://$HOST/api/health"
```

Then open `https://$HOST/` and run a sample query.

### Roll back to stock-spike

```bash
cd ~/AI-OS/Projects/policy-analytics-platform/terraform
terraform apply -var "image_digest=<the digest noted in step 1>"   # no var file: the stock-spike defaults return
```

The PostgreSQL database, its volume and the keeper are untouched by this deploy, so stock-spike comes back with its data.

## Frontend on Vercel

The page is a static Next.js build that calls the OpenShift API directly (CORS allows `sgpolicyanalytics.com` and
`www.`; see `CORS_ORIGINS` in `policy-analytics.tfvars`). The API address is baked in at build time.

```bash
cd ~/AI-OS/Projects/policy-analytics-platform/frontend
npx vercel@latest login                                   # once
npx vercel@latest link --yes --project sgpolicyanalytics   # creates/links the Vercel project
HOST=$(oc get route openshift-node-app -o jsonpath='{.spec.host}')
printf 'https://%s' "$HOST" | npx vercel@latest env add NEXT_PUBLIC_API_URL production
npx vercel@latest deploy --prod
npx vercel@latest domains add sgpolicyanalytics.com sgpolicyanalytics   # the domain already uses Vercel DNS
```

Change the backend URL: update `NEXT_PUBLIC_API_URL` (`vercel env rm` / `env add`) and redeploy.
