from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.historical_backfill import backfill_market_candles


@dataclass(frozen=True)
class Candle:
    open_time: int
    close_time: int
    closed: bool = True


class FakeMarketService:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = sorted(candles, key=lambda item: item.open_time)
        self.range_calls: list[dict[str, int]] = []

    def _provider(self, market: str, exchange: str):
        supported = {
            ("spot", "binance"),
            ("spot", "binance_us"),
            ("spot", "coinbase"),
            ("usdm", "binance"),
            ("usdm", "gate"),
        }
        if (market, exchange) not in supported:
            raise ValueError(f"{exchange} does not provide {market}")
        return object()

    async def klines(self, market, symbol, timeframe, limit, exchange):
        return self.candles[-limit:]

    async def klines_range(
        self, market, symbol, timeframe, *, start_ms, end_ms, limit, exchange
    ):
        self.range_calls.append(
            {"start_ms": start_ms, "end_ms": end_ms, "limit": limit}
        )
        return [
            item
            for item in self.candles
            if item.closed and start_ms <= item.open_time <= end_ms
        ][:limit]


def _candles(count: int, *, missing: set[int] | None = None) -> list[Candle]:
    missing = missing or set()
    step_ms = 60_000
    return [
        Candle(index * step_ms, (index + 1) * step_ms - 1)
        for index in range(count)
        if index not in missing
    ]


def test_coinbase_backfill_paginates_at_300_candles_per_request():
    service = FakeMarketService(_candles(450))

    result = asyncio.run(
        backfill_market_candles(
            service,
            market="spot",
            exchange="coinbase",
            symbol="BTC-USD",
            timeframe="1m",
            requested_bars=350,
        )
    )

    assert result["closed_bars"] == 350
    assert result["pages"] == 2
    assert result["gaps"] == 0
    assert [item.open_time for item in result["bars"]] == [
        index * 60_000 for index in range(100, 450)
    ]
    assert [call["limit"] for call in service.range_calls] == [300, 50]


def test_binance_backfill_paginates_at_1000_candles_per_request():
    service = FakeMarketService(_candles(1200))

    result = asyncio.run(
        backfill_market_candles(
            service,
            market="spot",
            exchange="binance",
            symbol="BTCUSDT",
            timeframe="1m",
            requested_bars=1100,
        )
    )

    assert result["closed_bars"] == 1100
    assert result["pages"] == 2
    assert result["gaps"] == 0
    assert [call["limit"] for call in service.range_calls] == [1000, 100]


def test_backfill_reports_missing_candles_instead_of_bridging_them():
    service = FakeMarketService(_candles(450, missing={300}))

    result = asyncio.run(
        backfill_market_candles(
            service,
            market="spot",
            exchange="coinbase",
            symbol="BTC-USD",
            timeframe="1m",
            requested_bars=350,
        )
    )

    assert result["closed_bars"] == 350
    assert result["gaps"] == 1
    assert 300 * 60_000 not in {item.open_time for item in result["bars"]}


def test_backfill_uses_last_closed_candle_and_ignores_open_candle():
    candles = _candles(100)
    candles[-1] = Candle(candles[-1].open_time, candles[-1].close_time, closed=False)
    service = FakeMarketService(candles)

    result = asyncio.run(
        backfill_market_candles(
            service,
            market="spot",
            exchange="binance",
            symbol="BTCUSDT",
            timeframe="1m",
            requested_bars=80,
        )
    )

    assert result["closed_bars"] == 80
    assert result["bars"][-1].open_time == 98 * 60_000
    assert all(item.closed for item in result["bars"])


def test_backfill_rejects_unsupported_market_exchange_pair():
    service = FakeMarketService(_candles(200))

    with pytest.raises(ValueError, match="does not provide"):
        asyncio.run(
            backfill_market_candles(
                service,
                market="spot",
                exchange="gate",
                symbol="BTCUSDT",
                timeframe="1m",
                requested_bars=80,
            )
        )
