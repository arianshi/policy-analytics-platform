.PHONY: setup backend frontend test test-backend test-frontend lint load-test up down refresh-data

setup:               ## install backend + frontend dependencies
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm ci

backend:             ## API on :8000 (add LIVE_DATA=false to stay offline)
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend:            ## UI on :3000
	cd frontend && npm run dev

test: test-backend test-frontend

test-backend:        ## unit + integration + LLM + data-quality + perf tests with coverage
	cd backend && .venv/bin/pytest -o addopts="" --cov=app --cov-report=term:skip-covered

test-frontend:
	cd frontend && npm test

lint:
	cd backend && .venv/bin/ruff check .
	cd frontend && npm run lint && npm run typecheck

load-test:           ## needs a running API; use LIVE_DATA=false so the load does not hit government servers
	cd backend && .venv/bin/python scripts/load_test.py --base-url http://localhost:8000 --total 40 --concurrency 8

refresh-data:        ## re-download the real government datasets into data/snapshots
	cd backend && .venv/bin/python scripts/refresh_snapshots.py

up:
	docker compose up --build -d

down:
	docker compose down
