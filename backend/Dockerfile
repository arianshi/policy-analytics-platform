FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY data ./data

# Run as an unprivileged user; /data is the only writable path (SQLite db + HTTP cache) and is a volume in Compose.
RUN useradd --system --uid 10001 --home /nonexistent app && mkdir -p /data && chown app:app /data
ENV DATA_DIR=/app/data RUNTIME_DIR=/data
USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=4s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
