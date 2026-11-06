# PA Crypto Agent Web

Multi-exchange Web market-data and original PA analysis foundation.

## Current exchange coverage

| Exchange | Spot candles/ticker | USDⓈ-M Futures |
|---|---|---|
| Binance Global | Yes | Yes |
| Binance.US | Yes | Not offered by this adapter |
| Coinbase Exchange | Yes | Not offered by this adapter |
| Coinbase Derivatives | Not applicable | FIX/UDP market-data integration is a separate future task |

The API does not silently replace one exchange with another. Every response is associated with an explicitly selected exchange. Binance Global Spot defaults to the official public market-data-only host https://data-api.binance.vision. This host supports public Spot market-data endpoints and is separate from the global trading API host. Binance Global Futures still uses https://fapi.binance.com and may remain region-restricted. When another Spot provider is needed, use Coinbase Exchange or Binance.US if those services are available from the current network.

Coinbase Exchange uses BASE-QUOTE symbols such as BTC-USD and ETH-USD. Binance uses symbols such as BTCUSDT. These are different markets and quote currencies; do not concatenate their candles into one time series or treat USD and USDT as identical.

Coinbase public candles provide OHLCV but do not provide Binance-style taker-buy volumes, trade counts, or quote-volume fields in this adapter. Such fields remain empty/unavailable rather than being fabricated.

## Futures limitation

The Binance USDⓈ-M Futures adapter still calls Binance Global's fapi endpoint. This may be region-restricted. Coinbase Derivatives offers US perpetual-style futures, but its market-data access is documented through FIX and UDP rather than the simple public REST candle API used here. It must not be represented as already supported. A separate FIX/UDP market-data adapter and entitlement/access setup are required before it can be used.

No Binance account access, order, cancel, transfer, or withdrawal endpoints are implemented.

## Local development (Windows CMD)

From repository root:

~~~cmd
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e . --no-deps
python -m pip install -r web/backend/requirements.txt
set PYTHONPATH=web\backend
python -m uvicorn app.main:app --app-dir web\backend --reload
~~~

In a second CMD:

~~~cmd
cd web\frontend
npm install
npm run dev
~~~

Open http://localhost:5173.

## Market API

- GET /api/v1/exchanges
- GET /api/v1/markets?market=spot&exchange=coinbase
- GET /api/v1/market/spot/BTC-USD/klines?exchange=coinbase&interval=15m&limit=200
- GET /api/v1/market/spot/BTC-USD/ticker?exchange=coinbase
- GET /api/v1/market/spot/BTCUSDT/klines?exchange=binance_us&interval=15m&limit=200
- GET /api/v1/market/usdm/BTCUSDT/klines?exchange=binance&interval=15m&limit=200

Supported exchange capabilities are returned by /api/v1/exchanges.

## AI configuration

AI_API_KEY, AI_BASE_URL, and AI_MODEL are server-side environment variables. Public analysis remains disabled by default. The current quota is an in-memory per-IP UTC-day counter and is suitable only for single-process staging, not a multi-replica public launch.

## Docker

~~~bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
~~~

Open http://localhost:8080.
