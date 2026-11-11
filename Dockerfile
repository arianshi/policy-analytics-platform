# Single-image build for OpenShift (built in the cluster by `oc start-build`; see docs/DEPLOYMENT.md).
# One process, one port: FastAPI serves the API, the WebSocket and the static Next.js export, so the browser talks to
# the same origin. Red Hat UBI base images, which run as an arbitrary non-root UID on OpenShift.
# Local development and docker compose keep using backend/Dockerfile and frontend/Dockerfile.

FROM registry.access.redhat.com/ubi9/nodejs-22 AS web
WORKDIR /opt/app-root/src
COPY --chown=1001:0 frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --chown=1001:0 frontend/ ./
# An empty API URL makes the page call the API on its own origin.
ENV NEXT_OUTPUT=export NEXT_PUBLIC_API_URL= NEXT_PUBLIC_HIDE_OFFLINE=true NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM registry.access.redhat.com/ubi9/python-312
WORKDIR /opt/app-root/src
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/data ./data
COPY --from=web /opt/app-root/src/out ./web
# The SQLite database and HTTP cache go to RUNTIME_DIR. /tmp is writable by any UID; it is lost when the pod restarts.
ENV DATA_DIR=/opt/app-root/src/data WEB_DIR=/opt/app-root/src/web RUNTIME_DIR=/tmp/policy-analytics \
    PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --proxy-headers
