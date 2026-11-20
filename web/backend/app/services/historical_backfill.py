"""Multi-exchange historical candle backfill with bounded time pagination."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.adapters.binance.normalizer import normalize_gate_klines

_INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "7d": 604_800_000,
}
_PAGE_LIMITS = {
    "binance": 1000,
    "binance_us": 1000,
    "coinbase": 300,
    "gate": 1000,
}
_MIN_BARS = 80
_MAX_BARS = 12_000


async def backfill_market_candles(
    market_service: Any,
    *,
    market: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    requested_bars: int,
    before_open_time_ms: int | None = None,
) -> dict[str, Any]:
    """Fetch completed candles before an optional archive boundary.

    Page boundaries are inclusive candle open times. Missing candles remain
    gaps and downstream sample generation never bridges them.
    """
    if timeframe not in _INTERVAL_MS:
        raise ValueError(f"Unsupported historical candle interval: {timeframe}")
    if not _MIN_BARS <= requested_bars <= _MAX_BARS:
        raise ValueError(
            f"requested_bars must be between {_MIN_BARS} and {_MAX_BARS}"
        )

    # No candle can exist before the Unix epoch; avoid issuing a negative Gate range.
    if before_open_time_ms is not None and int(before_open_time_ms) <= 0:
        return {
            "bars": [],
            "pages": 0,
            "gaps": 0,
            "closed_bars": 0,
            "source": exchange,
            "truncated": True,
        }

    # Fail fast for unsupported market/exchange combinations.
    market_service._provider(market, exchange)
    # Keep the established Gate range-pagination path unchanged; its API has
    # different from/to semantics and its behavior is covered by existing tests.
    if exchange == "gate":
        return await backfill_gate_candles(
            market_service._gate_futures,
            symbol=symbol,
            timeframe=timeframe,
            requested_bars=requested_bars,
            end_ms=(
                int(before_open_time_ms) + 1
                if before_open_time_ms is not None
                else None
            ),
        )
    page_limit = _PAGE_LIMITS.get(exchange)
    if page_limit is None:
        raise ValueError(f"Historical backfill is not configured for {exchange}")

    step_ms = _INTERVAL_MS[timeframe]
    if before_open_time_ms is None:
        recent = await market_service.klines(
            market, symbol, timeframe, min(5, page_limit), exchange
        )
        closed_recent = [item for item in recent if item.closed]
        if not closed_recent:
            raise ValueError("The exchange returned no completed candles")
        cursor = max(item.open_time for item in closed_recent)
    else:
        cursor = int(before_open_time_ms) - step_ms
        if cursor < 0:
            return {
                "bars": [],
                "pages": 0,
                "gaps": 0,
                "closed_bars": 0,
                "source": exchange,
                "truncated": True,
            }
    seen: dict[int, Any] = {}
    pages = 0
    empty_pages = 0
    max_pages = (requested_bars + page_limit - 1) // page_limit + 2

    while len(seen) < requested_bars and pages < max_pages:
        count = min(page_limit, requested_bars - len(seen))
        lower = max(0, cursor - (count - 1) * step_ms)
        rows = await market_service.klines_range(
            market,
            symbol,
            timeframe,
            start_ms=lower,
            end_ms=cursor,
            limit=count,
            exchange=exchange,
        )
        pages += 1

        page_closed = [
            item
            for item in rows
            if item.closed and lower <= item.open_time <= cursor
        ]
        for candle in page_closed:
            seen[candle.open_time] = candle

        if page_closed:
            empty_pages = 0
        else:
            empty_pages += 1
            if empty_pages >= 2:
                break

        if lower <= 0:
            break
        cursor = lower - step_ms
        await asyncio.sleep(0.05)

    candles = sorted(seen.values(), key=lambda item: item.open_time)
    if len(candles) > requested_bars:
        candles = candles[-requested_bars:]

    gaps = sum(
        1
        for left, right in zip(candles, candles[1:])
        if right.open_time - left.open_time != step_ms
    )
    return {
        "bars": candles,
        "pages": pages,
        "gaps": gaps,
        "closed_bars": len(candles),
        "source": exchange,
        "truncated": len(candles) < requested_bars,
    }


_GATE_INTERVAL_SECONDS = {
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
    """Backward-compatible Gate-only time-window backfill helper."""
    if timeframe not in _GATE_INTERVAL_SECONDS:
        raise ValueError(f"Gate history does not support {timeframe}")
    if not 80 <= requested_bars <= 12000:
        raise ValueError("requested_bars must be between 80 and 12000")

    multiplier = (await gate.contract_info(symbol)).get("quanto_multiplier")
    if multiplier is None:
        raise ValueError("Gate contract metadata lacks quanto_multiplier")

    now_ms = int(time.time() * 1000)
    cutoff_ms = min(int(end_ms or now_ms), now_ms)
    step = _GATE_INTERVAL_SECONDS[timeframe]
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
                response,
                interval=timeframe,
                quanto_multiplier=str(multiplier),
                now_ms=now_ms,
            )
            for candle in normalized:
                if candle.closed and candle.open_time <= upper * 1000:
                    seen[candle.open_time] = candle
        if lower <= 0:
            break
        upper = lower - step
        await asyncio.sleep(0.05)

    candles = sorted(seen.values(), key=lambda item: item.open_time)
    if len(candles) > requested_bars:
        candles = candles[-requested_bars:]
    gaps = sum(
        1
        for left, right in zip(candles, candles[1:])
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
