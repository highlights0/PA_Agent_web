from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.analysis_history_store import AnalysisHistoryStore
from app.services.analysis_service import AnalysisRun, AnalysisService, InsufficientMarketDataError
from app.services.historical_replay import (
    build_historical_derivatives_context,
    select_replay_decision_times,
)


@dataclass
class ReplayCandle:
    open_time: int
    close_time: int
    open: str
    high: str
    low: str
    close: str
    volume: str = "10"
    quote_volume: str = "1000"
    trades: int = 10
    taker_buy_base_volume: str = "5"
    taker_buy_quote_volume: str = "500"
    closed: bool = True
    volume_unit: str = "base_asset"
    contract_volume: str | None = None


class NoLiveMarketService:
    def __init__(self):
        self.calls = []

    async def klines(self, *args, **kwargs):
        self.calls.append("klines")
        raise AssertionError("Historical replay must not request live klines")

    async def futures_context(self, *args, **kwargs):
        self.calls.append("futures_context")
        raise AssertionError("Historical replay must not request live derivatives")


BASE_TIME_MS = 1_700_000_000_000


def _candle_rows(count: int, *, missing: set[int] | None = None):
    missing = missing or set()
    rows = []
    for index in range(count):
        if index in missing:
            continue
        price = 100 + index * 0.05
        rows.append(
            ReplayCandle(
                open_time=BASE_TIME_MS + index * 60_000,
                close_time=BASE_TIME_MS + (index + 1) * 60_000 - 1,
                open=str(price),
                high=str(price + 0.2),
                low=str(price - 0.2),
                close=str(price + 0.1),
            )
        )
    return rows


def test_derivatives_asof_query_requires_event_and_availability_before_cutoff(tmp_path: Path):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    common = {
        "market": "usdm",
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "metric": "funding_rate",
        "period": "",
        "raw": {},
        "source_endpoint": "/fapi/v1/fundingRate",
        "quality": "ok",
    }
    store.upsert_derivatives_observations([
        {
            **common,
            "event_time_ms": 1_000,
            "fetched_at_ms": 9_000,
            "available_at_ms": 2_000,
            "data_kind": "historical",
            "value": {"funding_rate": "0.0001"},
        },
        {
            **common,
            "event_time_ms": 2_400,
            "fetched_at_ms": 2_600,
            "available_at_ms": 2_600,
            "data_kind": "snapshot",
            "value": {"funding_rate": "0.0002"},
        },
        {
            **common,
            "event_time_ms": 2_600,
            "fetched_at_ms": 2_700,
            "available_at_ms": 2_700,
            "data_kind": "historical",
            "value": {"funding_rate": "0.0003"},
        },
        {
            **common,
            "event_time_ms": 900,
            "fetched_at_ms": 9_000,
            "available_at_ms": 0,
            "data_kind": "historical",
            "value": {"funding_rate": "0.0004"},
        },
    ])

    result = store.list_derivatives_observations_asof(
        market="usdm",
        exchange="binance",
        symbol="BTCUSDT",
        decision_time_ms=2_500,
        period="15m",
    )

    assert result["eligible_count"] == 1
    assert result["items"][0]["value"]["funding_rate"] == "0.0001"
    # Historical endpoint fetch time is ingestion provenance, not the original
    # publication time; the conservative available_at boundary controls replay.
    assert result["items"][0]["fetched_at_ms"] > 2_500
    assert result["items"][0]["available_at_ms"] <= 2_500
    assert result["excluded_or_unavailable_count"] == 2


