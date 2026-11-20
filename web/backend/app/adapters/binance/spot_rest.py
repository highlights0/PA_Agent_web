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

    async def ticker_24h(self, symbol: str) -> dict[str, Any]:
        return await self._client.get("/api/v3/ticker/24hr", {"symbol": symbol})
