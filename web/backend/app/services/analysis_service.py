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

    def __init__(self, market_service: Any, web_settings: WebSettings) -> None:
        self._market = market_service
        self._web_settings = web_settings

    async def analyze(
        self,
        market: MarketType,
        symbol: str,
        timeframe: str,
        analysis_bars: int = 100,
        exchange: str = "binance",
    ) -> AnalysisRun:
        if not self._web_settings.ai_api_key.strip():
            raise AnalysisConfigurationError("AI_API_KEY is not configured on the server")
        if analysis_bars < 20 or analysis_bars + 50 > self._web_settings.max_klines:
            raise ValueError("analysis_bars must be between 20 and server max bars minus 50")
        if market == "usdm" and exchange not in {"binance", "gate"}:
            raise ValueError(
                f"{exchange} USDT perpetual futures are not configured; no cross-exchange substitution is allowed"
            )
        if market == "spot" and exchange == "gate":
            raise ValueError("Gate provider currently supports USDT perpetual futures only")

        rows = await self._market.klines(
            market, symbol, timeframe, analysis_bars + 50, exchange
        )
        core_bars = to_core_bars(rows)
        from pa_agent.data.snapshot import build_analysis_frame

        frame = build_analysis_frame(
            core_bars,
            analysis_bars,
            symbol,
            timeframe,
        )
        if frame is None:
            raise InsufficientMarketDataError(
                f"Not enough closed candles to build {analysis_bars}-bar analysis frame"
            )

        if market == "usdm":
            context_period = "5m" if exchange == "gate" else _context_period(timeframe)
            derivatives = await self._market.futures_context(
                symbol,
                context_period,
                exchange,
            )
            context = CryptoMarketContext(
                market_type=market,
                **derivatives.model_dump(),
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

        return await asyncio.to_thread(self._run_sync, frame, context)

    def _run_sync(self, frame: Any, context: CryptoMarketContext) -> AnalysisRun:
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
                model=self._web_settings.ai_model,
                base_url=self._web_settings.ai_base_url,
                api_key=self._web_settings.ai_api_key,
                thinking=self._web_settings.ai_thinking,
                reasoning_effort=self._web_settings.ai_reasoning_effort,
            )
        )
        settings.general.decision_stance = "conservative"
        settings.prompt.experience_max_entries = 0

        experience_reader = ExperienceReader(experience_dir=Path(EXPERIENCE_DIR))
        assembler = CryptoPromptAssembler(
            prompt_dir=Path(PROMPT_DIR),
            experience_reader=experience_reader,
            prompt_settings=settings.prompt,
            crypto_context_text=render_crypto_context(context),
        )
        client = DeepSeekClient(settings.provider, logger_=logger)
        validator = JsonValidator(settings)
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
        record = orchestrator.submit(
            frame=frame,
            cancel_token=CancelToken(),
            on_event=lambda event: events.append(getattr(event, "name", str(event))),
        )
        return AnalysisRun(record=record, events=events)
