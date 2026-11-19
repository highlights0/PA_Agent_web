"""Public AI analysis API with live, pollable progress traces."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
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
_SECRET_RE = re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+")


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


def _analysis_enabled(request: Request) -> bool:
    config = request.app.state.settings
    if config.public_analysis_enabled:
        return True
    if config.app_env.lower() == "development":
        return (
            request.app.state.ai_provider_store.has_active_key()
            or bool(config.ai_api_key.strip())
        )
    return False


def _safe_text(value: object, limit: int = 600) -> str:
    text = str(value or "")
    text = _SECRET_RE.sub(r"\1=***", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "***", text)
    return text[:limit]


def _safe_error(record_exception: object) -> dict | None:
    if not isinstance(record_exception, dict):
        return None
    safe = {
        key: record_exception[key]
        for key in (
            "type", "stage", "category", "missing_fields", "invalid_fields",
            "parse_position", "validation_attempts", "failed_check",
        )
        if key in record_exception
    }
    if record_exception.get("message"):
        safe["message"] = _safe_text(record_exception["message"])
    return safe


def _validate_payload(payload: AnalysisRequest, request: Request) -> str:
    config = request.app.state.settings
    if not _analysis_enabled(request):
        raise HTTPException(
            status_code=503,
            detail="AI analysis is disabled or no usable model is configured. 请先在网页 AI 模型设置中配置模型。",
        )
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
    return symbol


def _result_payload(
    run: object,
    payload: AnalysisRequest,
    symbol: str,
    remaining: int,
    analysis_id: str | None = None,
) -> dict:
    record = run.record
    return {
        "analysis_id": analysis_id or str(uuid4()),
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
        "trace": run.trace,
        "details": run.details,
    }


def _seconds_until_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = now.date().toordinal() + 1
    next_date = datetime.fromordinal(tomorrow).replace(tzinfo=timezone.utc)
    return max(1, int((next_date - now).total_seconds()))


def _consume_quota(request: Request) -> int:
    allowed, remaining = request.app.state.analysis_quota.consume(_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Daily public AI analysis quota exhausted",
            headers={"Retry-After": str(_seconds_until_utc_midnight())},
        )
    return remaining


def _translate_analysis_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AnalysisConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, InsufficientMarketDataError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, UnsupportedMarketProvider):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, BinanceAPIError):
        if exc.status_code in (418, 429):
            return HTTPException(
                status_code=503,
                detail="Selected exchange is rate-limiting this service; retry later",
                headers={"Retry-After": exc.retry_after or "5"},
            )
        return HTTPException(
            status_code=502,
            detail=f"Selected exchange market data is unavailable (HTTP {exc.status_code or 'network error'})",
        )
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    logger.exception("Public AI analysis failed", exc_info=exc)
    return HTTPException(status_code=502, detail="AI analysis failed; check server logs")


@router.get("/analysis/quota")
async def analysis_quota(request: Request):
    ip = _client_ip(request)
    return {
        "enabled": _analysis_enabled(request),
        "daily_limit": request.app.state.settings.public_analysis_per_ip_daily,
        "remaining": request.app.state.analysis_quota.remaining(ip),
        "scope": "per_ip_utc_day_in_process",
    }


@router.post("/analysis/jobs", status_code=status.HTTP_202_ACCEPTED)
async def start_analysis_job(payload: AnalysisRequest, request: Request):
    """Start a background analysis; poll GET /analysis/jobs/{job_id} for live trace."""
    symbol = _validate_payload(payload, request)
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
        remaining = _consume_quota(request)
        job = request.app.state.analysis_jobs.create(
            _client_ip(request),
            {
                "market": payload.market,
                "exchange": payload.exchange,
                "symbol": symbol,
                "timeframe": payload.timeframe,
                "analysis_bars": payload.analysis_bars,
            },
        )
        task = asyncio.create_task(
            _execute_analysis_job(
                request,
                job["job_id"],
                payload,
                symbol,
                remaining,
                semaphore,
            )
        )
        request.app.state.analysis_tasks.add(task)
        task.add_done_callback(request.app.state.analysis_tasks.discard)
        return job
    except Exception:
        semaphore.release()
        raise


async def _execute_analysis_job(
    request: Request,
    job_id: str,
    payload: AnalysisRequest,
    symbol: str,
    remaining: int,
    semaphore: asyncio.Semaphore,
) -> None:
    store = request.app.state.analysis_jobs
    store.set_running(job_id)
    try:
        run = await request.app.state.analysis_service.analyze(
            payload.market,
            symbol,
            payload.timeframe,
            payload.analysis_bars,
            payload.exchange,
            on_trace=lambda event: store.append_event(job_id, event),
        )
        result = _result_payload(
            run,
            payload,
            symbol,
            remaining,
            analysis_id=job_id,
        )
        store.complete(
            job_id,
            result=result,
            details=run.details,
            trace=run.trace,
        )
    except Exception as exc:  # noqa: BLE001
        error = _translate_analysis_error(exc)
        store.fail(
            job_id,
            {
                "status_code": error.status_code,
                "message": _safe_text(error.detail),
            },
        )
    finally:
        semaphore.release()


@router.get("/analysis/jobs/{job_id}")
async def get_analysis_job(job_id: str, request: Request):
    job = request.app.state.analysis_jobs.get(job_id, _client_ip(request))
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job


@router.post("/analysis")
async def submit_analysis(payload: AnalysisRequest, request: Request):
    """Synchronous compatibility endpoint; new Web UI uses the pollable jobs API."""
    symbol = _validate_payload(payload, request)
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
        remaining = _consume_quota(request)
        run = await request.app.state.analysis_service.analyze(
            payload.market,
            symbol,
            payload.timeframe,
            payload.analysis_bars,
            payload.exchange,
        )
    except Exception as exc:
        raise _translate_analysis_error(exc) from exc
    finally:
        semaphore.release()

    return _result_payload(run, payload, symbol, remaining)
