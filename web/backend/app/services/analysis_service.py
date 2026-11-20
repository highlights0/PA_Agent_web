"""Per-job adapter around the original synchronous PA two-stage orchestrator."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.adapters.binance.models import KlineDTO, MarketType
from app.adapters.binance.normalizer import to_core_bars
from app.core.config import Settings as WebSettings
from app.services.crypto_context import CryptoMarketContext, render_crypto_context
from app.services.crypto_prompt_assembler import CryptoPromptAssembler
from app.services.analysis_trace import AnalysisTrace, TracingClient, TracingValidator
from app.services.segmenter import build_current_features, build_segment_samples
from app.services.historical_replay import (
    build_historical_derivatives_context,
    context_period,
)

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
        history_store: Any | None = None,
    ) -> None:
        self._market = market_service
        self._web_settings = web_settings
        self._provider_store = provider_store
        self._history_store = history_store

    def _active_provider(self) -> dict[str, Any]:
        if self._provider_store is not None:
            saved = self._provider_store.get_decrypted()
            if saved and saved.get("api_key"):
                return {
                    "name": saved.get("name", "AI Provider"),
                    "provider_id": saved.get("id"),
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
                "provider_id": None,
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
        historical_similarity: list[dict[str, Any]] = []
        if self._history_store is not None:
            try:
                await asyncio.to_thread(
                    self._history_store.upsert_market_candles,
                    market=market,
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    candles=rows,
                )
                samples = build_segment_samples(
                    rows,
                    market=market,
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                )
                if samples:
                    await asyncio.to_thread(self._history_store.upsert_segments, samples)
                current_features = build_current_features(rows)
                if current_features is not None and self._web_settings.historical_similarity_enabled:
                    historical_similarity = await asyncio.to_thread(
                        self._history_store.similar_segments,
                        market=market,
                        exchange=exchange,
                        symbol=symbol,
                        timeframe=timeframe,
                        features=current_features,
                        before_ms=current_features["decision_time_ms"],
                        limit=5,
                    )
                    trace.emit(
                        "historical_similarity_ready",
                        stage="history",
                        title="历史相似行情检索完成",
                        detail=f"找到 {len(historical_similarity)} 个历史样本；仅供人工参考，不作为模型训练标签输入",
                        data={
                            "sample_ids": [item["sample_id"] for item in historical_similarity],
                            "similarities": [item["similarity"] for item in historical_similarity],
                        },
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Historical sample indexing/retrieval failed; continuing PA analysis")
                trace.emit(
                    "historical_dataset_warning",
                    stage="history",
                    title="历史样本库暂不可用",
                    detail="本次 PA 分析继续执行；历史样本保存/检索失败，请检查 SQLite 数据库",
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
            if self._history_store is not None:
                try:
                    await asyncio.to_thread(
                        self._history_store.save_derivatives_snapshot,
                        market=market,
                        exchange=exchange,
                        symbol=symbol,
                        context=derivatives.model_dump(),
                        source_endpoint=f"/api/v1/market/usdm/{symbol}/context",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to persist derivatives context snapshot")
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

        run = await asyncio.to_thread(
            self._run_sync, frame, context, provider_config, trace
        )
        run.details["historical_similarity"] = [
            {
                "sample_id": item["sample_id"],
                "regime": item["regime"],
                "similarity": item["similarity"],
                "decision_time_ms": item["decision_time_ms"],
                "labels": item["labels"],
                "split": item["split"],
            }
            for item in historical_similarity
        ]
        return run

    async def analyze_historical(
        self,
        market: MarketType,
        symbol: str,
        timeframe: str,
        analysis_bars: int,
        exchange: str,
        decision_time_ms: int,
        on_trace: Any | None = None,
    ) -> AnalysisRun:
        """Run the original PA orchestrator against an immutable as-of archive snapshot.

        This path deliberately never calls live exchange APIs and never loads
        future labels. Kline availability is determined by candle close time;
        derivatives are filtered by both event_time and available_at.
        """
        if self._history_store is None:
            raise AnalysisConfigurationError("历史行情归档未配置，无法执行历史回放")
        if analysis_bars < 20 or analysis_bars + 50 > self._web_settings.max_klines:
            raise ValueError("analysis_bars must be between 20 and server max bars minus 50")
        if decision_time_ms <= 0:
            raise ValueError("decision_time_ms must be a positive Unix millisecond timestamp")
        if market == "usdm" and exchange not in {"binance", "gate"}:
            raise ValueError("Historical USD-M replay is supported only for Binance and Gate")
        if market == "spot" and exchange == "gate":
            raise ValueError("Gate spot replay is not supported")

        interval_ms = {
            "1m": 60_000, "3m": 180_000, "5m": 300_000,
            "15m": 900_000, "30m": 1_800_000,
            "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
            "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
            "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
            "7d": 604_800_000,
        }.get(timeframe)
        if interval_ms is None:
            raise ValueError(f"Historical replay does not support timeframe {timeframe}")

        provider_config = self._active_provider()
        trace = AnalysisTrace(on_trace)
        trace.emit(
            "historical_replay_started",
            stage="system",
            title="历史回放分析已启动",
            detail=f"{symbol} · {timeframe} · {exchange} · 截止 {decision_time_ms}",
            data={
                "market": market,
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe,
                "analysis_bars": analysis_bars,
                "decision_time_ms": decision_time_ms,
            },
        )

        required_bars = analysis_bars + 50
        archived_rows = await asyncio.to_thread(
            self._history_store.list_market_candles,
            market=market,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_ms=decision_time_ms - 1,
            limit=min(required_bars + 20, 50000),
        )
        closed_rows = [
            item
            for item in archived_rows
            if int(item.get("close_time", 0)) < decision_time_ms
        ]
        if len(closed_rows) < required_bars:
            raise InsufficientMarketDataError(
                f"历史时点 {decision_time_ms} 之前只有 {len(closed_rows)} 根已收盘归档 K 线，"
                f"至少需要 {required_bars} 根；请先补齐历史行情。"
            )
        selected_rows = closed_rows[-required_bars:]
        for left, right in zip(selected_rows, selected_rows[1:]):
            if int(right["open_time"]) - int(left["open_time"]) != interval_ms:
                raise InsufficientMarketDataError(
                    "历史 K 线归档存在时间缺口，当前回放要求分析窗口连续；"
                    "请先补齐缺失区间或选择其他回放时点。"
                )

        candles = [
            KlineDTO(
                open_time=int(item["open_time"]),
                close_time=int(item["close_time"]),
                open=str(item["open"]),
                high=str(item["high"]),
                low=str(item["low"]),
                close=str(item["close"]),
                volume=str(item["volume"]),
                quote_volume=str(item.get("quote_volume") or ""),
                trades=item.get("trades"),
                taker_buy_base_volume=str(item.get("taker_buy_base_volume") or ""),
                taker_buy_quote_volume=str(item.get("taker_buy_quote_volume") or ""),
                closed=True,
                exchange=exchange,
                market_type=market,
                volume_unit=str(item.get("volume_unit") or "base_asset"),
                contract_volume=item.get("contract_volume"),
            )
            for item in selected_rows
        ]
        core_bars = to_core_bars(candles)
        from pa_agent.data.snapshot import build_analysis_frame

        frame = build_analysis_frame(
            core_bars,
            analysis_bars,
            symbol,
            timeframe,
            now_ms=decision_time_ms,
        )
        if frame is None:
            raise InsufficientMarketDataError("归档 K 线不足以构建历史 PA 分析帧")
        frame = replace(frame, snapshot_ts_local_ms=decision_time_ms)

        derivative_provenance: dict[str, Any] = {
            "observation_count": 0,
            "historical_count": 0,
            "snapshot_count": 0,
            "metrics_used": [],
            "missing_metrics": [],
            "future_observations_excluded": True,
        }
        if market == "usdm":
            period = context_period(exchange, timeframe)
            asof = await asyncio.to_thread(
                self._history_store.list_derivatives_observations_asof,
                market="usdm",
                exchange=exchange,
                symbol=symbol,
                decision_time_ms=decision_time_ms,
                period=period,
                include_snapshots=True,
            )
            context, derivative_provenance = build_historical_derivatives_context(
                asof["items"],
                exchange=exchange,
                symbol=symbol,
                period=period,
                decision_time_ms=decision_time_ms,
            )
            derivative_provenance["excluded_or_unavailable_count"] = asof[
                "excluded_or_unavailable_count"
            ]
            derivative_provenance["latest_available_at_ms"] = asof[
                "latest_available_at_ms"
            ]
            trace.emit(
                "historical_derivatives_reconstructed",
                stage="market",
                title="历史衍生品上下文已按可用时间重建",
                detail=(
                    f"可用观测 {asof['eligible_count']} 条；"
                    f"使用指标 {len(derivative_provenance['metrics_used'])} 项"
                ),
                data=derivative_provenance,
            )
        else:
            context = CryptoMarketContext(
                market_type=f"spot:{exchange}",
                exchange=exchange,
                symbol=symbol,
                data_timestamp=decision_time_ms,
                data_quality={
                    "derivatives": "not_available_for_spot_replay",
                    "historical_replay": "asof_candles_only",
                },
            )

        trace.emit(
            "historical_frame_ready",
            stage="market",
            title="历史 K 线分析帧已构建",
            detail=(
                f"{len(selected_rows)} 根连续已收盘 K 线；"
                f"最新收盘时间 {selected_rows[-1]['close_time']}"
            ),
            data={
                "first_open_time_ms": selected_rows[0]["open_time"],
                "latest_open_time_ms": selected_rows[-1]["open_time"],
                "latest_close_time_ms": selected_rows[-1]["close_time"],
                "decision_time_ms": decision_time_ms,
                "future_candles_included": False,
            },
        )

        run = await asyncio.to_thread(
            self._run_sync, frame, context, provider_config, trace
        )
        run.details["provenance"] = {
            "analysis_mode": "historical_replay",
            "decision_time_ms": decision_time_ms,
            "candle_source": "sqlite:market_candles",
            "candle_count": len(selected_rows),
            "first_candle_open_time_ms": selected_rows[0]["open_time"],
            "latest_candle_open_time_ms": selected_rows[-1]["open_time"],
            "latest_candle_close_time_ms": selected_rows[-1]["close_time"],
            "derivatives": derivative_provenance,
            "live_exchange_requests": False,
            "future_candles_excluded": True,
            "future_labels_excluded": True,
            "experience_learning_disabled": True,
        }
        trace.emit(
            "historical_replay_completed",
            stage="system",
            title="历史回放分析完成",
            detail="已使用当前 PA 两阶段流程完成指定历史时点分析；未执行任何交易操作",
            data={
                "decision_time_ms": decision_time_ms,
                "analysis_status": "failed" if run.record.exception else "completed",
                "model_usage": run.record.usage_total,
            },
        )
        run.trace = trace.snapshot()
        return run

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
        prompt_dir = Path(PROMPT_DIR)
        assembler = CryptoPromptAssembler(
            prompt_dir=prompt_dir,
            experience_reader=experience_reader,
            prompt_settings=settings.prompt,
            crypto_context_text=render_crypto_context(context),
        )
        loaded_prompt_names: set[str] = set()
        original_load = assembler._load

        def tracked_load(filename: str) -> str:
            loaded_prompt_names.add(filename)
            return original_load(filename)

        # Instrument only the per-run assembler instance. The original PA prompt
        # construction and caching behavior remain unchanged.
        assembler._load = tracked_load
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
        trace.emit(
            "preflight_started",
            stage="preflight",
            title="开始数据预检",
            detail="执行原版 check_preflight_data；通过后才进入阶段一",
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
        stage1 = record.stage1_diagnosis or {}
        details["chart_findings"] = {
            "support_levels": list(stage1.get("support_levels") or []),
            "resistance_levels": list(stage1.get("resistance_levels") or []),
            "bar_by_bar_summary": list(stage1.get("bar_by_bar_summary") or []),
            "gate_trace": list(stage1.get("gate_trace") or []),
        }
        from pa_agent.ai import prompt_assembler as prompt_module
        prompt_files = set(loaded_prompt_names)
        # The shared system prompt is process-cached; include its declared source
        # files and the fixed Stage 1 / Stage 2 task files even on cache hits.
        for constant_name in (
            "COMMON_SYSTEM_STAGE1_TXT_FILES",
            "STAGE1_TASK_PROMPT_TXT_FILES",
        ):
            prompt_files.update(getattr(prompt_module, constant_name, ()) or ())
        # Stage 2 task files are only part of this run if the orchestrator
        # actually built Stage 2 messages (a wait/unknown gate can short-circuit it).
        if getattr(record, "stage2_messages", None):
            prompt_files.update(
                getattr(prompt_module, "STAGE2_BASE_PROMPT_TXT_FILES", ()) or ()
            )
        prompt_files.update(record.strategy_files_used or [])
        project_root = prompt_dir.parent
        def file_digest(path: Path) -> dict[str, Any]:
            try:
                raw = path.read_bytes()
            except OSError:
                return {"path": str(path.relative_to(project_root)) if path.is_relative_to(project_root) else path.name, "available": False}
            return {
                "path": str(path.relative_to(project_root)) if path.is_relative_to(project_root) else path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
                "available": True,
            }
        prompt_provenance = []
        for filename in sorted(prompt_files):
            candidate = (prompt_dir / filename).resolve()
            if prompt_dir.resolve() not in candidate.parents:
                continue
            entry = file_digest(candidate)
            entry["kind"] = "strategy" if filename in (record.strategy_files_used or []) else "prompt"
            prompt_provenance.append(entry)
        engine_paths = [
            project_root / "pa_agent/ai/prompt_assembler.py",
            project_root / "pa_agent/ai/router.py",
            project_root / "pa_agent/ai/prompts/schemas.py",
            project_root / "pa_agent/ai/json_validator.py",
            project_root / "pa_agent/orchestrator/two_stage.py",
        ]
        details["provenance"] = {
            "prompt_files": prompt_provenance,
            "engine_files": [file_digest(path) for path in engine_paths],
            "strategy_files_used": list(record.strategy_files_used or []),
            "provider_id": provider_config.get("provider_id"),
            "model": provider_config.get("model"),
            "feature_schema_version": "pa-segment-features-v1",
            "label_schema_version": "forward-outcome-v1",
        }
        trace.emit(
            "prompt_provenance_captured",
            stage="history",
            title="本次 Prompt 与策略版本已记录",
            detail=f"{len(prompt_provenance)} 个实际加载/路由文件，{len(engine_paths)} 个核心引擎文件",
            data={
                "prompt_files": prompt_provenance,
                "engine_files": details["provenance"]["engine_files"],
            },
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
