from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.market_service import MarketService


class GateStub:
    async def exchange_info(self):
        return [
            {"name": "BTC_USDT", "status": "trading", "in_delisting": False},
            {"name": "ETH_USDT", "status": "trading", "in_delisting": False},
            {"name": "OLD_USDT", "status": "delisted", "in_delisting": True},
        ]

    async def contract_info(self, symbol):
        return {"name": "BTC_USDT", "quanto_multiplier": "0.001"}

    async def klines(self, symbol, interval, limit):
        return [
            {"t": 1_700_000_000, "v": "10", "o": "100", "h": "102", "l": "99", "c": "101", "sum": "1010"}
        ]

    async def ticker_24h(self, symbol):
        return {
            "contract": "BTC_USDT",
            "last": "101",
            "change_price": "1",
            "change_percentage": "1.0",
            "high_24h": "102",
            "low_24h": "99",
            "volume_24h_base": "10",
            "volume_24h_quote": "1010",
        }


def test_gate_market_list_klines_and_ticker_are_normalized():
    async def run():
        gate = GateStub()
        registry = SimpleNamespace(
            supported={"gate": ["usdm"]},
            get=lambda exchange, market: gate if (exchange, market) == ("gate", "usdm") else None,
        )
        service = MarketService(registry, None, gate)

        markets = await service.markets("usdm", "gate")
        assert [item.symbol for item in markets] == ["BTC_USDT", "ETH_USDT"]
        assert markets[0].exchange == "gate"
        assert markets[0].base_asset == "BTC"

        bars = await service.klines("usdm", "BTCUSDT", "15m", 100, "gate")
        assert bars[0].exchange == "gate"
        assert bars[0].volume == "0.010"
        assert bars[0].contract_volume == "10"
        assert bars[0].quote_volume == "1010"

        ticker = await service.ticker("usdm", "BTCUSDT", "gate")
        assert ticker.exchange == "gate"
        assert ticker.last_price == "101"
        assert ticker.price_change_percent == "1.0"
        assert ticker.quote_volume == "1010"

    asyncio.run(run())
