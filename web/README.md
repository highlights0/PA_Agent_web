# PA Crypto Agent Web

Multi-exchange Web market-data and original PA analysis foundation.

## Current exchange coverage

| Exchange | Spot candles/ticker | USDT perpetual futures |
|---|---|---|
| Binance Global | Yes, via Binance Vision public data host | Adapter retained, but the user's network returned HTTP 451 |
| Binance.US | Yes | Not offered by this adapter |
| Coinbase Exchange | Yes | Not offered by this adapter |
| Gate.io | Not currently enabled for Spot | Yes; public endpoints locally verified by the user for BTC, ETH and NEAR |

OKX and Bybit were not enabled: the user's local requests failed during TLS handshake (SSL EOF), so their API availability is not established for that network.

The API does not silently substitute one exchange for another. Every response is associated with an explicitly selected exchange. Binance Global Spot defaults to the official public market-data-only host https://data-api.binance.vision. Binance Global USDⓈ-M Futures still uses https://fapi.binance.com and may be region-restricted.

## Gate.io USDT perpetual futures

The Gate adapter uses public GET endpoints under `https://api.gateio.ws/api/v4`; no API key or account permission is used:

- `GET /futures/usdt/contracts` — active contract discovery
- `GET /futures/usdt/contracts/{contract}` — contract metadata and `quanto_multiplier`
- `GET /futures/usdt/tickers?contract=BTC_USDT` — last/24h ticker, mark/index price and funding rate
- `GET /futures/usdt/candlesticks?contract=BTC_USDT&interval=15m&limit=200` — futures OHLCV
- `GET /futures/usdt/funding_rate?contract=BTC_USDT&limit=5` — historical funding
- `GET /futures/usdt/contract_stats?contract=BTC_USDT&interval=5m&limit=37` — OI, account/taker/top-trader ratios and aggregate long/short liquidations

The user's local test returned HTTP 200 for ticker, 15m candles, funding history and contract stats for BTC_USDT, ETH_USDT and NEAR_USDT. Binance USD-M returned HTTP 451. OKX and Bybit returned TLS EOF errors from that local network.

### Data normalization and units

- Gate contract symbols use `BTC_USDT`; the Web API also accepts compact `BTCUSDT`.
- Gate candle `v` is contract volume (contracts), not base-asset volume. The adapter fetches the contract's `quanto_multiplier` and converts `v × quanto_multiplier` into base-asset volume for the original PA KlineBar. The raw contract count is retained separately as `contract_volume`.
- Gate candle `sum` is quote-currency turnover and is mapped to `quote_volume`.
- Gate does not supply Binance-style trade counts or taker-buy volume in this candle response. These fields remain unavailable; they are not fabricated.
- Gate `open_interest` is contract count; `open_interest_usd` is quote-currency value.
- `lsr_account`, `lsr_taker`, `top_lsr_account`, and `top_lsr_size` are kept as separate source-defined ratios. Account ratio is not treated as position-size ratio.
- The context endpoint requests up to 37 five-minute contract-stat records (about three hours). Liquidation totals are summed from Gate's quote-currency fields; `long_liq_usd_new`/`short_liq_usd_new` are preferred, with the documented legacy `long_liq_usd`/`short_liq_usd` fallback. The response includes the actual time window and data-quality markers.
- The separate detailed liquidation-order endpoint is not yet wired into the Web app. The initial integration uses the tested aggregate liquidation fields from `contract_stats`.

Official Gate API documentation: [Futures REST API](https://www.gate.com/docs/developers/apiv4/en/futures/).

## Market API

- GET /api/v1/exchanges
- GET /api/v1/markets?market=spot&exchange=coinbase
- GET /api/v1/market/spot/BTC-USD/klines?exchange=coinbase&interval=15m&limit=200
- GET /api/v1/market/spot/BTCUSDT/klines?exchange=binance_us&interval=15m&limit=200
- GET /api/v1/market/usdm/BTCUSDT/klines?exchange=gate&interval=15m&limit=200
- GET /api/v1/market/usdm/BTCUSDT/ticker?exchange=gate
- GET /api/v1/market/usdm/BTCUSDT/context?exchange=gate&period=5m

The Gate context endpoint returns funding rate, funding interval/next funding time, mark/index price, OI in contracts and quote currency, OI window change, account/taker/top-trader ratios, and aggregate long/short liquidation amounts. Missing fields are null and described in `data_quality`; they are never filled with zero or another exchange's values.

## Browser AI model settings

Open the Web app at http://localhost:5173 and click **AI 模型设置** in the top-right corner. The browser settings reproduce the original desktop PA Agent provider fields:

- Profile name, Model, API Base URL, API Key
- Optional GLM backup API Key
- Thinking on/off
- Reasoning Effort: low / medium / high / max
- Context Window
- Test connection, save, switch active model, and delete saved profiles

Multiple provider profiles can be saved; the active profile is used by both stages of the original PA analysis. Existing `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` environment variables remain as a backward-compatible fallback if no Web profile is active.

### Key protection and access

- API keys are encrypted at rest with Fernet. Plaintext keys are only decrypted in the backend process for an AI request; list/save responses only return a masked key and configured status.
- By default, the encryption key is generated once at `web/backend/data/ai_settings.key`; encrypted profiles are stored in `web/backend/data/ai_providers.json`. This directory is gitignored.
- Back up both files together. If the encryption key is lost, saved API keys cannot be recovered. Never commit or share them.
- For local development (`APP_ENV=development`), settings management is allowed only for loopback requests. The Vite dev server binds to 127.0.0.1.
- For a server deployment, set `APP_ENV=production` and configure a long random `AI_SETTINGS_ADMIN_TOKEN`. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Enter that token in the browser settings panel; it is kept in sessionStorage for the current tab only.
- `AI_SETTINGS_MASTER_KEY` is optional. If omitted, the backend generates `ai_settings.key` automatically. If supplied, it must be a Fernet URL-safe base64 key; generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it stable across restarts.
- For Docker, the `pa-agent-data` named volume persists encrypted profiles and the generated encryption key. Back up that volume. Alternatively store `AI_SETTINGS_MASTER_KEY` in a secure secret manager. Losing both the saved key file and master key makes encrypted provider keys unrecoverable.
- Public AI analysis remains disabled by default in production; set `PUBLIC_ANALYSIS_ENABLED=true` only after protecting the service and setting suitable quotas. In local development, saving an active AI profile enables analysis automatically.
- The current quota is an in-memory per-IP UTC-day counter and is suitable only for single-process staging, not a multi-replica public launch.

No exchange account access, order, cancel, transfer, or withdrawal endpoints are implemented.

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

## Environment

See repository-root .env.example. Gate public futures market-data requests do not require credentials. AI credentials are read only by the backend and never sent to the browser.

## Docker

~~~bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up --build -d
~~~

Open http://localhost:8080.
