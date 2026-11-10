"""Gate.io USDT perpetual futures public REST endpoints (no API key)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.adapters.binance.client import BinanceRESTClient


_SUPPORTED_INTERVALS = {
    "1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "7d",
}


class GateFuturesREST:
    """Read-only Gate.io USDT-settled perpetual market-data adapter."""

    def __init__(self, client: BinanceRESTClient) -> None:
        self._client = client
        self._contract_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_lock = asyncio.Lock()

    @staticmethod
    def contract_name(symbol: str) -> str:
        value = symbol.strip().upper().replace("-", "_")
        if "_" in value:
            base, quote = value.split("_", 1)
            if base and quote == "USDT":
                return f"{base}_USDT"
            raise ValueError("Gate USDT perpetual symbols must use BASE_USDT format")
        if value.endswith("USDT") and len(value) > 4:
            return f"{value[:-4]}_USDT"
        raise ValueError("Gate USDT perpetual symbol must end in USDT, e.g. BTCUSDT")

    async def exchange_info(self) -> list[dict[str, Any]]:
        return await self._client.get("/futures/usdt/contracts")

    async def contract_info(self, symbol: str) -> dict[str, Any]:
        contract = self.contract_name(symbol)
        cached = self._contract_cache.get(contract)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        async with self._cache_lock:
            cached = self._contract_cache.get(contract)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            payload = await self._client.get(f"/futures/usdt/contracts/{contract}")
            if not isinstance(payload, dict):
                raise ValueError("Malformed Gate contract information")
            self._contract_cache[contract] = (time.monotonic() + 300, payload)
            return payload

    async def klines(self, symbol: str, interval: str, limit: int) -> list[dict[str, Any]]:
        if interval not in _SUPPORTED_INTERVALS:
            raise ValueError(
                f"Gate does not support {interval} candles; supported intervals: "
                + ", ".join(sorted(_SUPPORTED_INTERVALS))
            )
        return await self._client.get(
            "/futures/usdt/candlesticks",
            {
                "contract": self.contract_name(symbol),
                "interval": interval,
                "limit": min(max(int(limit), 1), 2000),
                "timezone": "utc0",
            },
        )

    async def ticker_24h(self, symbol: str) -> dict[str, Any]:
        rows = await self._client.get(
            "/futures/usdt/tickers",
            {"contract": self.contract_name(symbol)},
        )
        if not isinstance(rows, list) or not rows:
            raise ValueError("Gate returned no ticker for this contract")
        return rows[0]

    async def funding_rate_history(self, symbol: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self._client.get(
            "/futures/usdt/funding_rate",
            {
                "contract": self.contract_name(symbol),
                "limit": min(max(int(limit), 1), 100),
            },
        )

    async def contract_stats(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 37,
    ) -> list[dict[str, Any]]:
        if interval not in {"5m", "15m", "30m", "1h", "4h", "8h", "1d"}:
            raise ValueError(f"Unsupported Gate contract-stats interval: {interval}")
        return await self._client.get(
            "/futures/usdt/contract_stats",
            {
                "contract": self.contract_name(symbol),
                "interval": interval,
                "limit": min(max(int(limit), 1), 200),
            },
        )
