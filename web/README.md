# PA Crypto Agent Web

This directory contains the first Web implementation slice for the PA_Agent fork.

## Current status

- P0 architecture audit completed.
- P1 Binance public REST foundation implemented for Spot and USDⓈ-M Futures.
- Basic Vue 3 market screen implemented.
- Original Price Action core is retained in the repository, but the Web analysis service has not been connected yet.
- Persistent monitoring workers, public guest quotas, PostgreSQL/Redis, and opportunity lifecycle are follow-on work. Do not expose this first slice as a production public service with AI analysis enabled.

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

Only public GET market-data endpoints are implemented. There are no Binance account, order, cancel, transfer, or withdrawal routes.

## Environment

See repository-root .env.example. The first slice does not require a Binance API key or AI API key. Binance public market data is used.

## Docker

From repository root:

~~~bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
~~~

Open http://localhost:8080.
