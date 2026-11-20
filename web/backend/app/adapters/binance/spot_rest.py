"""Binance Spot public REST endpoints used by the Web market service."""
from __future__ import annotations

from typing import Any

from .client import BinanceRESTClient


class BinanceSpotREST:
    def __init__(self, client: BinanceRESTClient) -> None:
        self._client = client

    async def exchange_info(self) -> dict[str, Any]:
        return await self._client.get("/api/v3/exchangeInfo")

    async def klines(self, symbol: str, interval: str, limit: int) -> list[Any]:
        return await self._client.get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    async def klines_range(
        self,
        symbol: str,
        interval: str,
        *,
        start_ms: int,
        end_ms: int,
        limit: int = 1000,
    ) -> list[Any]:
        if start_ms < 0 or end_ms < start_ms:
            raise ValueError("Invalid Binance historical candle time range")
        return await self._client.get(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": int(start_ms),
                "endTime": int(end_ms),
                "limit": min(max(int(limit), 1), 1000),
            },
        )

    async def ticker_24h(self, symbol: str) -> dict[str, Any]:
        return await self._client.get("/api/v3/ticker/24hr", {"symbol": symbol})
