"""Public market-data response models with explicit exchange attribution."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


MarketType = Literal["spot", "usdm"]
ExchangeType = Literal["binance", "binance_us", "coinbase", "gate"]


class MarketSymbol(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    market: MarketType
    status: str
    exchange: ExchangeType = "binance"


class KlineDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_time: int
    close_time: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    quote_volume: str
    trades: int
    taker_buy_base_volume: str
    taker_buy_quote_volume: str
    closed: bool
    exchange: ExchangeType = "binance"
    market_type: MarketType = "spot"
    volume_unit: str = "base_asset"
    contract_volume: str | None = None


class TickerDTO(BaseModel):
    symbol: str
    last_price: str
    price_change: str
    price_change_percent: str
    high_price: str
    low_price: str
    volume: str
    quote_volume: str
    exchange: ExchangeType = "binance"


class FuturesContextDTO(BaseModel):
    symbol: str
    exchange: ExchangeType = "binance"
    contract: str | None = None
    mark_price: str | None = None
    index_price: str | None = None
    funding_rate: str | None = None
    next_funding_time: int | None = None
    funding_interval_seconds: int | None = None
    open_interest: str | None = None
    open_interest_usd: str | None = None
    open_interest_unit: str | None = None
    open_interest_change_pct: float | None = None
    global_long_short_ratio: str | None = None
    taker_buy_sell_ratio: str | None = None
    taker_long_short_ratio: str | None = None
    top_trader_long_short_account_ratio: str | None = None
    top_trader_long_short_position_ratio: str | None = None
    long_liquidation_usd_3h: str | None = None
    short_liquidation_usd_3h: str | None = None
    long_liquidation_contracts_3h: str | None = None
    short_liquidation_contracts_3h: str | None = None
    stats_interval: str | None = None
    stats_window_minutes: int | None = None
    stats_history: list[dict[str, Any]] = Field(default_factory=list)
    funding_rate_history: list[dict[str, Any]] = Field(default_factory=list)
    data_timestamp: int
    data_quality: dict[str, str]
