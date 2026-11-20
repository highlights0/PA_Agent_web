"""Bounded historical Gate USDT futures backfill using time-window pagination.

Gate rejects mixing limit with from/to. Each page uses UTC-second inclusive
from/to boundaries and is normalized with the same multiplier as live candles.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.adapters.binance.normalizer import normalize_gate_klines

_INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "8h": 28800,
    "1d": 86400, "7d": 604800,
}


async def backfill_gate_candles(
    gate: Any,
    *,
    symbol: str,
    timeframe: str,
    requested_bars: int,
    end_ms: int | None = None,
) -> dict[str, Any]:
    """Fetch up to requested_bars closed candles; never bridge missing pages."""
    if timeframe not in _INTERVAL_SECONDS:
        raise ValueError(f"Gate history does not support {timeframe}")
    if not 80 <= requested_bars <= 12000:
        raise ValueError("requested_bars must be between 80 and 12000")

    multiplier = (await gate.contract_info(symbol)).get("quanto_multiplier")
    if multiplier is None:
        raise ValueError("Gate contract metadata lacks quanto_multiplier")

    now_ms = int(time.time() * 1000)
    cutoff_ms = min(int(end_ms or now_ms), now_ms)
    # Use last completed candle as upper bound; 7d is epoch-aligned, per Gate.
    step = _INTERVAL_SECONDS[timeframe]
    upper = ((cutoff_ms // 1000) // step) * step - step
    seen: dict[int, Any] = {}
    pages = 0
    empty_pages = 0
    while len(seen) < requested_bars and pages < 12:
        count = min(1000, requested_bars - len(seen) + 26)
        lower = max(0, upper - (count - 1) * step)
        response = await gate.klines_range(
            symbol, timeframe, from_s=lower, to_s=upper
        )
        pages += 1
        if not isinstance(response, list):
            raise ValueError("Gate historical candle response must be an array")
        if not response:
            empty_pages += 1
            if empty_pages >= 2:
                break
        else:
            normalized = normalize_gate_klines(
                response, interval=timeframe,
                quanto_multiplier=str(multiplier), now_ms=now_ms,
            )
            for candle in normalized:
                if candle.closed and candle.open_time <= upper * 1000:
                    seen[candle.open_time] = candle
        if lower <= 0:
            break
        upper = lower - step
        # One cooperative scheduling point and moderate pacing per page.
        await asyncio.sleep(0.05)
    candles = sorted(seen.values(), key=lambda x: x.open_time)
    if len(candles) > requested_bars:
        candles = candles[-requested_bars:]
    gaps = sum(
        1 for left, right in zip(candles, candles[1:])
        if right.open_time - left.open_time != step * 1000
    )
    return {
        "bars": candles,
        "pages": pages,
        "gaps": gaps,
        "closed_bars": len(candles),
        "source": "gate",
        "truncated": len(candles) < requested_bars,
    }
