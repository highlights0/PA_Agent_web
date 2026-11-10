"""Multi-exchange market service with explicit source and metric attribution."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from app.adapters.binance.models import (
    FuturesContextDTO,
    KlineDTO,
    MarketSymbol,
    MarketType,
    TickerDTO,
)
from app.adapters.binance.normalizer import (
    normalize_coinbase_klines,
    normalize_gate_klines,
    normalize_klines,
)
from app.services.provider_registry import ProviderRegistry


class MarketService:
    def __init__(
        self,
        providers: ProviderRegistry,
        binance_futures: Any,
        gate_futures: Any,
    ) -> None:
        self._providers = providers
        self._binance_futures = binance_futures
        self._gate_futures = gate_futures
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
            elif exchange == "gate":
                for item in payload:
                    if item.get("status") != "trading" or item.get("in_delisting"):
                        continue
                    contract = str(item.get("name", ""))
                    if not contract.endswith("_USDT"):
                        continue
                    base, quote = contract.rsplit("_", 1)
                    symbols.append(
                        MarketSymbol(
                            symbol=contract,
                            base_asset=base,
                            quote_asset=quote,
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
        provider = self._provider(market, exchange)
        rows = await provider.klines(symbol, interval, limit)
        if exchange == "coinbase":
            return normalize_coinbase_klines(rows, interval=interval)
        if exchange == "gate":
            contract = await provider.contract_info(symbol)
            multiplier = contract.get("quanto_multiplier")
            if multiplier is None:
                raise ValueError("Gate contract metadata is missing quanto_multiplier")
            return normalize_gate_klines(
                rows,
                interval=interval,
                quanto_multiplier=str(multiplier),
            )
        return normalize_klines(rows, market_type=market, exchange=exchange)

    async def ticker(
        self, market: MarketType, symbol: str, exchange: str = "binance"
    ) -> TickerDTO:
        raw = await self._provider(market, exchange).ticker_24h(symbol)
        if exchange == "gate":
            return TickerDTO(
                symbol=str(raw["contract"]),
                last_price=str(raw.get("last", "")),
                price_change=str(raw.get("change_price", "")),
                price_change_percent=str(raw.get("change_percentage", "")),
                high_price=str(raw.get("high_24h", "")),
                low_price=str(raw.get("low_24h", "")),
                volume=str(raw.get("volume_24h_base", "")),
                quote_volume=str(raw.get("volume_24h_quote", "")),
                exchange=exchange,
            )
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

    async def futures_context(
        self,
        symbol: str,
        period: str = "15m",
        exchange: str = "binance",
    ) -> FuturesContextDTO:
        if exchange == "gate":
            return await self._gate_futures_context(symbol, period)
        if exchange != "binance":
            raise ValueError(f"Futures context is not implemented for {exchange}")

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
            exchange="binance",
            mark_price=str(premium["markPrice"]) if premium.get("markPrice") is not None else None,
            index_price=str(premium["indexPrice"]) if premium.get("indexPrice") is not None else None,
            funding_rate=str(premium["lastFundingRate"]) if premium.get("lastFundingRate") is not None else None,
            next_funding_time=int(premium["nextFundingTime"]) if premium.get("nextFundingTime") is not None else None,
            open_interest=str(oi["openInterest"]) if oi.get("openInterest") is not None else None,
            open_interest_unit="base_asset",
            open_interest_change_pct=oi_change,
            global_long_short_ratio=str(long_short[-1].get("longShortRatio")) if long_short else None,
            taker_buy_sell_ratio=str(taker[-1].get("buySellRatio")) if taker else None,
            data_timestamp=int(time.time() * 1000),
            data_quality=quality,
        )

    async def _gate_futures_context(
        self, symbol: str, requested_interval: str = "5m"
    ) -> FuturesContextDTO:
        stats_interval = requested_interval if requested_interval in {
            "5m", "15m", "30m", "1h", "4h", "8h", "1d"
        } else "5m"
        tasks = {
            "contract": self._gate_futures.contract_info(symbol),
            "ticker": self._gate_futures.ticker_24h(symbol),
            "stats": self._gate_futures.contract_stats(symbol, stats_interval, 37),
            "funding": self._gate_futures.funding_rate_history(symbol, 5),
        }
        names = list(tasks)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data = dict(zip(names, results, strict=True))
        quality: dict[str, str] = {
            name: "ok" if not isinstance(result, BaseException) else "unavailable"
            for name, result in data.items()
        }

        contract = data["contract"] if isinstance(data["contract"], dict) else {}
        ticker = data["ticker"] if isinstance(data["ticker"], dict) else {}
        raw_stats = data["stats"] if isinstance(data["stats"], list) else []
        raw_funding = data["funding"] if isinstance(data["funding"], list) else []

        stats = sorted(raw_stats, key=lambda item: int(item.get("time", 0)))
        funding = sorted(raw_funding, key=lambda item: int(item.get("t", 0)))
        quality["funding_history"] = f"ok:{len(funding)}_records" if funding else "empty_or_unavailable"
        latest = stats[-1] if stats else {}
        first = stats[0] if stats else {}

        def number(value: Any) -> Decimal | None:
            if value is None or value == "":
                return None
            try:
                parsed = Decimal(str(value))
            except (InvalidOperation, ValueError):
                return None
            return parsed if parsed.is_finite() else None

        def string_value(value: Any) -> str | None:
            return str(value) if value is not None and value != "" else None

        def sum_stat(new_field: str, legacy_field: str) -> tuple[str | None, bool]:
            values: list[Decimal] = []
            missing = False
            for row in stats:
                raw = row.get(new_field)
                if raw is None:
                    raw = row.get(legacy_field)
                parsed = number(raw)
                if parsed is None:
                    missing = True
                else:
                    values.append(parsed)
            if not values:
                return None, True
            total = sum(values, Decimal("0"))
            return format(total, "f"), missing

        def sum_contracts(field: str) -> str | None:
            values = [number(row.get(field)) for row in stats]
            parsed = [value for value in values if value is not None]
            return format(sum(parsed, Decimal("0")), "f") if parsed else None

        oi_change: float | None = None
        oi_change_basis: str | None = None
        oi_usd_values = [number(row.get("open_interest_usd")) for row in stats]
        oi_contract_values = [number(row.get("open_interest")) for row in stats]
        if len(oi_usd_values) >= 2 and all(value is not None for value in oi_usd_values):
            first_oi, latest_oi = oi_usd_values[0], oi_usd_values[-1]
            oi_change_basis = "quote_currency"
        elif len(oi_contract_values) >= 2 and all(value is not None for value in oi_contract_values):
            first_oi, latest_oi = oi_contract_values[0], oi_contract_values[-1]
            oi_change_basis = "contracts"
        else:
            first_oi = latest_oi = None
        if first_oi is not None and latest_oi is not None and first_oi != 0:
            oi_change = float((latest_oi - first_oi) / first_oi * Decimal("100"))

        long_liq_usd, long_liq_partial = sum_stat("long_liq_usd_new", "long_liq_usd")
        short_liq_usd, short_liq_partial = sum_stat("short_liq_usd_new", "short_liq_usd")
        long_liq_contracts = sum_contracts("long_liq_size")
        short_liq_contracts = sum_contracts("short_liq_size")

        if stats:
            quality["contract_stats"] = f"ok:{len(stats)}_records"
            quality["liquidation_long"] = "partial" if long_liq_partial else "ok"
            quality["liquidation_short"] = "partial" if short_liq_partial else "ok"
        else:
            quality["contract_stats"] = "unavailable"
            quality["liquidation_long"] = "unavailable"
            quality["liquidation_short"] = "unavailable"

        window_minutes = None
        if len(stats) >= 2:
            window_minutes = max(
                0, int((int(stats[-1].get("time", 0)) - int(stats[0].get("time", 0))) / 60)
            )

        stats_history: list[dict[str, Any]] = []
        for row in stats:
            row_long = row.get("long_liq_usd_new")
            if row_long is None:
                row_long = row.get("long_liq_usd")
            row_short = row.get("short_liq_usd_new")
            if row_short is None:
                row_short = row.get("short_liq_usd")
            stats_history.append(
                {
                    "timestamp": int(row.get("time", 0)) * 1000,
                    "mark_price": string_value(row.get("mark_price")),
                    "open_interest_contracts": string_value(row.get("open_interest")),
                    "open_interest_usd": string_value(row.get("open_interest_usd")),
                    "long_short_account_ratio": string_value(row.get("lsr_account")),
                    "taker_long_short_ratio": string_value(row.get("lsr_taker")),
                    "long_liquidation_usd": string_value(row_long),
                    "short_liquidation_usd": string_value(row_short),
                }
            )

        funding_history = [
            {
                "timestamp": int(row.get("t", 0)) * 1000,
                "rate": string_value(row.get("r")),
            }
            for row in funding
        ]

        funding_rate = ticker.get("funding_rate")
        if funding_rate is None:
            funding_rate = contract.get("funding_rate")
        mark_price = ticker.get("mark_price")
        if mark_price is None:
            mark_price = latest.get("mark_price")
        index_price = ticker.get("index_price")
        if index_price is None:
            index_price = contract.get("index_price")

        quality["funding_rate"] = "ok" if funding_rate is not None else "unavailable"
        quality["open_interest"] = "ok" if latest.get("open_interest") is not None else "unavailable"
        quality["open_interest_usd"] = "ok" if latest.get("open_interest_usd") is not None else "unavailable"
        quality["open_interest_change_pct"] = "ok" if oi_change is not None else "unavailable_or_inconsistent_history"
        quality["open_interest_change_basis"] = oi_change_basis or "unavailable"

        next_funding = contract.get("funding_next_apply")
        try:
            next_funding_ms = int(float(next_funding) * 1000) if next_funding is not None else None
        except (TypeError, ValueError):
            next_funding_ms = None

        timestamp = int(latest.get("time", 0)) * 1000 if latest else int(time.time() * 1000)
        return FuturesContextDTO(
            symbol=symbol,
            exchange="gate",
            contract=self._gate_futures.contract_name(symbol),
            mark_price=string_value(mark_price),
            index_price=string_value(index_price),
            funding_rate=string_value(funding_rate),
            next_funding_time=next_funding_ms,
            funding_interval_seconds=int(contract["funding_interval"]) if contract.get("funding_interval") is not None else None,
            open_interest=string_value(latest.get("open_interest")),
            open_interest_usd=string_value(latest.get("open_interest_usd")),
            open_interest_unit="contracts",
            open_interest_change_pct=oi_change,
            global_long_short_ratio=string_value(latest.get("lsr_account")),
            taker_long_short_ratio=string_value(latest.get("lsr_taker")),
            top_trader_long_short_account_ratio=string_value(latest.get("top_lsr_account")),
            top_trader_long_short_position_ratio=string_value(latest.get("top_lsr_size")),
            long_liquidation_usd_3h=long_liq_usd,
            short_liquidation_usd_3h=short_liq_usd,
            long_liquidation_contracts_3h=long_liq_contracts,
            short_liquidation_contracts_3h=short_liq_contracts,
            stats_interval=stats_interval,
            stats_window_minutes=window_minutes,
            stats_history=stats_history,
            funding_rate_history=funding_history,
            data_timestamp=timestamp,
            data_quality=quality,
        )
