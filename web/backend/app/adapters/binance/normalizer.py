"""Normalize Binance REST klines and adapt closed bars to PA_Agent types."""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import KlineDTO


def _decimal_string(value: Any, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid Binance kline {field}") from exc
    if not number.is_finite():
        raise ValueError(f"Non-finite Binance kline {field}")
    return format(number, "f")


def normalize_klines(rows: list[Any], *, now_ms: int | None = None) -> list[KlineDTO]:
    """Convert Binance's positional kline arrays to stable DTOs in oldest-first order."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    normalized: list[KlineDTO] = []
    previous_open_time = -1

    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 11:
            raise ValueError("Malformed Binance kline row")
        open_time = int(row[0])
        close_time = int(row[6])
        if open_time <= previous_open_time:
            raise ValueError("Binance klines must be strictly oldest-first without duplicates")
        previous_open_time = open_time
        normalized.append(
            KlineDTO(
                open_time=open_time,
                close_time=close_time,
                open=_decimal_string(row[1], "open"),
                high=_decimal_string(row[2], "high"),
                low=_decimal_string(row[3], "low"),
                close=_decimal_string(row[4], "close"),
                volume=_decimal_string(row[5], "volume"),
                quote_volume=_decimal_string(row[7], "quote_volume"),
                trades=int(row[8]),
                taker_buy_base_volume=_decimal_string(row[9], "taker_buy_base_volume"),
                taker_buy_quote_volume=_decimal_string(row[10], "taker_buy_quote_volume"),
                closed=close_time < now,
            )
        )
    return normalized


def to_core_bars(klines: list[KlineDTO]):
    """Convert only closed candles to the original newest-first KlineBar sequence."""
    from pa_agent.data.base import KlineBar

    closed = [item for item in klines if item.closed]
    newest_first = list(reversed(closed))
    return [
        KlineBar(
            seq=index + 1,
            ts_open=float(item.open_time),
            open=float(Decimal(item.open)),
            high=float(Decimal(item.high)),
            low=float(Decimal(item.low)),
            close=float(Decimal(item.close)),
            volume=float(Decimal(item.volume)),
            amount=float(Decimal(item.quote_volume)),
            closed=True,
        )
        for index, item in enumerate(newest_first)
    ]
