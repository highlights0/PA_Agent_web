"""Guarded public AI analysis endpoint. Disabled by default until configured by the operator."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.binance.client import BinanceAPIError
from app.adapters.binance.models import MarketType
from app.services.analysis_service import (
    AnalysisConfigurationError,
    InsufficientMarketDataError,
)
from app.services.provider_registry import UnsupportedMarketProvider

logger = logging.getLogger(__name__)
router = APIRouter()
_SYMBOL_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
_TIMEFRAMES = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h",
    "8h", "12h", "1d", "3d", "1w", "1M", "7d",
}


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: MarketType
    exchange: Literal["binance", "binance_us", "coinbase", "gate"] = "binance"
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = "15m"
    analysis_bars: int = Field(default=100, ge=20, le=250)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded:
        return forwarded[:64]
    return request.client.host if request.client else "unknown"


def _safe_error(record_exception: object) -> dict | None:
    if not isinstance(record_exception, dict):
        return None
    safe = {
        key: record_exception[key]
        for key in ("type", "stage", "category", "missing_fields", "invalid_fields")
        if key in record_exception
    }
    if record_exception.get("message"):
        safe["message"] = str(record_exception["message"])[:500]
    return safe


@router.get("/analysis/quota")
async def analysis_quota(request: Request):
    ip = _client_ip(request)
    return {
        "enabled": request.app.state.settings.public_analysis_enabled,
        "daily_limit": request.app.state.settings.public_analysis_per_ip_daily,
        "remaining": request.app.state.analysis_quota.remaining(ip),
        "scope": "per_ip_utc_day_in_process",
    }


@router.post("/analysis")
async def submit_analysis(payload: AnalysisRequest, request: Request):
    config = request.app.state.settings
    if not config.public_analysis_enabled:
        raise HTTPException(
            status_code=503,
            detail="Public AI analysis is disabled by the server operator",
        )
    if not config.ai_api_key.strip():
        raise HTTPException(status_code=503, detail="AI service is not configured")

    symbol = payload.symbol.strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol")
    if payload.timeframe not in _TIMEFRAMES:
        raise HTTPException(status_code=422, detail="Unsupported timeframe")
    if payload.analysis_bars + 50 > config.max_klines:
        raise HTTPException(
            status_code=422,
            detail=f"analysis_bars exceeds server limit; maximum is {config.max_klines - 50}",
        )

    semaphore: asyncio.Semaphore = request.app.state.analysis_semaphore
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=0.05)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=429,
            detail="AI analysis is busy; retry later",
            headers={"Retry-After": "30"},
        ) from exc

    try:
        allowed, remaining = request.app.state.analysis_quota.consume(_client_ip(request))
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Daily public AI analysis quota exhausted",
                headers={"Retry-After": str(_seconds_until_utc_midnight())},
            )

        run = await request.app.state.analysis_service.analyze(
            payload.market,
            symbol,
            payload.timeframe,
            payload.analysis_bars,
            payload.exchange,
        )
    except AnalysisConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InsufficientMarketDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsupportedMarketProvider as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BinanceAPIError as exc:
        if exc.status_code in (418, 429):
            raise HTTPException(
                status_code=503,
                detail="Selected exchange is rate-limiting this service; retry later",
                headers={"Retry-After": exc.retry_after or "5"},
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"Selected exchange market data is unavailable (HTTP {exc.status_code or 'network error'})",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Public AI analysis failed")
        raise HTTPException(status_code=502, detail="AI analysis failed; check server logs") from exc
    finally:
        semaphore.release()

    record = run.record
    return {
        "analysis_id": str(uuid4()),
        "status": "completed" if record.exception is None else "failed",
        "market": payload.market,
        "exchange": payload.exchange,
        "symbol": symbol,
        "timeframe": payload.timeframe,
        "bar_count": record.meta.bar_count,
        "events": run.events,
        "stage1_diagnosis": record.stage1_diagnosis,
        "stage2_decision": record.stage2_decision,
        "usage": record.usage_total,
        "error": _safe_error(record.exception),
        "quota_remaining": remaining,
    }


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = now.date().toordinal() + 1
    next_date = datetime.fromordinal(tomorrow).replace(tzinfo=timezone.utc)
    return max(1, int((next_date - now).total_seconds()))
