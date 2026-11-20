"""Persistent analysis/search history and leakage-safe PA dataset endpoints."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.ai_settings import _authorize
from app.services.historical_backfill import backfill_market_candles
from app.services.derivatives_history import collect_derivatives_history
from app.services.historical_replay import context_period
from app.services.baseline_evaluation import evaluate_rule_baseline
from app.services.segmenter import build_current_features, build_segment_samples

async def require_history_access(
    request: Request,
    x_pa_admin_token: str | None = Header(default=None),
):
    # IP-based record grouping is not authentication.
    _authorize(request, x_pa_admin_token)


router = APIRouter(dependencies=[Depends(require_history_access)])
_SYMBOL_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")


class SearchHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_text: str = Field(default="", max_length=200)
    market: Literal["spot", "usdm"]
    exchange: Literal["binance", "binance_us", "coinbase", "gate"]
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = Field(min_length=2, max_length=8)


class DatasetBuildInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["spot", "usdm"]
    exchange: Literal["binance", "binance_us", "coinbase", "gate"]
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = Field(min_length=2, max_length=8)
    limit: int = Field(default=1000, ge=80, le=1000)


def _owner(request: Request) -> str:
    # Match the analysis job's client scope. Deployments exposed to multiple
    # users should place real authentication in front of this API.
    forwarded = request.headers.get("x-real-ip", "").strip()
    if forwarded:
        return forwarded[:64]
    return request.client.host if request.client else "unknown"


def _history(request: Request):
    return request.app.state.history_store


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="交易对格式不正确")
    return normalized


@router.post("/history/searches", status_code=201)
async def save_search_history(payload: SearchHistoryInput, request: Request):
    symbol = _symbol(payload.symbol)
    _history(request).save_search(
        _owner(request),
        query_text=payload.query_text or symbol,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    return {"saved": True}


@router.get("/history/searches")
async def list_search_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
):
    return {"items": _history(request).list_searches(_owner(request), limit)}


@router.delete("/history/searches/{search_id}")
async def delete_search_history(search_id: int, request: Request):
    if not _history(request).delete_search(_owner(request), search_id):
        raise HTTPException(status_code=404, detail="搜索记录不存在")
    return {"deleted": True}


@router.get("/history/analyses")
async def list_analysis_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    symbol: str | None = None,
    exchange: str | None = None,
):
    normalized = _symbol(symbol) if symbol else None
    return {
        "items": _history(request).list_analyses(
            _owner(request),
            limit=limit,
            symbol=normalized,
            exchange=exchange,
        )
    }


@router.get("/history/analyses/compare")
async def compare_analysis_history(
    request: Request,
    left_id: str,
    right_id: str,
):
    try:
        return _history(request).compare_analyses(_owner(request), left_id, right_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@router.get("/history/analyses/{analysis_id}")
async def get_analysis_history(analysis_id: str, request: Request):
    result = _history(request).get_analysis(_owner(request), analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="分析历史不存在")
    return result


@router.delete("/history/analyses/{analysis_id}")
async def delete_analysis_history(analysis_id: str, request: Request):
    if not _history(request).delete_analysis(_owner(request), analysis_id):
        raise HTTPException(status_code=404, detail="分析历史不存在")
    return {"deleted": True}


@router.post("/history/dataset/build")
async def build_historical_dataset(payload: DatasetBuildInput, request: Request):
    symbol = _symbol(payload.symbol)
    if payload.market == "usdm" and payload.exchange not in {"binance", "gate"}:
        raise HTTPException(status_code=422, detail="所选交易所尚未提供 USDT 永续数据")
    if payload.market == "spot" and payload.exchange == "gate":
        raise HTTPException(status_code=422, detail="Gate 目前只接入 USDT 永续")
    limit = min(payload.limit, 300) if payload.exchange == "coinbase" else payload.limit
    try:
        bars = await request.app.state.market_service.klines(
            payload.market,
            symbol,
            payload.timeframe,
            limit,
            payload.exchange,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"获取历史 K 线失败：{str(exc)[:240]}") from exc

    history = _history(request)
    stored_candles = await asyncio.to_thread(
        history.upsert_market_candles,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        candles=bars,
    )
    archived = await asyncio.to_thread(
        history.list_market_candles,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        limit=50000,
    )
    samples = await asyncio.to_thread(
        build_segment_samples,
        archived,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    stored = await asyncio.to_thread(history.upsert_segments, samples)
    stats = await asyncio.to_thread(
        history.dataset_stats,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    coverage = await asyncio.to_thread(
        history.market_candle_coverage,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    return {
        "requested_bars": limit,
        "received_bars": len(bars),
        "closed_bars": sum(1 for bar in bars if bar.closed),
        "stored_candles": stored_candles,
        "archive_candles": coverage["candle_count"],
        "new_samples": stored,
        "dataset": stats,
        "note": "已收盘 K 线已归档；样本从完整归档重建。未来标签只用于事后评估，不进入特征或 AI Prompt。",
    }


class BackfillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["spot", "usdm"]
    exchange: Literal["binance", "binance_us", "coinbase", "gate"]
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = Field(min_length=2, max_length=8)
    bars: int = Field(default=3000, ge=80, le=12000)


class BatchBackfillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jobs: list[BackfillInput] = Field(min_length=1, max_length=24)


class DerivativesBackfillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["usdm"] = "usdm"
    exchange: Literal["binance", "gate"]
    symbol: str = Field(min_length=3, max_length=32)
    period: str = Field(default="15m", min_length=2, max_length=8)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class HistoricalReplayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["spot", "usdm"]
    exchange: Literal["binance", "binance_us", "coinbase", "gate"]
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = Field(min_length=2, max_length=8)
    analysis_bars: int = Field(default=100, ge=20, le=250)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    points: int = Field(default=5, ge=1, le=20)


@router.post("/history/dataset/backfill")
async def backfill_history(payload: BackfillInput, request: Request):
    """Resume a bounded backfill from the oldest archived candle."""
    symbol = _symbol(payload.symbol)
    history = _history(request)
    coverage = await asyncio.to_thread(
        history.market_candle_coverage,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    existing_count = int(coverage["candle_count"])
    remaining = max(0, payload.bars - existing_count)
    response = {
        "bars": [],
        "pages": 0,
        "gaps": 0,
        "closed_bars": 0,
        "source": payload.exchange,
        "truncated": False,
    }
    if remaining:
        try:
            response = await backfill_market_candles(
                request.app.state.market_service,
                market=payload.market,
                exchange=payload.exchange,
                symbol=symbol,
                timeframe=payload.timeframe,
                requested_bars=max(80, remaining),
                before_open_time_ms=coverage["oldest_open_time_ms"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"历史回补参数不受支持：{str(exc)[:240]}") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"历史 K 线回补失败：{str(exc)[:240]}") from exc

    stored_candles = await asyncio.to_thread(
        history.upsert_market_candles,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        candles=response["bars"],
    )
    archived = await asyncio.to_thread(
        history.list_market_candles,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        limit=50000,
    )
    samples = await asyncio.to_thread(
        build_segment_samples,
        archived,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    new_samples = await asyncio.to_thread(history.upsert_segments, samples)
    coverage_after = await asyncio.to_thread(
        history.market_candle_coverage,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    return {
        "requested_bars": payload.bars,
        "received_bars": response["closed_bars"],
        "stored_candles": stored_candles,
        "archive_candles": coverage_after["candle_count"],
        "pages": response["pages"],
        "gaps": response["gaps"],
        "truncated": response["truncated"] or coverage_after["candle_count"] < payload.bars,
        "new_samples": new_samples,
        "dataset": await asyncio.to_thread(
            history.dataset_stats,
            market=payload.market,
            exchange=payload.exchange,
            symbol=symbol,
            timeframe=payload.timeframe,
        ),
        "note": (
            f"{payload.exchange} 历史 K 线归档已更新；"
            "重复执行会从最早已归档时间继续向前补齐，重复 K 线按唯一键去重。"
            "缺失 K  线保留为时间缺口，样本生成不会跨缺口构造未来标签。"
        ),
    }


async def _backfill_one_item(app: Any, payload: BackfillInput) -> dict[str, Any]:
    history = app.state.history_store
    symbol = _symbol(payload.symbol)
    coverage = await asyncio.to_thread(
        history.market_candle_coverage,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    existing_count = int(coverage["candle_count"])
    remaining = max(0, payload.bars - existing_count)
    response = {
        "bars": [],
        "pages": 0,
        "gaps": 0,
        "closed_bars": 0,
        "source": payload.exchange,
        "truncated": False,
    }
    if remaining:
        response = await backfill_market_candles(
            app.state.market_service,
            market=payload.market,
            exchange=payload.exchange,
            symbol=symbol,
            timeframe=payload.timeframe,
            requested_bars=max(80, remaining),
            before_open_time_ms=coverage["oldest_open_time_ms"],
        )
    stored_candles = await asyncio.to_thread(
        history.upsert_market_candles,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        candles=response["bars"],
    )
    archived = await asyncio.to_thread(
        history.list_market_candles,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        limit=50000,
    )
    samples = await asyncio.to_thread(
        build_segment_samples,
        archived,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    new_samples = await asyncio.to_thread(history.upsert_segments, samples)
    coverage_after = await asyncio.to_thread(
        history.market_candle_coverage,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    return {
        "status": "completed",
        "market": payload.market,
        "exchange": payload.exchange,
        "symbol": symbol,
        "timeframe": payload.timeframe,
        "requested_bars": payload.bars,
        "received_bars": response["closed_bars"],
        "stored_candles": stored_candles,
        "archive_candles": coverage_after["candle_count"],
        "pages": response["pages"],
        "gaps": response["gaps"],
        "truncated": response["truncated"] or coverage_after["candle_count"] < payload.bars,
        "new_samples": new_samples,
    }


async def _run_batch_backfill(app: Any, job_id: str, jobs: list[dict[str, Any]]) -> None:
    job_store = app.state.backfill_jobs
    job_store.set_running(job_id)
    async with app.state.backfill_semaphore:
        for index, item in enumerate(jobs, start=1):
            job_store.set_current(job_id, item, index)
            try:
                result = await _backfill_one_item(app, BackfillInput(**item))
            except Exception as exc:  # noqa: BLE001
                result = {
                    "status": "failed",
                    **item,
                    "requested_bars": item.get("bars", 0),
                    "error": str(exc)[:300],
                }
            job_store.complete_item(job_id, result)
    job_store.finish(job_id)


@router.post("/history/dataset/batch-backfill", status_code=202)
async def start_batch_backfill(payload: BatchBackfillInput, request: Request):
    items = [item.model_dump() for item in payload.jobs]
    keys = [
        (item["market"], item["exchange"], _symbol(item["symbol"]), item["timeframe"])
        for item in items
    ]
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=422, detail="批量任务中存在重复的交易对与周期组合")
    if sum(item["bars"] for item in items) > 60000:
        raise HTTPException(status_code=422, detail="单批回补总量不能超过 60000 根 K 线")
    for item in items:
        try:
            request.app.state.market_service._provider(item["market"], item["exchange"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    owner = _owner(request)
    job = request.app.state.backfill_jobs.create(owner, items)
    task = asyncio.create_task(
        _run_batch_backfill(request.app, job["job_id"], items)
    )
    request.app.state.backfill_tasks.add(task)
    task.add_done_callback(request.app.state.backfill_tasks.discard)
    return job


@router.get("/history/dataset/batch-backfill/{job_id}")
async def get_batch_backfill(job_id: str, request: Request):
    job = request.app.state.backfill_jobs.get(job_id, _owner(request))
    if job is None:
        raise HTTPException(status_code=404, detail="批量回补任务不存在或已过期")
    return job


def _replay_interval_ms(timeframe: str) -> int | None:
    return {
        "1m": 60_000, "3m": 180_000, "5m": 300_000,
        "15m": 900_000, "30m": 1_800_000,
        "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
        "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
        "1d": 86_400_000, "3d": 259_200_000,
        "1w": 604_800_000, "7d": 604_800_000,
    }.get(timeframe)


@router.post("/history/replay", status_code=202)
async def start_historical_replay(payload: HistoricalReplayInput, request: Request):
    symbol = _symbol(payload.symbol)
    if payload.market == "usdm" and payload.exchange not in {"binance", "gate"}:
        raise HTTPException(status_code=422, detail="USDT 永续历史回放仅支持 Binance 和 Gate")
    if payload.market == "spot" and payload.exchange == "gate":
        raise HTTPException(status_code=422, detail="Gate 现货历史回放暂不支持")
    interval_ms = _replay_interval_ms(payload.timeframe)
    if interval_ms is None:
        raise HTTPException(status_code=422, detail="当前周期暂不支持历史回放")
    if payload.end_ms < payload.start_ms:
        raise HTTPException(status_code=422, detail="结束时间不能早于开始时间")
    now_ms = int(time.time() * 1000)
    if payload.end_ms >= now_ms:
        raise HTTPException(status_code=422, detail="历史回放结束时间必须早于当前时间")
    if payload.end_ms - payload.start_ms > 366 * 24 * 60 * 60 * 1000:
        raise HTTPException(status_code=422, detail="单次回放时间范围不能超过 366 天")
    if payload.analysis_bars + 50 > request.app.state.settings.max_klines:
        raise HTTPException(status_code=422, detail="分析 K 线数量超过服务器限制")

    required_bars = payload.analysis_bars + 50
    archived = await asyncio.to_thread(
        _history(request).list_market_candles,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
        end_ms=payload.end_ms,
        limit=50000,
    )
    if len(archived) < required_bars:
        raise HTTPException(
            status_code=422,
            detail=f"归档只有 {len(archived)} 根 K 线，至少需要 {required_bars} 根",
        )

    candidates: list[int] = []
    for index in range(required_bars - 1, len(archived)):
        close_time = int(archived[index]["close_time"])
        decision_time = close_time + 1
        if decision_time < payload.start_ms or decision_time > payload.end_ms:
            continue
        window = archived[index - required_bars + 1 : index + 1]
        if all(
            int(right["open_time"]) - int(left["open_time"]) == interval_ms
            for left, right in zip(window, window[1:])
        ):
            candidates.append(decision_time)
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="指定时间范围内没有足够长且连续的已归档 K 线窗口",
        )

    point_count = min(payload.points, len(candidates))
    if point_count == 1:
        decision_times = [candidates[-1]]
    else:
        indices = [
            round(index * (len(candidates) - 1) / (point_count - 1))
            for index in range(point_count)
        ]
        decision_times = list(dict.fromkeys(candidates[index] for index in indices))

    items = [
        {
            "market": payload.market,
            "exchange": payload.exchange,
            "symbol": symbol,
            "timeframe": payload.timeframe,
            "analysis_bars": payload.analysis_bars,
            "decision_time_ms": decision_time,
        }
        for decision_time in decision_times
    ]
    owner = _owner(request)
    job = request.app.state.replay_jobs.create(owner, items)
    task = asyncio.create_task(
        _run_historical_replay(request.app, job["job_id"], owner, items, job["created_at"])
    )
    request.app.state.replay_tasks.add(task)
    task.add_done_callback(request.app.state.replay_tasks.discard)
    return {
        **job,
        "candidate_count": len(candidates),
        "selected_points": len(decision_times),
        "note": "回放时点取自归档 K 线收盘边界；只使用决策时点已收盘 K 线和当时已可用的衍生品记录。",
    }


async def _run_historical_replay(
    app: Any,
    job_id: str,
    owner: str,
    items: list[dict[str, Any]],
    created_at: str,
) -> None:
    jobs = app.state.replay_jobs
    jobs.set_running(job_id)
    async with app.state.replay_semaphore:
        for index, item in enumerate(items, start=1):
            jobs.set_current(job_id, item, index)
            analysis_id = f"replay-{job_id}-{item['decision_time_ms']}"
            trace_events: list[dict[str, Any]] = []
            request_data = {
                **item,
                "historical_replay": True,
                "replay_job_id": job_id,
            }
            try:
                async with app.state.analysis_semaphore:
                    run = await app.state.analysis_service.analyze_historical(
                        item["market"],
                        item["symbol"],
                        item["timeframe"],
                        item["analysis_bars"],
                        item["exchange"],
                        item["decision_time_ms"],
                        on_trace=trace_events.append,
                    )
                result = {
                    "analysis_id": analysis_id,
                    "status": "failed" if run.record.exception else "completed",
                    "market": item["market"],
                    "exchange": item["exchange"],
                    "symbol": item["symbol"],
                    "timeframe": item["timeframe"],
                    "decision_time_ms": item["decision_time_ms"],
                    "bar_count": run.record.meta.bar_count,
                    "events": run.events,
                    "stage1_diagnosis": run.record.stage1_diagnosis,
                    "stage2_decision": run.record.stage2_decision,
                    "usage": run.record.usage_total,
                    "error": run.record.exception,
                    "trace": run.trace,
                    "details": run.details,
                    "historical_replay": True,
                }
                await asyncio.to_thread(
                    app.state.history_store.save_analysis,
                    analysis_id=analysis_id,
                    owner=owner,
                    request_data=request_data,
                    status=result["status"],
                    result=result,
                    trace=run.trace,
                    details=run.details,
                    created_at=created_at,
                )
                jobs.complete_item(
                    job_id,
                    {
                        "status": result["status"],
                        "analysis_id": analysis_id,
                        "decision_time_ms": item["decision_time_ms"],
                        "stage1_diagnosis": run.record.stage1_diagnosis,
                        "stage2_decision": run.record.stage2_decision,
                        "usage": run.record.usage_total,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)[:400]
                await asyncio.to_thread(
                    app.state.history_store.save_analysis,
                    analysis_id=analysis_id,
                    owner=owner,
                    request_data=request_data,
                    status="failed",
                    result={
                        "analysis_id": analysis_id,
                        "status": "failed",
                        "error": {"message": message},
                        "historical_replay": True,
                    },
                    trace=trace_events,
                    details={},
                    created_at=created_at,
                )
                jobs.complete_item(
                    job_id,
                    {
                        "status": "failed",
                        "analysis_id": analysis_id,
                        "decision_time_ms": item["decision_time_ms"],
                        "error": message,
                    },
                )
    jobs.finish(job_id)


@router.get("/history/replay/{job_id}")
async def get_historical_replay(job_id: str, request: Request):
    job = request.app.state.replay_jobs.get(job_id, _owner(request))
    if job is None:
        raise HTTPException(status_code=404, detail="历史回放任务不存在或已过期")
    return job


@router.post("/history/derivatives/backfill")
async def backfill_derivatives_history(
    payload: DerivativesBackfillInput,
    request: Request,
):
    symbol = _symbol(payload.symbol)
    now_ms = int(time.time() * 1000)
    if payload.end_ms < payload.start_ms:
        raise HTTPException(status_code=422, detail="结束时间不能早于开始时间")
    if payload.end_ms > now_ms:
        raise HTTPException(status_code=422, detail="历史回补结束时间不能晚于当前时间")
    if payload.end_ms - payload.start_ms > 366 * 24 * 60 * 60 * 1000:
        raise HTTPException(status_code=422, detail="单次衍生品历史回补范围不能超过 366 天")

    try:
        result = await collect_derivatives_history(
            request.app.state.market_service,
            exchange=payload.exchange,
            symbol=symbol,
            period=payload.period,
            start_ms=payload.start_ms,
            end_ms=payload.end_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"衍生品历史回补参数不受支持：{str(exc)[:240]}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"衍生品历史回补失败：{str(exc)[:240]}") from exc

    history = _history(request)
    stored = await asyncio.to_thread(
        history.upsert_derivatives_observations,
        result["observations"],
    )
    run = {
        key: value
        for key, value in result.items()
        if key not in {"observations", "observation_count"}
    }
    await asyncio.to_thread(history.save_derivatives_collection_run, run)
    return {
        "run_id": result["run_id"],
        "market": result["market"],
        "exchange": result["exchange"],
        "symbol": result["symbol"],
        "period": result["period"],
        "start_time_ms": result["start_time_ms"],
        "end_time_ms": result["end_time_ms"],
        "fetched_at_ms": result["fetched_at_ms"],
        "status": result["status"],
        "observation_count": result["observation_count"],
        "stored_observations": stored,
        "metrics": result["metrics"],
        "note": result["note"],
    }


@router.get("/history/derivatives")
async def list_derivatives_history(
    request: Request,
    exchange: Literal["binance", "gate"],
    symbol: str,
    market: Literal["usdm"] = "usdm",
    start_ms: int | None = None,
    end_ms: int | None = None,
    metric: str | None = None,
    period: str | None = None,
    data_kind: Literal["historical", "snapshot"] | None = None,
    limit: int = Query(default=5000, ge=1, le=50000),
):
    if start_ms is not None and start_ms < 0:
        raise HTTPException(status_code=422, detail="start_ms 不能小于 0")
    if end_ms is not None and end_ms < 0:
        raise HTTPException(status_code=422, detail="end_ms 不能小于 0")
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise HTTPException(status_code=422, detail="结束时间不能早于开始时间")
    return {
        "items": await asyncio.to_thread(
            _history(request).list_derivatives_observations,
            market=market,
            exchange=exchange,
            symbol=_symbol(symbol),
            start_ms=start_ms,
            end_ms=end_ms,
            metric=metric,
            period=period,
            data_kind=data_kind,
            limit=limit,
        )
    }


@router.get("/history/derivatives/stats")
async def get_derivatives_archive_stats(
    request: Request,
    market: Literal["usdm"] = "usdm",
    exchange: Literal["binance", "gate"] | None = None,
    symbol: str | None = None,
):
    return {
        "items": await asyncio.to_thread(
            _history(request).derivatives_archive_stats,
            market=market,
            exchange=exchange,
            symbol=_symbol(symbol) if symbol else None,
        )
    }


@router.get("/history/derivatives/runs")
async def list_derivatives_collection_runs(
    request: Request,
    exchange: Literal["binance", "gate"],
    symbol: str,
    market: Literal["usdm"] = "usdm",
    limit: int = Query(default=20, ge=1, le=100),
):
    return {
        "items": await asyncio.to_thread(
            _history(request).list_derivatives_collection_runs,
            market=market,
            exchange=exchange,
            symbol=_symbol(symbol),
            limit=limit,
        )
    }


@router.get("/history/candles/stats")
async def get_market_candle_stats(
    request: Request,
    market: str | None = None,
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
):
    return {
        "items": await asyncio.to_thread(
            _history(request).market_candle_stats,
            market=market,
            exchange=exchange,
            symbol=_symbol(symbol) if symbol else None,
            timeframe=timeframe,
        )
    }


@router.get("/history/candles")
async def list_market_candles(
    request: Request,
    market: Literal["spot", "usdm"],
    exchange: Literal["binance", "binance_us", "coinbase", "gate"],
    symbol: str,
    timeframe: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
):
    return {
        "items": await asyncio.to_thread(
            _history(request).list_market_candles,
            market=market,
            exchange=exchange,
            symbol=_symbol(symbol),
            timeframe=timeframe,
            start_ms=start_ms,
            end_ms=end_ms,
            limit=limit,
        )
    }


@router.get("/history/dataset/evaluation")
async def evaluate_history_dataset(
    request: Request,
    market: Literal["spot", "usdm"],
    exchange: Literal["binance", "binance_us", "coinbase", "gate"],
    symbol: str,
    timeframe: str,
):
    samples = _history(request).evaluation_samples(
        market=market, exchange=exchange,
        symbol=_symbol(symbol), timeframe=timeframe,
    )
    return evaluate_rule_baseline(samples)


@router.get("/history/dataset/stats")
async def get_dataset_stats(
    request: Request,
    market: str | None = None,
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
):
    return _history(request).dataset_stats(
        market=market,
        exchange=exchange,
        symbol=_symbol(symbol) if symbol else None,
        timeframe=timeframe,
    )


@router.get("/history/dataset/samples")
async def list_dataset_samples(
    request: Request,
    market: str | None = None,
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    split: Literal["train", "validation", "test", "embargo", "unassigned"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {
        "items": _history(request).list_segments(
            market=market,
            exchange=exchange,
            symbol=_symbol(symbol) if symbol else None,
            timeframe=timeframe,
            split=split,
            limit=limit,
        )
    }


@router.get("/history/dataset/similar")
async def find_similar_historical_segments(
    request: Request,
    market: Literal["spot", "usdm"],
    exchange: Literal["binance", "binance_us", "coinbase", "gate"],
    symbol: str,
    timeframe: str,
    limit: int = Query(default=5, ge=1, le=20),
):
    symbol = _symbol(symbol)
    try:
        bars = await request.app.state.market_service.klines(
            market, symbol, timeframe, 100, exchange
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"获取当前行情特征失败：{str(exc)[:240]}") from exc
    current = build_current_features(bars)
    if current is None:
        return {"current": None, "items": [], "note": "已收盘 K 线不足，无法构建当前特征。"}
    items = _history(request).similar_segments(
        market=market,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        features=current,
        before_ms=current["decision_time_ms"],
        limit=limit,
    )
    return {
        "current": {
            "regime": current["regime"],
            "features": current["features"],
            "decision_time_ms": current["decision_time_ms"],
        },
        "items": items,
        "note": "相似度是可解释的特征距离分数，不代表未来收益概率或模型准确率。",
    }
