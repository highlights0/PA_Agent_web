"""Normalize exchange candles into the common Web DTO and original PA bar shape."""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import KlineDTO


_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000, "7d": 604_800_000,
}
_GATE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "7d"}


def _decimal_string(value: Any, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid kline {field}") from exc
    if not number.is_finite():
        raise ValueError(f"Non-finite kline {field}")
    return format(number, "f")


def normalize_klines(
    rows: list[Any],
    *,
    market_type: str = "spot",
    exchange: str = "binance",
    now_ms: int | None = None,
) -> list[KlineDTO]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    normalized: list[KlineDTO] = []
    previous_open_time = -1
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 11:
            raise ValueError("Malformed Binance kline row")
        open_time = int(row[0])
        close_time = int(row[6])
        if open_time <= previous_open_time:
            raise ValueError("Klines must be strictly oldest-first without duplicates")
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
                exchange=exchange,
                market_type=market_type,
                volume_unit="base_asset",
            )
        )
    return normalized


def normalize_coinbase_klines(
    rows: list[Any], *, interval: str, now_ms: int | None = None
) -> list[KlineDTO]:
    """Coinbase Exchange candles: [time, low, high, open, close, volume]."""
    if interval not in _INTERVAL_MS:
        raise ValueError(f"Unsupported Coinbase interval: {interval}")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    ordered = sorted(rows, key=lambda row: int(row[0]))
    normalized: list[KlineDTO] = []
    previous_open_time = -1
    duration = _INTERVAL_MS[interval]
    for row in ordered:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise ValueError("Malformed Coinbase candle row")
        open_time = int(row[0]) * 1000
        if open_time <= previous_open_time:
            raise ValueError("Coinbase candles contain duplicate timestamps")
        previous_open_time = open_time
        low, high, open_price, close_price, volume = row[1:6]
        close_time = open_time + duration - 1
        normalized.append(
            KlineDTO(
                open_time=open_time,
                close_time=close_time,
                open=_decimal_string(open_price, "open"),
                high=_decimal_string(high, "high"),
                low=_decimal_string(low, "low"),
                close=_decimal_string(close_price, "close"),
                volume=_decimal_string(volume, "volume"),
                quote_volume="",
                trades=None,
                taker_buy_base_volume="",
                taker_buy_quote_volume="",
                closed=close_time < now,
                exchange="coinbase",
                market_type="spot",
                volume_unit="base_asset",
            )
        )
    return normalized


def normalize_gate_klines(
    rows: list[dict[str, Any]],
    *,
    interval: str,
    quanto_multiplier: str,
    now_ms: int | None = None,
) -> list[KlineDTO]:
    """Gate candles use contract volume v and quote turnover sum; convert v to base volume."""
    if interval not in _GATE_INTERVALS:
        raise ValueError(f"Unsupported Gate candle interval: {interval}")
    multiplier = Decimal(_decimal_string(quanto_multiplier, "quanto_multiplier"))
    if multiplier <= 0:
        raise ValueError("Gate contract quanto_multiplier must be positive")
    for row in rows:
        if not isinstance(row, dict) or "t" not in row:
            raise ValueError("Malformed Gate futures candle row")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    ordered = sorted(rows, key=lambda row: int(row["t"]))
    normalized: list[KlineDTO] = []
    previous_open_time = -1
    duration = _INTERVAL_MS[interval]

    for row in ordered:
        open_time = int(row["t"]) * 1000
        if open_time <= previous_open_time:
            raise ValueError("Gate candles contain duplicate timestamps")
        previous_open_time = open_time

        contract_volume = _decimal_string(row.get("v"), "contract_volume")
        base_volume = Decimal(contract_volume) * multiplier
        close_time = open_time + duration - 1
        normalized.append(
            KlineDTO(
                open_time=open_time,
                close_time=close_time,
                open=_decimal_string(row.get("o"), "open"),
                high=_decimal_string(row.get("h"), "high"),
                low=_decimal_string(row.get("l"), "low"),
                close=_decimal_string(row.get("c"), "close"),
                volume=format(base_volume, "f"),
                quote_volume=_decimal_string(row.get("sum"), "quote_volume") if row.get("sum") is not None else "",
                trades=None,
                taker_buy_base_volume="",
                taker_buy_quote_volume="",
                closed=close_time < now,
                exchange="gate",
                market_type="usdm",
                volume_unit="base_asset",
                contract_volume=contract_volume,
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
            amount=float(Decimal(item.quote_volume or "0")),
            closed=True,
        )
        for index, item in enumerate(newest_first)
    ]
