"""FastAPI application for PA Crypto Agent multi-exchange market data."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.binance.client import BinanceAPIError, BinanceRESTClient
from app.adapters.binance.futures_rest import BinanceUSDMREST
from app.adapters.binance.spot_rest import BinanceSpotREST
from app.adapters.binance_us.spot_rest import BinanceUSSpotREST
from app.adapters.coinbase.spot_rest import CoinbaseSpotREST
from app.api.v1.analysis import router as analysis_router
from app.api.v1.market import router as market_router
from app.core.config import settings
from app.core.public_quota import PublicAnalysisQuota
from app.services.market_service import MarketService
from app.services.provider_registry import ProviderRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    limits = httpx.Limits(max_connections=80, max_keepalive_connections=30)
    http = httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=False)

    binance_spot = BinanceSpotREST(
        BinanceRESTClient(http, settings.spot_rest_url)
    )
    binance_futures = BinanceUSDMREST(
        BinanceRESTClient(http, settings.futures_rest_url)
    )
    binance_us_spot = BinanceUSSpotREST(
        BinanceRESTClient(http, settings.binance_us_spot_rest_url)
    )
    coinbase_spot = CoinbaseSpotREST(
        BinanceRESTClient(http, settings.coinbase_spot_rest_url)
    )

    providers = ProviderRegistry(
        binance_spot=binance_spot,
        binance_futures=binance_futures,
        binance_us_spot=binance_us_spot,
        coinbase_spot=coinbase_spot,
    )
    market_service = MarketService(providers, binance_futures)

    from app.services.analysis_service import AnalysisService

    app.state.http = http
    app.state.settings = settings
    app.state.market_service = market_service
    app.state.analysis_service = AnalysisService(market_service, settings)
    app.state.analysis_quota = PublicAnalysisQuota(settings.public_analysis_per_ip_daily)
    app.state.analysis_semaphore = asyncio.Semaphore(
        max(1, settings.public_max_concurrent_analyses)
    )
    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(
    title="PA Crypto Agent API",
    version="0.4.0",
    description=(
        "Multi-exchange public crypto market data: Binance, Binance.US and Coinbase Spot. "
        "Binance USD-M Futures is a separate capability. No trading endpoints."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "service": "pa-crypto-agent-api"}


@app.get("/api/v1/health/ready")
async def ready():
    return {"status": "ready"}


@app.exception_handler(BinanceAPIError)
async def binance_error_handler(request, exc: BinanceAPIError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=502,
        content={"detail": "Upstream exchange market-data service is unavailable"},
    )


app.include_router(market_router, prefix="/api/v1")
app.include_router(analysis_router, prefix="/api/v1")
