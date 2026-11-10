"""Typed, source-attributed derivatives context passed alongside candles to PA prompts."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CryptoMarketContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_type: str
    exchange: str = "unknown"
    symbol: str
    contract: str | None = None
    data_timestamp: int
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
    data_quality: dict[str, str]


def render_crypto_context(context: CryptoMarketContext) -> str:
    """Render explicit market provenance and quality; missing fields are never treated as zero."""
    payload = context.model_dump()
    return (
        "## 加密货币衍生品市场补充数据（辅助背景，不得替代已收盘 K 线结构）\n\n"
        f"数据来源：{context.exchange}；合约：{context.contract or context.symbol}。"
        "所有比率和金额保留来源交易所的原始口径。字段缺失代表不可用，不代表数值为零。"
        "必须先检查 data_timestamp、stats_interval 和 data_quality；不同交易所数据不可混为同一市场。"
        "多空比大于 1 的方向解释以对应交易所字段定义为准，不得把账户比率等同于持仓量比率。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
