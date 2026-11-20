from __future__ import annotations

import asyncio

from app.adapters.binance.futures_rest import BinanceUSDMREST
from app.adapters.gate.futures_rest import GateFuturesREST


class FakeClient:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params or {}))
        return []


def test_binance_historical_derivatives_adapters_use_inclusive_time_ranges():
    client = FakeClient()
    api = BinanceUSDMREST(client)

    asyncio.run(
        api.funding_rate_history_range(
            "BTCUSDT", start_ms=1000, end_ms=9000, limit=5000
        )
    )
    asyncio.run(
        api.open_interest_history_range(
            "BTCUSDT", "15m", start_ms=2000, end_ms=8000, limit=900
        )
    )
    asyncio.run(
        api.top_long_short_position_ratio_range(
            "BTCUSDT", "1h", start_ms=3000, end_ms=7000, limit=900
        )
    )

    assert client.calls == [
        (
            "/fapi/v1/fundingRate",
            {"symbol": "BTCUSDT", "startTime": 1000, "endTime": 9000, "limit": 1000},
        ),
        (
            "/futures/data/openInterestHist",
            {
                "symbol": "BTCUSDT",
                "period": "15m",
                "startTime": 2000,
                "endTime": 8000,
                "limit": 500,
            },
        ),
        (
            "/futures/data/topLongShortPositionRatio",
            {
                "symbol": "BTCUSDT",
                "period": "1h",
                "startTime": 3000,
                "endTime": 7000,
                "limit": 500,
            },
        ),
    ]


def test_gate_historical_derivatives_adapters_use_supported_range_parameters():
    client = FakeClient()
    api = GateFuturesREST(client)

    asyncio.run(
        api.funding_rate_history_range(
            "NEARUSDT", from_s=100, to_s=500, limit=1000
        )
    )
    asyncio.run(
        api.contract_stats_range(
            "NEARUSDT", "15m", from_s=200, limit=500
        )
    )

    assert client.calls == [
        (
            "/futures/usdt/funding_rate",
            {"contract": "NEAR_USDT", "from": 100, "to": 500, "limit": 100},
        ),
        (
            "/futures/usdt/contract_stats",
            {"contract": "NEAR_USDT", "from": 200, "interval": "15m", "limit": 200},
        ),
    ]
