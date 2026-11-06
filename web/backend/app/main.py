"""FastAPI application for PA Crypto Agent's public market-data API."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.binance.client import BinanceAPIError, BinanceRESTClient
from app.adapters.binance.futures_rest import BinanceUSDMREST
from app.adapters.binance.spot_rest import BinanceSpotREST
from app.api.v1.market import router as market_router
from app.core.config import settings
from app.services.market_service import MarketService


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
    http = httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=False)
    spot = BinanceSpotREST(BinanceRESTClient(http, settings.spot_rest_url))
    futures = BinanceUSDMREST(BinanceRESTClient(http, settings.futures_rest_url))
    app.state.http = http
    app.state.settings = settings
    app.state.market_service = MarketService(spot, futures)
    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(
    title="PA Crypto Agent API",
    version="0.1.0",
    description="Public Binance Spot and USDⓈ-M Futures market-data API. No trading endpoints.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
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
        content={"detail": "Binance market-data service is unavailable"},
    )


app.include_router(market_router, prefix="/api/v1")
