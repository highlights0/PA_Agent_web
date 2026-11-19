"""Per-job adapter around the original synchronous PA two-stage orchestrator."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapters.binance.models import MarketType
from app.adapters.binance.normalizer import to_core_bars
from app.core.config import Settings as WebSettings
from app.services.crypto_context import CryptoMarketContext, render_crypto_context
from app.services.crypto_prompt_assembler import CryptoPromptAssembler
from app.services.analysis_trace import AnalysisTrace, TracingClient, TracingValidator

logger = logging.getLogger("pa_crypto.analysis")


class AnalysisConfigurationError(RuntimeError):
    pass


class InsufficientMarketDataError(ValueError):
    pass


class _NoopRecordWriter:
    """Temporary writer for the stateless P2 adapter; database persistence comes in P3."""

    def save_full(self, record: Any) -> None:
        return None

    def save_partial(self, record: Any, reason: str) -> None:
        return None


@dataclass(slots=True)
class AnalysisRun:
    record: Any
    events: list[str]
    trace: list[dict[str, Any]]
    details: dict[str, Any]


def _context_period(timeframe: str) -> str:
    supported = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
    if timeframe in supported:
        return timeframe
    if timeframe in {"1m", "3m"}:
        return "5m"
    if timeframe in {"8h"}:
        return "6h"
    return "1d"


class AnalysisService:
    """Builds canonical KlineFrame input and runs a fresh orchestrator per request."""

    def __init__(
        self,
        market_service: Any,
        web_settings: WebSettings,
        provider_store: Any | None = None,
    ) -> None:
        self._market = market_service
        self._web_settings = web_settings
        self._provider_store = provider_store

    def _active_provider(self) -> dict[str, Any]:
        if self._provider_store is not None:
            saved = self._provider_store.get_decrypted()
            if saved and saved.get("api_key"):
                return {
                    "name": saved.get("name", "AI Provider"),
                    "model": saved["model"],
                    "base_url": saved["base_url"],
                    "api_key": saved["api_key"],
                    "thinking": saved.get("thinking", True),
                    "reasoning_effort": saved.get("reasoning_effort", "medium"),
                    "context_window": saved.get("context_window", 200000),
                    "backup_glm_api_key": saved.get("backup_glm_api_key", ""),
                }

        # Backward compatibility for operators who still configure the legacy env vars.
        if self._web_settings.ai_api_key.strip():
            return {
                "name": "Environment provider",
                "model": self._web_settings.ai_model,
                "base_url": self._web_settings.ai_base_url,
                "api_key": self._web_settings.ai_api_key,
                "thinking": self._web_settings.ai_thinking,
                "reasoning_effort": self._web_settings.ai_reasoning_effort,
                "context_window": 200000,
                "backup_glm_api_key": "",
            }
        raise AnalysisConfigurationError(
            "尚未配置 AI 模型。请打开网页右上角「AI 模型设置」，添加 API Base URL、模型和 API Key。"
        )

    async def analyze(
        self,
        market: MarketType,
        symbol: str,
        timeframe: str,
        analysis_bars: int = 100,
        exchange: str = "binance",
        on_trace: Any | None = None,
    ) -> AnalysisRun:
        provider_config = self._active_provider()
        trace = AnalysisTrace(on_trace)
        trace.emit(
            "analysis_started",
            stage="system",
            title="分析任务已启动",
            detail=f"{symbol} · {timeframe} · {exchange} · {market}",
            data={
                "symbol": symbol,
                "timeframe": timeframe,
                "exchange": exchange,
                "market": market,
                "analysis_bars": analysis_bars,
            },
        )
        if analysis_bars < 20 or analysis_bars + 50 > self._web_settings.max_klines:
            raise ValueError("analysis_bars must be between 20 and server max bars minus 50")
        if market == "usdm" and exchange not in {"binance", "gate"}:
            raise ValueError(
                f"{exchange} USDT perpetual futures are not configured; no cross-exchange substitution is allowed"
            )
        if market == "spot" and exchange == "gate":
            raise ValueError("Gate provider currently supports USDT perpetual futures only")

        trace.emit(
            "market_data_fetch_started",
            stage="market",
            title="正在获取分析所需 K 线",
            detail=f"请求 {analysis_bars + 50} 根 {timeframe} K 线",
        )
        rows = await self._market.klines(
            market, symbol, timeframe, analysis_bars + 50, exchange
        )
        core_bars = to_core_bars(rows)
        trace.emit(
            "market_data_loaded",
            stage="market",
            title="K 线数据获取完成",
            detail=f"收到 {len(rows)} 根，已收盘 {len(core_bars)} 根",
            data={"received_bars": len(rows), "closed_bars": len(core_bars)},
        )
        from pa_agent.data.snapshot import build_analysis_frame

        frame = build_analysis_frame(
            core_bars,
            analysis_bars,
            symbol,
            timeframe,
        )
        if frame is None:
            trace.emit(
                "frame_build_failed",
                stage="market",
                title="分析数据帧构建失败",
                detail=f"不足以构建 {analysis_bars} 根分析 K 线",
            )
            raise InsufficientMarketDataError(
                f"Not enough closed candles to build {analysis_bars}-bar analysis frame"
            )
        trace.emit(
            "frame_ready",
            stage="market",
            title="分析数据帧构建完成",
            detail=f"{frame.symbol} · {frame.timeframe} · {len(frame.bars)} 根",
            data={
                "symbol": frame.symbol,
                "timeframe": frame.timeframe,
                "bar_count": len(frame.bars),
                "latest_close": getattr(frame.bars[0], "close", None) if frame.bars else None,
            },
        )

        if market == "usdm":
            context_period = "5m" if exchange == "gate" else _context_period(timeframe)
            trace.emit(
                "derivatives_context_started",
                stage="market",
                title="正在获取合约衍生品上下文",
                detail=f"{exchange} · {symbol} · {context_period}",
            )
            derivatives = await self._market.futures_context(
                symbol,
                context_period,
                exchange,
            )
            context = CryptoMarketContext(
                market_type=market,
                **derivatives.model_dump(),
            )
            trace.emit(
                "derivatives_context_loaded",
                stage="market",
                title="合约衍生品上下文已加载",
                detail="资金费率、OI、多空比和可用爆仓统计已附加",
                data={
                    "exchange": exchange,
                    "funding_available": derivatives.funding_rate is not None,
                    "open_interest_available": derivatives.open_interest is not None,
                    "long_short_available": derivatives.global_long_short_ratio is not None,
                    "liquidation_available": (
                        derivatives.long_liquidation_usd_3h is not None
                        or derivatives.short_liquidation_usd_3h is not None
                    ),
                    "funding_rate": derivatives.funding_rate,
                    "mark_price": derivatives.mark_price,
                    "index_price": derivatives.index_price,
                    "open_interest": derivatives.open_interest,
                    "open_interest_usd": derivatives.open_interest_usd,
                    "open_interest_change_pct": derivatives.open_interest_change_pct,
                    "global_long_short_ratio": derivatives.global_long_short_ratio,
                    "taker_long_short_ratio": derivatives.taker_long_short_ratio,
                    "top_trader_long_short_account_ratio": derivatives.top_trader_long_short_account_ratio,
                    "top_trader_long_short_position_ratio": derivatives.top_trader_long_short_position_ratio,
                    "long_liquidation_usd_3h": derivatives.long_liquidation_usd_3h,
                    "short_liquidation_usd_3h": derivatives.short_liquidation_usd_3h,
                    "stats_interval": derivatives.stats_interval,
                    "stats_window_minutes": derivatives.stats_window_minutes,
                    "data_timestamp": derivatives.data_timestamp,
                    "data_quality": derivatives.data_quality,
                },
            )
        else:
            import time

            context = CryptoMarketContext(
                market_type=f"spot:{exchange}",
                exchange=exchange,
                symbol=symbol,
                data_timestamp=int(time.time() * 1000),
                data_quality={
                    "derivatives": "not_available_from_selected_spot_provider",
                    "quote_volume": "not_provided_by_coinbase" if exchange == "coinbase" else "available",
                },
            )

        return await asyncio.to_thread(
            self._run_sync, frame, context, provider_config, trace
        )

    def _run_sync(
        self,
        frame: Any,
        context: CryptoMarketContext,
        provider_config: dict[str, Any],
        trace: AnalysisTrace,
    ) -> AnalysisRun:
        """Construct isolated settings/client/orchestrator state for one request."""
        from pa_agent.ai.deepseek_client import DeepSeekClient
        from pa_agent.ai.json_validator import JsonValidator
        from pa_agent.ai.router import route_strategy_files
        from pa_agent.config.paths import EXPERIENCE_DIR, PROMPT_DIR
        from pa_agent.config.settings import AIProviderSettings, Settings
        from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
        from pa_agent.records.experience_reader import ExperienceReader
        from pa_agent.util.threading import CancelToken

        settings = Settings(
            provider=AIProviderSettings(
                model=provider_config["model"],
                base_url=provider_config["base_url"],
                api_key=provider_config["api_key"],
                thinking=provider_config["thinking"],
                reasoning_effort=provider_config["reasoning_effort"],
                context_window=provider_config.get("context_window", 200000),
                backup_glm_api_key=provider_config.get("backup_glm_api_key", ""),
            )
        )
        if settings.provider.backup_glm_api_key:
            from pa_agent.ai.glm_backup import ensure_glm_fallback

            ensure_glm_fallback(settings.provider)
        settings.general.decision_stance = "conservative"
        settings.prompt.experience_max_entries = 0

        experience_reader = ExperienceReader(experience_dir=Path(EXPERIENCE_DIR))
        assembler = CryptoPromptAssembler(
            prompt_dir=Path(PROMPT_DIR),
            experience_reader=experience_reader,
            prompt_settings=settings.prompt,
            crypto_context_text=render_crypto_context(context),
        )
        client = TracingClient(
            DeepSeekClient(settings.provider, logger_=logger),
            trace,
        )
        validator = TracingValidator(JsonValidator(settings), trace)
        orchestrator = TwoStageOrchestrator(
            client=client,
            assembler=assembler,
            router=route_strategy_files,
            validator=validator,
            pending_writer=_NoopRecordWriter(),
            exp_reader=experience_reader,
            settings=settings,
        )
        events: list[str] = []

        def on_event(event: Any) -> None:
            name = getattr(event, "name", str(event))
            events.append(name)
            trace.orchestrator_event(event)

        def on_stage_prompt(stage: str, system: str, user: str) -> None:
            trace.prompt_ready(stage, system, user)

        def on_stage2_files(files: list[str]) -> None:
            trace.strategy_files_ready(files)

        trace.emit(
            "orchestrator_ready",
            stage="system",
            title="原版 PA 两阶段编排器已就绪",
            detail="保留原版预检、阶段一、策略路由、阶段二和校验重试逻辑",
        )
        record = orchestrator.submit(
            frame=frame,
            cancel_token=CancelToken(),
            on_event=on_event,
            on_stage_prompt=on_stage_prompt,
            on_stage2_files=on_stage2_files,
        )
        details = trace.details(
            frame=frame,
            context=context,
            record=record,
            provider=provider_config,
        )
        if details["stage2_short_circuited"]:
            trace.emit(
                "stage2_short_circuited",
                stage="stage2",
                title="阶段二由闸门条件短路",
                detail="阶段一 gate_result 为 wait/unknown；程序生成等待结果，没有调用第二次 AI",
            )
        trace.emit(
            "analysis_finished",
            stage="system",
            title="分析流程结束",
            detail=(
                "分析完成"
                if record.exception is None
                else "分析未完成，详情见异常与校验记录"
            ),
            data={
                "stage1_completed": record.stage1_diagnosis is not None,
                "stage2_completed": record.stage2_decision is not None,
                "usage": record.usage_total,
            },
        )
        return AnalysisRun(
            record=record,
            events=events,
            trace=trace.snapshot(),
            details=details,
        )
