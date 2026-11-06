"""Public API response models for normalized Binance market data."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


MarketType = Literal["spot", "usdm"]


class MarketSymbol(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str
    market: MarketType
    status: str


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


class TickerDTO(BaseModel):
    symbol: str
    last_price: str
    price_change: str
    price_change_percent: str
    high_price: str
    low_price: str
    volume: str
    quote_volume: str


class FuturesContextDTO(BaseModel):
    symbol: str
    mark_price: str | None = None
    index_price: str | None = None
    funding_rate: str | None = None
    next_funding_time: int | None = None
    open_interest: str | None = None
    open_interest_change_pct: float | None = None
    global_long_short_ratio: str | None = None
    taker_buy_sell_ratio: str | None = None
    data_timestamp: int
    data_quality: dict[str, str]
