"""Public multi-exchange market-data API. No trading or account endpoints."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.adapters.binance.client import BinanceAPIError
from app.adapters.binance.models import MarketType
from app.services.provider_registry import UnsupportedMarketProvider

router = APIRouter()
_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
    "12h", "1d", "3d", "1w", "1M",
}
_SYMBOL_RE = re.compile(r"^[A-Z0-9-]{3,24}$")
Exchange = Literal["binance", "binance_us", "coinbase"]


def _service(request: Request):
    return request.app.state.market_service


def _validate_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="Invalid symbol")
    return normalized


def _translate_error(exc: BinanceAPIError, exchange: str) -> HTTPException:
    if exc.status_code in (418, 429):
        return HTTPException(
            status_code=503,
            detail=f"{exchange} is rate-limiting this service; retry later.",
            headers={"Retry-After": exc.retry_after or "5"},
        )
    if exc.status_code == 451:
        return HTTPException(
            status_code=502,
            detail=(
                f"{exchange} returned HTTP 451 (Unavailable For Legal Reasons). "
                "This exchange endpoint is restricted for the current network region or IP. "
                "Select another supported market-data provider."
            ),
        )
    if exc.status_code == 403:
        return HTTPException(
            status_code=502,
            detail=f"{exchange} returned HTTP 403 (Forbidden); access was denied upstream.",
        )
    if exc.status_code is not None and 400 <= exc.status_code < 500:
        code = f", upstream code {exc.error_code}" if exc.error_code is not None else ""
        return HTTPException(
            status_code=502,
            detail=f"{exchange} returned HTTP {exc.status_code}{code}: {str(exc)[:200]}",
        )
    if exc.status_code is None:
        return HTTPException(status_code=502, detail=str(exc)[:200])
    return HTTPException(status_code=502, detail=f"{exchange} market-data service is unavailable")


def _provider_error(exchange: str, exc: Exception) -> HTTPException:
    if isinstance(exc, BinanceAPIError):
        return _translate_error(exc, exchange)
    if isinstance(exc, UnsupportedMarketProvider):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=502, detail=f"{exchange} market data request failed")


@router.get("/exchanges")
async def supported_exchanges(request: Request):
    return _service(request).supported_exchanges


@router.get("/markets")
async def list_markets(
    request: Request,
    market: MarketType = Query(default="spot"),
    exchange: Exchange = Query(default="binance"),
):
    try:
        return await _service(request).markets(market, exchange)
    except Exception as exc:
        raise _provider_error(exchange, exc) from exc


@router.get("/market/{market}/{symbol}/klines")
async def get_klines(
    request: Request,
    market: MarketType,
    symbol: str,
    exchange: Exchange = Query(default="binance"),
    interval: str = Query(default="15m"),
    limit: int = Query(default=200, ge=20, le=1000),
):
    if interval not in _INTERVALS:
        raise HTTPException(status_code=422, detail="Unsupported interval")
    if limit > request.app.state.settings.max_klines:
        raise HTTPException(
            status_code=422,
            detail=f"limit exceeds server maximum ({request.app.state.settings.max_klines})",
        )
    try:
        return await _service(request).klines(
            market, _validate_symbol(symbol), interval, limit, exchange
        )
    except Exception as exc:
        raise _provider_error(exchange, exc) from exc


@router.get("/market/{market}/{symbol}/ticker")
async def get_ticker(
    request: Request,
    market: MarketType,
    symbol: str,
    exchange: Exchange = Query(default="binance"),
):
    try:
        return await _service(request).ticker(
            market, _validate_symbol(symbol), exchange
        )
    except Exception as exc:
        raise _provider_error(exchange, exc) from exc


@router.get("/market/usdm/{symbol}/context")
async def get_futures_context(
    request: Request,
    symbol: str,
    period: Literal["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"] = "15m",
):
    try:
        return await _service(request).futures_context(_validate_symbol(symbol), period)
    except Exception as exc:
        raise _provider_error("binance", exc) from exc
