from __future__ import annotations

import asyncio

import pytest

from app.adapters.gate.futures_rest import GateFuturesREST


class FakeClient:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "/futures/usdt/contracts":
            return [{"name": "BTC_USDT", "status": "trading"}]
        if path == "/futures/usdt/contracts/BTC_USDT":
            return {"name": "BTC_USDT", "quanto_multiplier": "0.001"}
        if path == "/futures/usdt/candlesticks":
            return [{"t": 100, "v": "10", "o": "1", "h": "2", "l": "1", "c": "2", "sum": "20"}]
        if path == "/futures/usdt/tickers":
            return [{"contract": "BTC_USDT", "last": "2"}]
        if path == "/futures/usdt/funding_rate":
            return [{"t": 100, "r": "0.0001"}]
        if path == "/futures/usdt/contract_stats":
            return [{"time": 100, "open_interest": "10"}]
        raise AssertionError(f"Unexpected endpoint: {path}")


def test_gate_symbol_mapping_accepts_compact_and_exchange_symbols():
    assert GateFuturesREST.contract_name("BTCUSDT") == "BTC_USDT"
    assert GateFuturesREST.contract_name("BTC_USDT") == "BTC_USDT"
    assert GateFuturesREST.contract_name("BTC-USDT") == "BTC_USDT"
    with pytest.raises(ValueError):
        GateFuturesREST.contract_name("BTC-USD")


def test_gate_public_endpoints_are_get_only_and_use_expected_paths():
    async def run():
        client = FakeClient()
        gate = GateFuturesREST(client)

        assert await gate.exchange_info()
        assert (await gate.contract_info("BTCUSDT"))["quanto_multiplier"] == "0.001"
        assert (await gate.klines("BTCUSDT", "15m", 200))[0]["c"] == "2"
        assert (await gate.ticker_24h("BTCUSDT"))["last"] == "2"
        assert (await gate.funding_rate_history("BTCUSDT"))[0]["r"] == "0.0001"
        assert (await gate.contract_stats("BTCUSDT"))[0]["open_interest"] == "10"

        paths = [path for path, _ in client.calls]
        assert "/futures/usdt/contracts" in paths
        assert "/futures/usdt/contracts/BTC_USDT" in paths
        assert "/futures/usdt/candlesticks" in paths
        assert "/futures/usdt/tickers" in paths
        assert "/futures/usdt/funding_rate" in paths
        assert "/futures/usdt/contract_stats" in paths

    asyncio.run(run())
