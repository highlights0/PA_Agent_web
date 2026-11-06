# PA Crypto Agent Web

This directory contains the Web implementation for the PA_Agent fork.

## Current status

- P0 architecture audit completed.
- P1 Binance public REST foundation implemented for Spot and USDⓈ-M Futures.
- Basic Vue 3 market screen implemented.
- P2 internal analysis adapter uses the original KlineFrame, PromptAssembler, JsonValidator, and TwoStageOrchestrator. Binance derivatives context is appended to both analysis stages without replacing the original PA rules.
- P3 guarded synchronous analysis endpoint and Vue analysis panel implemented. It is disabled by default and requires a server-side AI key plus an explicit operator opt-in.
- Persistent monitoring workers, durable public quotas, database-backed analysis history, Redis job queue, and opportunity lifecycle are not yet implemented. This is not yet a production-ready public trading-analysis service.

## Local development

Backend (run from repository root):

~~~cmd
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e . --no-deps
python -m pip install -r web/backend/requirements.txt
set PYTHONPATH=web\backend
python -m uvicorn app.main:app --app-dir web\backend --reload
~~~

If pip reports a dependency-resolution error for OpenAI/jiter, first check whether a private or stale package mirror is configured:

~~~cmd
python -m pip config list
python -m pip index versions jiter
~~~

Then retry against the official PyPI index:

~~~cmd
python -m pip install -i https://pypi.org/simple -r web/backend/requirements.txt
~~~

The Web requirements intentionally constrain the OpenAI SDK to the 1.x API line used by the existing PA client and declare its jiter dependency explicitly. This avoids accidentally selecting a future major SDK release. If the resolver still fails, capture the complete output including the final conflicting-package lines; do not randomly downgrade unrelated packages.

Frontend:

~~~cmd
cd web\frontend
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
- GET /api/v1/analysis/quota
- POST /api/v1/analysis

The analysis endpoint runs the original two-stage PA pipeline in a worker thread and returns a sanitized structured result. It does not expose prompts, raw provider payloads, reasoning_content, or API keys.

## Enable AI analysis

1. Set AI_API_KEY, AI_BASE_URL, and AI_MODEL in the server-side .env file.
2. Set PUBLIC_ANALYSIS_ENABLED=true.
3. Keep PUBLIC_MAX_CONCURRENT_ANALYSES low (default 1).

The current public quota is an in-memory per-IP UTC-day counter. It is suitable only for a single-process staging deployment; it resets on restart and is not shared between replicas. Keep the default PUBLIC_ANALYSIS_ENABLED=false on a public production host until the Redis-backed quota, guest-session isolation, persistent job queue, and durable audit records are implemented.

The Web service never requests Binance trading permissions and contains no order, cancel, transfer, or withdrawal endpoints.

## Environment

See repository-root .env.example. AI credentials are read only by the backend process and are never sent to the browser.

## Docker

From repository root:

~~~bash
cp .env.example .env
# Configure server-side AI_API_KEY and explicitly enable analysis only for a protected staging test.
docker compose -f deploy/docker-compose.yml up --build -d
~~~

Open http://localhost:8080.
