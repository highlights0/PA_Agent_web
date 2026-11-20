"""Coinbase Exchange public REST market-data adapter (no account credentials)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.adapters.binance.client import BinanceRESTClient


_GRANULARITY_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
    "6h": 21600, "1d": 86400,
}


class CoinbaseSpotREST:
    def __init__(self, client: BinanceRESTClient) -> None:
        self._client = client

    @staticmethod
    def product_id(symbol: str) -> str:
        value = symbol.strip().upper()
        if "-" not in value:
            raise ValueError(
                "Coinbase spot symbols use BASE-QUOTE format, for example BTC-USD or ETH-USD"
            )
        return value

    async def exchange_info(self) -> dict[str, Any]:
        products = await self._client.get("/products")
        return {"products": products}

    async def klines(self, symbol: str, interval: str, limit: int) -> list[Any]:
        if interval not in _GRANULARITY_SECONDS:
            raise ValueError(
                f"Coinbase does not provide {interval} candles; supported: "
                + ", ".join(_GRANULARITY_SECONDS)
            )
        product = self.product_id(symbol)
        seconds = _GRANULARITY_SECONDS[interval]
        end = int(datetime.now(timezone.utc).timestamp())
        start = end - seconds * limit
        rows = await self._client.get(
            f"/products/{product}/candles",
            {"start": start, "end": end, "granularity": seconds},
        )
        return list(reversed(rows))

    async def ticker_24h(self, symbol: str) -> dict[str, Any]:
        product = self.product_id(symbol)
        stats = await self._client.get(f"/products/{product}/stats")
        ticker = await self._client.get(f"/products/{product}/ticker")
        open_price = float(stats.get("open") or 0)
        last_price = float(ticker.get("price") or 0)
        change = last_price - open_price
        change_pct = (change / open_price * 100) if open_price else 0.0
        return {
            "symbol": product,
            "lastPrice": str(ticker.get("price", "")),
            "priceChange": str(change),
            "priceChangePercent": str(change_pct),
            "highPrice": str(stats.get("high", "")),
            "lowPrice": str(stats.get("low", "")),
            "volume": str(stats.get("volume", "")),
            "quoteVolume": "",
        }
