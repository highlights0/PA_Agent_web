"""Persistent analysis/search history and leakage-safe PA dataset endpoints."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.ai_settings import _authorize
from app.services.historical_backfill import backfill_market_candles
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

    samples = build_segment_samples(
        bars,
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    stored = _history(request).upsert_segments(samples)
    stats = _history(request).dataset_stats(
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    return {
        "requested_bars": limit,
        "received_bars": len(bars),
        "closed_bars": sum(1 for bar in bars if bar.closed),
        "new_samples": stored,
        "dataset": stats,
        "note": "历史样本按已收盘 K 线构建；未来标签仅存入 labels，不进入 features 或 AI Prompt。",
    }



class BackfillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["spot", "usdm"]
    exchange: Literal["binance", "binance_us", "coinbase", "gate"]
    symbol: str = Field(min_length=3, max_length=32)
    timeframe: str = Field(min_length=2, max_length=8)
    bars: int = Field(default=3000, ge=80, le=12000)


@router.post("/history/dataset/backfill")
async def backfill_history(payload: BackfillInput, request: Request):
    """Backfill a bounded time range from the explicitly selected exchange."""
    symbol = _symbol(payload.symbol)
    try:
        response = await backfill_market_candles(
            request.app.state.market_service,
            market=payload.market,
            exchange=payload.exchange,
            symbol=symbol,
            timeframe=payload.timeframe,
            requested_bars=payload.bars,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"历史回补参数不受支持：{str(exc)[:240]}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"历史 K 线回补失败：{str(exc)[:240]}") from exc

    samples = build_segment_samples(
        response["bars"],
        market=payload.market,
        exchange=payload.exchange,
        symbol=symbol,
        timeframe=payload.timeframe,
    )
    new_samples = _history(request).upsert_segments(samples)
    return {
        "requested_bars": payload.bars,
        "received_bars": response["closed_bars"],
        "pages": response["pages"],
        "gaps": response["gaps"],
        "truncated": response["truncated"],
        "new_samples": new_samples,
        "dataset": _history(request).dataset_stats(
            market=payload.market,
            exchange=payload.exchange,
            symbol=symbol,
            timeframe=payload.timeframe,
        ),
        "note": (
            f"{payload.exchange} 历史 K 线分页回补完成；"
            "缺失 K 线会保留为时间缺口，样本生成不会跨缺口构造未来标签。"
            "回补数据仅用于样本与评估，不自动修改 AI 决策。"
        ),
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
