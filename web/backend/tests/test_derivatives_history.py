from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.services.analysis_history_store import AnalysisHistoryStore
from app.services.derivatives_history import collect_derivatives_history


class FakeBinanceFutures:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def funding_rate_history_range(self, symbol, *, start_ms, end_ms, limit):
        self.calls.append("funding")
        ts = max(start_ms, end_ms - 1000)
        return [{"symbol": symbol, "fundingTime": ts, "fundingRate": "0.0001", "markPrice": "100"}] if ts <= end_ms else []

    async def open_interest_history_range(self, symbol, period, *, start_ms, end_ms, limit):
        self.calls.append("oi")
        ts = max(start_ms, end_ms - 2000)
        return [{"symbol": symbol, "timestamp": ts, "sumOpenInterest": "120", "sumOpenInterestValue": "12000"}] if ts <= end_ms else []

    async def global_long_short_ratio_range(self, symbol, period, *, start_ms, end_ms, limit):
        self.calls.append("global")
        ts = max(start_ms, end_ms - 3000)
        return [{"symbol": symbol, "timestamp": ts, "longShortRatio": "1.2", "longAccount": "0.55", "shortAccount": "0.45"}] if ts <= end_ms else []

    async def taker_long_short_ratio_range(self, symbol, period, *, start_ms, end_ms, limit):
        self.calls.append("taker")
        ts = max(start_ms, end_ms - 4000)
        return [{"symbol": symbol, "timestamp": ts, "buySellRatio": "1.1", "buyVol": "10", "sellVol": "9"}] if ts <= end_ms else []

    async def top_long_short_account_ratio_range(self, symbol, period, *, start_ms, end_ms, limit):
        self.calls.append("top_account")
        ts = max(start_ms, end_ms - 5000)
        return [{"symbol": symbol, "timestamp": ts, "longShortRatio": "1.3", "longAccount": "0.56", "shortAccount": "0.44"}] if ts <= end_ms else []

    async def top_long_short_position_ratio_range(self, symbol, period, *, start_ms, end_ms, limit):
        self.calls.append("top_position")
        ts = max(start_ms, end_ms - 6000)
        return [{"symbol": symbol, "timestamp": ts, "longShortRatio": "1.4", "longAccount": "0.58", "shortAccount": "0.42"}] if ts <= end_ms else []


class FakeGateFutures:
    def __init__(self) -> None:
        self.funding_calls = 0
        self.stats_calls = 0

    async def funding_rate_history_range(self, symbol, *, from_s, to_s, limit):
        self.funding_calls += 1
        rows = [
            {"t": 1000, "r": "0.0002"},
            {"t": 2000, "r": "-0.0001"},
        ]
        return [row for row in rows if from_s <= row["t"] <= to_s][:limit]

    async def contract_stats_range(self, symbol, interval, *, from_s, limit):
        self.stats_calls += 1
        rows = [
            {
                "time": 1000,
                "open_interest": "100",
                "open_interest_usd": 10000,
                "lsr_account": 1.2,
                "lsr_taker": 0.9,
                "top_lsr_account": 1.4,
                "top_lsr_size": 1.5,
                "long_liq_size": "2",
                "short_liq_size": "3",
                "long_liq_usd_new": 200,
                "short_liq_usd_new": 300,
                "mark_price": 100,
            },
            {
                "time": 1300,
                "open_interest": "110",
                "open_interest_usd": 11000,
                "lsr_account": 1.3,
                "lsr_taker": 1.0,
                "top_lsr_account": 1.5,
                "top_lsr_size": 1.6,
                "long_liq_size": "4",
                "short_liq_size": "5",
                "long_liq_usd_new": 400,
                "short_liq_usd_new": 500,
                "mark_price": 101,
            },
        ]
        return [row for row in rows if row["time"] >= from_s][:limit]


class FakeMarketService:
    def __init__(self, binance=None, gate=None):
        self.binance = binance or FakeBinanceFutures()
        self.gate = gate or FakeGateFutures()

    def _provider(self, market, exchange):
        assert market == "usdm"
        return self.binance if exchange == "binance" else self.gate