def test_historical_context_uses_only_asof_observations_and_reports_missing_metrics():
    observations = [
        {
            "metric": "funding_rate",
            "period": "",
            "event_time_ms": 1_000,
            "available_at_ms": 1_000,
            "data_kind": "historical",
            "quality": "ok",
            "value": {"funding_rate": "0.0001", "mark_price": "100"},
        },
        {
            "metric": "open_interest",
            "period": "15m",
            "event_time_ms": 1_000,
            "available_at_ms": 901_000,
            "data_kind": "historical",
            "quality": "ok",
            "value": {"sum_open_interest": "200", "sum_open_interest_value": "20000"},
        },
        {
            "metric": "global_long_short_ratio",
            "period": "15m",
            "event_time_ms": 901_000,
            "available_at_ms": 1_801_000,
            "data_kind": "historical",
            "quality": "ok",
            "value": {"long_short_ratio": "1.3"},
        },
    ]

    context, provenance = build_historical_derivatives_context(
        observations,
        exchange="binance",
        symbol="BTCUSDT",
        period="15m",
        decision_time_ms=1_000_000,
    )

    assert context.funding_rate == "0.0001"
    assert context.open_interest == "200"
    assert context.global_long_short_ratio is None
    assert context.data_quality["global_long_short_ratio"].startswith("missing")
    assert provenance["future_observations_excluded"] is True
    assert provenance["metrics_used"] == ["funding_rate", "open_interest"]


def test_replay_time_selector_uses_only_contiguous_closed_candle_boundaries():
    rows = [
        {
            "open_time": index * 60_000,
            "close_time": (index + 1) * 60_000 - 1,
        }
        for index in range(120)
    ]
    rows.pop(80)

    selected, candidate_count = select_replay_decision_times(
        rows,
        timeframe="1m",
        start_ms=60 * 60_000,
        end_ms=119 * 60_000,
        required_bars=60,
        points=3,
    )

    assert candidate_count > 0
    assert len(selected) == 3
    assert selected == sorted(set(selected))
    assert all(value <= 120 * 60_000 for value in selected)


def test_replay_selector_rejects_unsupported_timeframe():
    with pytest.raises(ValueError, match="does not support"):
        select_replay_decision_times(
            [],
            timeframe="1M",
            start_ms=0,
            end_ms=1,
            required_bars=70,
            points=1,
        )


def test_analysis_service_replays_archived_candles_without_live_market_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    candles = _candle_rows(72)
    store.upsert_market_candles(
        market="spot",
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="1m",
        candles=candles,
    )
    live_market = NoLiveMarketService()
    settings = SimpleNamespace(max_klines=300)
    service = AnalysisService(live_market, settings, history_store=store)
    monkeypatch.setattr(
        service,
        "_active_provider",
        lambda: {
            "name": "test",
            "provider_id": "test",
            "model": "test-model",
            "base_url": "https://example.invalid",
            "api_key": "test-key",
            "thinking": False,
            "reasoning_effort": "low",
            "context_window": 10000,
        },
    )
    captured = {}

    def fake_run_sync(frame, context, provider_config, trace):
        captured["frame"] = frame
        captured["context"] = context
        return AnalysisRun(
            record=SimpleNamespace(
                exception=None,
                usage_total={"total_tokens": 10},
                meta=SimpleNamespace(bar_count=len(frame.bars)),
            ),
            events=[],
            trace=[],
            details={},
        )

    monkeypatch.setattr(service, "_run_sync", fake_run_sync)
    decision_time_ms = BASE_TIME_MS + 70 * 60_000

    run = asyncio.run(
        service.analyze_historical(
            "spot",
            "BTCUSDT",
            "1m",
            20,
            "binance",
            decision_time_ms,
        )
    )

    assert live_market.calls == []
    assert len(captured["frame"].bars) == 20
    assert captured["frame"].snapshot_ts_local_ms == decision_time_ms
    assert captured["frame"].bars[0].ts_open == BASE_TIME_MS + 69 * 60_000
    assert run.details["provenance"]["live_exchange_requests"] is False
    assert run.details["provenance"]["future_candles_excluded"] is True
    assert run.details["provenance"]["latest_candle_close_time_ms"] < decision_time_ms


def test_analysis_service_rejects_gapped_historical_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    store.upsert_market_candles(
        market="spot",
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="1m",
        candles=_candle_rows(72, missing={65}),
    )
    service = AnalysisService(NoLiveMarketService(), SimpleNamespace(max_klines=300), history_store=store)
    monkeypatch.setattr(service, "_active_provider", lambda: {"model": "unused"})

    with pytest.raises(InsufficientMarketDataError, match="时间缺口"):
        asyncio.run(
            service.analyze_historical(
                "spot",
                "BTCUSDT",
                "1m",
                20,
                "binance",
                BASE_TIME_MS + 71 * 60_000,
            )
        )
