"""Typed derivatives context passed alongside candles to the PA prompts."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict


class CryptoMarketContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_type: str
    symbol: str
    data_timestamp: int
    mark_price: str | None = None
    index_price: str | None = None
    funding_rate: str | None = None
    next_funding_time: int | None = None
    open_interest: str | None = None
    open_interest_change_pct: float | None = None
    global_long_short_ratio: str | None = None
    taker_buy_sell_ratio: str | None = None
    data_quality: dict[str, str]


def render_crypto_context(context: CryptoMarketContext) -> str:
    """Render an explicit, timestamped context block; unavailable is never encoded as zero."""
    payload = context.model_dump()
    return (
        "## 加密货币市场补充数据（仅作辅助背景，不得替代已收盘 K 线结构）\n\n"
        "数据均来自 Binance 公共市场数据接口。字段缺失代表不可用，不代表数值为零。"
        "请先检查数据时间戳和 data_quality；不得根据缺失字段推断多空方向。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