def test_binance_historical_derivatives_are_normalized_with_source_and_timestamps():
    now = int(time.time() * 1000)
    service = FakeMarketService()

    result = asyncio.run(
        collect_derivatives_history(
            service,
            exchange="binance",
            symbol="BTCUSDT",
            period="15m",
            start_ms=now - 60_000,
            end_ms=now,
        )
    )

    assert result["status"] == "completed"
    assert result["observation_count"] == 6
    assert result["metrics"]["funding_rate"]["records"] == 1
    assert result["metrics"]["open_interest"]["records"] == 1
    funding = next(item for item in result["observations"] if item["metric"] == "funding_rate")
    assert funding["data_kind"] == "historical"
    assert funding["source_endpoint"] == "/fapi/v1/fundingRate"
    assert funding["value"]["funding_rate"] == "0.0001"
    assert funding["event_time_ms"] <= result["end_time_ms"]
    assert funding["fetched_at_ms"] >= funding["event_time_ms"]


def test_binance_one_month_limit_is_reported_without_fabricating_older_oi():
    now = int(time.time() * 1000)
    start = now - 45 * 24 * 60 * 60 * 1000
    service = FakeMarketService()

    result = asyncio.run(
        collect_derivatives_history(
            service,
            exchange="binance",
            symbol="BTCUSDT",
            period="1h",
            start_ms=start,
            end_ms=now,
        )
    )

    assert result["status"] == "partial"
    assert result["metrics"]["funding_rate"]["status"] == "ok"
    assert result["metrics"]["open_interest"]["status"] == "window_limited"
    assert result["metrics"]["open_interest"]["effective_start_ms"] > start


def test_gate_historical_stats_are_fetched_once_and_fanned_out_to_metrics():
    service = FakeMarketService()
    result = asyncio.run(
        collect_derivatives_history(
            service,
            exchange="gate",
            symbol="NEARUSDT",
            period="5m",
            start_ms=900_000,
            end_ms=2_500_000,
        )
    )

    assert result["status"] == "completed"
    assert service.gate.funding_calls == 1
    assert service.gate.stats_calls == 1
    assert result["metrics"]["open_interest_contracts"]["records"] == 2
    assert result["metrics"]["long_liquidation_usd"]["records"] == 2
    assert result["metrics"]["taker_long_short_ratio"]["records"] == 2
    assert all(item["data_kind"] == "historical" for item in result["observations"])
    assert all(item["event_time_ms"] <= result["end_time_ms"] for item in result["observations"])


def test_derivatives_archive_keeps_historical_and_snapshot_data_separate(tmp_path: Path):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    historical = {
        "market": "usdm",
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "metric": "funding_rate",
        "period": "",
        "event_time_ms": 1_000,
        "fetched_at_ms": 2_000,
        "data_kind": "historical",
        "value": {"funding_rate": "0.0001"},
        "raw": {"fundingRate": "0.0001"},
        "source_endpoint": "/fapi/v1/fundingRate",
        "quality": "ok",
    }
    assert store.upsert_derivatives_observations([historical]) == 1
    assert store.upsert_derivatives_observations([historical]) == 1

    store.save_derivatives_snapshot(
        market="usdm",
        exchange="binance",
        symbol="BTCUSDT",
        context={
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "funding_rate": "0.0002",
            "open_interest": "123",
            "data_timestamp": 900,
            "data_quality": {"funding_rate": "ok"},
        },
        source_endpoint="/api/v1/market/usdm/BTCUSDT/context",
    )

    rows = store.list_derivatives_observations(
        market="usdm",
        exchange="binance",
        symbol="BTCUSDT",
        limit=20,
    )
    assert {item["data_kind"] for item in rows} == {"historical", "snapshot"}
    snapshot = next(item for item in rows if item["data_kind"] == "snapshot")
    assert snapshot["event_time_ms"] == 900
    assert snapshot["available_at_ms"] == snapshot["fetched_at_ms"]
    assert snapshot["fetched_at_ms"] > snapshot["event_time_ms"]
    assert snapshot["raw"]["data_timestamp"] == 900
    assert snapshot["source_endpoint"].endswith("/context")

    stats = store.derivatives_archive_stats(
        market="usdm", exchange="binance", symbol="BTCUSDT"
    )
    assert {item["data_kind"] for item in stats} == {"historical", "snapshot"}
