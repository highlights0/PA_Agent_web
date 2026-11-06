"""Multi-exchange market service with explicit source attribution."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.adapters.binance.models import (
    FuturesContextDTO,
    KlineDTO,
    MarketSymbol,
    MarketType,
    TickerDTO,
)
from app.adapters.binance.normalizer import normalize_coinbase_klines, normalize_klines
from app.services.provider_registry import ProviderRegistry


class MarketService:
    def __init__(self, providers: ProviderRegistry, binance_futures: Any) -> None:
        self._providers = providers
        self._binance_futures = binance_futures
        self._symbol_cache: dict[tuple[str, str], tuple[float, list[MarketSymbol]]] = {}
        self._cache_lock = asyncio.Lock()

    @property
    def supported_exchanges(self) -> dict[str, list[str]]:
        return self._providers.supported

    def _provider(self, market: MarketType, exchange: str):
        return self._providers.get(exchange, market)

    async def markets(
        self, market: MarketType, exchange: str = "binance"
    ) -> list[MarketSymbol]:
        key = (exchange, market)
        cached = self._symbol_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        async with self._cache_lock:
            cached = self._symbol_cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]

            payload = await self._provider(market, exchange).exchange_info()
            symbols: list[MarketSymbol] = []
            if exchange == "coinbase":
                for item in payload.get("products", []):
                    if item.get("status") != "online" or item.get("trading_disabled"):
                        continue
                    product_id = str(item.get("id", ""))
                    if not product_id:
                        continue
                    symbols.append(
                        MarketSymbol(
                            symbol=product_id,
                            base_asset=str(item.get("base_currency", "")),
                            quote_asset=str(item.get("quote_currency", "")),
                            market=market,
                            status="TRADING",
                            exchange=exchange,
                        )
                    )
            else:
                for item in payload.get("symbols", []):
                    if item.get("status") != "TRADING":
                        continue
                    if market == "spot" and item.get("isSpotTradingAllowed") is False:
                        continue
                    if market == "usdm" and item.get("contractType") != "PERPETUAL":
                        continue
                    symbols.append(
                        MarketSymbol(
                            symbol=item["symbol"],
                            base_asset=item["baseAsset"],
                            quote_asset=item["quoteAsset"],
                            market=market,
                            status=item["status"],
                            exchange=exchange,
                        )
                    )
            symbols.sort(key=lambda item: item.symbol)
            self._symbol_cache[key] = (time.monotonic() + 300, symbols)
            return symbols

    async def klines(
        self,
        market: MarketType,
        symbol: str,
        interval: str,
        limit: int,
        exchange: str = "binance",
    ) -> list[KlineDTO]:
        rows = await self._provider(market, exchange).klines(symbol, interval, limit)
        if exchange == "coinbase":
            return normalize_coinbase_klines(rows, interval=interval)
        return normalize_klines(rows)

    async def ticker(
        self, market: MarketType, symbol: str, exchange: str = "binance"
    ) -> TickerDTO:
        raw = await self._provider(market, exchange).ticker_24h(symbol)
        return TickerDTO(
            symbol=raw["symbol"],
            last_price=str(raw["lastPrice"]),
            price_change=str(raw["priceChange"]),
            price_change_percent=str(raw["priceChangePercent"]),
            high_price=str(raw["highPrice"]),
            low_price=str(raw["lowPrice"]),
            volume=str(raw["volume"]),
            quote_volume=str(raw.get("quoteVolume", "")),
            exchange=exchange,
        )

    async def futures_context(self, symbol: str, period: str = "15m") -> FuturesContextDTO:
        """Binance USD-M context; another exchange must implement this explicitly."""
        tasks = {
            "premium": self._binance_futures.premium_index(symbol),
            "open_interest": self._binance_futures.open_interest(symbol),
            "oi_history": self._binance_futures.open_interest_history(symbol, period, 2),
            "long_short": self._binance_futures.global_long_short_ratio(symbol, period),
            "taker": self._binance_futures.taker_long_short_ratio(symbol, period),
        }
        names = list(tasks)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data = dict(zip(names, results, strict=True))
        quality: dict[str, str] = {
            name: "ok" if not isinstance(result, BaseException) else "unavailable"
            for name, result in data.items()
        }

        premium = data.get("premium")
        oi = data.get("open_interest")
        oi_history = data.get("oi_history")
        long_short = data.get("long_short")
        taker = data.get("taker")

        def valid(value: Any) -> bool:
            return not isinstance(value, BaseException) and value is not None

        oi_change: float | None = None
        if valid(oi_history) and isinstance(oi_history, list) and len(oi_history) >= 2:
            try:
                old = float(oi_history[-2]["sumOpenInterest"])
                new = float(oi_history[-1]["sumOpenInterest"])
                if old:
                    oi_change = (new - old) / old * 100
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                quality["oi_history"] = "invalid"

        premium = premium if valid(premium) else {}
        oi = oi if valid(oi) else {}
        long_short = long_short if valid(long_short) and long_short else []
        taker = taker if valid(taker) and taker else []

        return FuturesContextDTO(
            symbol=symbol,
            mark_price=str(premium["markPrice"]) if premium.get("markPrice") is not None else None,
            index_price=str(premium["indexPrice"]) if premium.get("indexPrice") is not None else None,
            funding_rate=str(premium["lastFundingRate"]) if premium.get("lastFundingRate") is not None else None,
            next_funding_time=int(premium["nextFundingTime"]) if premium.get("nextFundingTime") is not None else None,
            open_interest=str(oi["openInterest"]) if oi.get("openInterest") is not None else None,
            open_interest_change_pct=oi_change,
            global_long_short_ratio=str(long_short[-1].get("longShortRatio")) if long_short else None,
            taker_buy_sell_ratio=str(taker[-1].get("buySellRatio")) if taker else None,
            data_timestamp=int(time.time() * 1000),
            data_quality=quality,
        )
