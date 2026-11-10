from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.market_service import MarketService


class GateStub:
    @staticmethod
    def contract_name(symbol):
        return "BTC_USDT"

    async def contract_info(self, symbol):
        return {
            "name": "BTC_USDT",
            "quanto_multiplier": "0.001",
            "funding_interval": 28800,
            "funding_next_apply": 1_700_001_000,
            "funding_rate": "0.0001",
            "index_price": "60000",
        }

    async def ticker_24h(self, symbol):
        return {
            "contract": "BTC_USDT",
            "last": "60010",
            "mark_price": "60005",
            "index_price": "60000",
            "funding_rate": "0.0001",
        }

    async def contract_stats(self, symbol, interval, limit):
        return [
            {
                "time": 1_700_000_000,
                "open_interest": "1000",
                "open_interest_usd": "1000000",
                "lsr_account": 1.1,
                "lsr_taker": 1.2,
                "top_lsr_account": 1.3,
                "top_lsr_size": 1.4,
                "long_liq_size": "2",
                "short_liq_size": "3",
                "long_liq_usd_new": 100,
                "short_liq_usd_new": 50,
            },
            {
                "time": 1_700_000_300,
                "open_interest": "1100",
                "open_interest_usd": "1100000",
                "lsr_account": 1.15,
                "lsr_taker": 1.25,
                "top_lsr_account": 1.35,
                "top_lsr_size": 1.45,
                "long_liq_size": "4",
                "short_liq_size": "5",
                "long_liq_usd_new": 200,
                "short_liq_usd_new": 75,
            },
        ]

    async def funding_rate_history(self, symbol, limit):
        return [{"t": 1_700_000_000, "r": "0.00009"}]


def test_gate_context_contains_oi_ratios_funding_and_three_hour_liquidations():
    async def run():
        service = MarketService(
            providers=SimpleNamespace(),
            binance_futures=None,
            gate_futures=GateStub(),
        )
        context = await service.futures_context("BTCUSDT", "5m", "gate")

        assert context.exchange == "gate"
        assert context.contract == "BTC_USDT"
        assert context.funding_rate == "0.0001"
        assert context.funding_interval_seconds == 28800
        assert context.open_interest == "1100"
        assert context.open_interest_usd == "1100000"
        assert context.open_interest_change_pct == pytest.approx(10.0)
        assert context.global_long_short_ratio == "1.15"
        assert context.taker_long_short_ratio == "1.25"
        assert context.top_trader_long_short_account_ratio == "1.35"
        assert context.top_trader_long_short_position_ratio == "1.45"
        assert context.long_liquidation_usd_3h == "300"
        assert context.short_liquidation_usd_3h == "125"
        assert context.long_liquidation_contracts_3h == "6"
        assert context.short_liquidation_contracts_3h == "8"
        assert context.stats_window_minutes == 5
        assert len(context.stats_history) == 2
        assert context.data_quality["contract_stats"] == "ok:2_records"

    asyncio.run(run())


def test_gate_oi_change_does_not_mix_usd_and_contract_units():
    class InconsistentGateStub(GateStub):
        async def contract_stats(self, symbol, interval, limit):
            return [
                {"time": 1_700_000_000, "open_interest": "1000", "open_interest_usd": "1000000"},
                {"time": 1_700_000_300, "open_interest": "1100"},
            ]

    async def run():
        service = MarketService(
            providers=SimpleNamespace(),
            binance_futures=None,
            gate_futures=InconsistentGateStub(),
        )
        context = await service.futures_context("BTCUSDT", "5m", "gate")
        assert context.open_interest_change_pct == pytest.approx(10.0)
        assert context.data_quality["open_interest_change_basis"] == "contracts"

    asyncio.run(run())
