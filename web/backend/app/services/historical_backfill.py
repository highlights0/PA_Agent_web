"""Multi-exchange historical candle backfill with bounded time pagination."""
from __future__ import annotations

import asyncio
from typing import Any

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
) -> dict[str, Any]:
    """Fetch the newest requested number of closed candles, oldest page first.

    Page boundaries are expressed as inclusive candle open times. Missing
    candles are preserved as gaps; downstream sample generation rejects any
    feature/label window that crosses a gap.
    """
    if timeframe not in _INTERVAL_MS:
        raise ValueError(f"Unsupported historical candle interval: {timeframe}")
    if not _MIN_BARS <= requested_bars <= _MAX_BARS:
        raise ValueError(
            f"requested_bars must be between {_MIN_BARS} and {_MAX_BARS}"
        )

    # Fail fast for unsupported market/exchange combinations.
    market_service._provider(market, exchange)
    page_limit = _PAGE_LIMITS.get(exchange)
    if page_limit is None:
        raise ValueError(f"Historical backfill is not configured for {exchange}")

    recent = await market_service.klines(
        market, symbol, timeframe, min(5, page_limit), exchange
    )
    closed_recent = [item for item in recent if item.closed]
    if not closed_recent:
        raise ValueError("The exchange returned no completed candles")

    step_ms = _INTERVAL_MS[timeframe]
    cursor = max(item.open_time for item in closed_recent)
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
