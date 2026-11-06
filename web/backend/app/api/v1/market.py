"""Public market-data API routes. These endpoints never place orders."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.adapters.binance.client import BinanceAPIError
from app.adapters.binance.models import MarketType

router = APIRouter()
_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h",
    "12h", "1d", "3d", "1w", "1M",
}
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,24}$")


def _service(request: Request):
    return request.app.state.market_service


def _validate_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="Invalid symbol")
    return normalized


def _translate_error(exc: BinanceAPIError) -> HTTPException:
    if exc.status_code in (418, 429):
        return HTTPException(
            status_code=503,
            detail="Binance is rate-limiting this service; retry later.",
            headers={"Retry-After": exc.retry_after or "5"},
        )
    if exc.status_code == 451:
        return HTTPException(
            status_code=502,
            detail=(
                "Binance returned HTTP 451 (Unavailable For Legal Reasons). "
                "The Binance global API may be restricted for this network region or IP. "
                "Test the same endpoint from another permitted network or use a supported exchange."
            ),
        )
    if exc.status_code == 403:
        return HTTPException(
            status_code=502,
            detail=(
                "Binance returned HTTP 403 (Forbidden). The request may be blocked by "
                "Binance, a regional access rule, or an upstream security filter."
            ),
        )
    if exc.status_code is not None and 400 <= exc.status_code < 500:
        code = f", Binance code {exc.error_code}" if exc.error_code is not None else ""
        message = str(exc)[:240]
        return HTTPException(
            status_code=502,
            detail=f"Binance returned HTTP {exc.status_code}{code}: {message}",
        )
    if exc.status_code is None:
        return HTTPException(status_code=502, detail=str(exc)[:240])
    return HTTPException(status_code=502, detail="Binance market-data service is unavailable")


@router.get("/markets")
async def list_markets(
    request: Request,
    market: MarketType = Query(default="spot"),
):
    try:
        return await _service(request).markets(market)
    except BinanceAPIError as exc:
        raise _translate_error(exc) from exc


@router.get("/market/{market}/{symbol}/klines")
async def get_klines(
    request: Request,
    market: MarketType,
    symbol: str,
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
            market, _validate_symbol(symbol), interval, limit
        )
    except BinanceAPIError as exc:
        raise _translate_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid Binance kline payload") from exc


@router.get("/market/{market}/{symbol}/ticker")
async def get_ticker(request: Request, market: MarketType, symbol: str):
    try:
        return await _service(request).ticker(market, _validate_symbol(symbol))
    except BinanceAPIError as exc:
        raise _translate_error(exc) from exc


@router.get("/market/usdm/{symbol}/context")
async def get_futures_context(
    request: Request,
    symbol: str,
    period: Literal["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"] = "15m",
):
    try:
        return await _service(request).futures_context(_validate_symbol(symbol), period)
    except BinanceAPIError as exc:
        raise _translate_error(exc) from exc
