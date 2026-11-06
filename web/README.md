# PA Crypto Agent Web

This directory contains the Web implementation for the PA_Agent fork.

## Current status

- P0 architecture audit completed.
- P1 Binance public REST foundation implemented for Spot and USDⓈ-M Futures.
- Basic Vue 3 market screen implemented.
- P2 internal analysis adapter now constructs the original KlineFrame, uses the original PromptAssembler/JsonValidator/TwoStageOrchestrator, and injects timestamped crypto derivatives context into both stages.
- The analysis adapter is wired into the FastAPI application state but is not exposed as an unauthenticated HTTP endpoint yet. Public guest quotas, job isolation, persistent records, and monitoring workers must be implemented before enabling public AI analysis.
- The first Web slice is not yet a production public service.

## Local development

Backend (run from repository root):

~~~bash
python -m venv .venv
source .venv/bin/activate
pip install -e . --no-deps
pip install -r web/backend/requirements.txt
PYTHONPATH=web/backend uvicorn app.main:app --reload --app-dir web/backend
~~~

Frontend:

~~~bash
cd web/frontend
npm install
npm run dev
~~~

The Vite development server proxies /api to http://127.0.0.1:8000.

## API

- GET /api/v1/health
- GET /api/v1/markets?market=spot|usdm
- GET /api/v1/market/{market}/{symbol}/klines?interval=15m&limit=200
- GET /api/v1/market/{market}/{symbol}/ticker
- GET /api/v1/market/usdm/{symbol}/context?period=15m

Only public GET market-data endpoints are exposed. There are no Binance account, order, cancel, transfer, or withdrawal routes.

## AI configuration

AI_API_KEY, AI_BASE_URL, AI_MODEL, AI_THINKING, and AI_REASONING_EFFORT are read only by the backend from environment variables. The browser never receives the AI key. The current analysis service is an internal adapter only; do not enable public analysis until quota/session controls and a persistent job queue are implemented.

## Docker

From repository root:

~~~bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
~~~

Open http://localhost:8080.
