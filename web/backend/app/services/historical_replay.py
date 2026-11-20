"""As-of market context reconstruction for historical PA Agent replay."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.crypto_context import CryptoMarketContext

_BINANCE_CONTEXT_PERIODS = {
    "1m": "5m",
    "3m": "5m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "6h",
    "12h": "12h",
    "1d": "1d",
}
_GATE_CONTEXT_PERIODS = {"5m", "15m", "30m", "1h", "4h", "8h", "1d"}


def context_period(exchange: str, timeframe: str) -> str:
    if exchange == "gate":
        return timeframe if timeframe in _GATE_CONTEXT_PERIODS else "5m"
    return _BINANCE_CONTEXT_PERIODS.get(timeframe, "1d")


def _number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _text(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def build_historical_derivatives_context(
    observations: list[dict[str, Any]],
    *,
    exchange: str,
    symbol: str,
    period: str,
    decision_time_ms: int,
) -> tuple[CryptoMarketContext, dict[str, Any]]:
    """Build a prompt context only from records whose availability precedes the cutoff."""
    eligible = [
        item
        for item in observations
        if int(item.get("event_time_ms", 0)) <= decision_time_ms
        and 0 < int(item.get("available_at_ms", 0)) <= decision_time_ms
        and (
            item.get("data_kind") != "historical"
            or item.get("period", "") in {"", period}
        )
        and item.get("quality") not in {"invalid", "unavailable", "no_data"}
    ]
    eligible.sort(
        key=lambda item: (
            int(item.get("available_at_ms", 0)),
            int(item.get("event_time_ms", 0)),
        )
    )

    by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        by_metric[str(item["metric"])].append(item)

    def latest(metric: str) -> dict[str, Any] | None:
        values = by_metric.get(metric, [])
        return values[-1] if values else None

    def value(metric: str, *keys: str) -> Any:
        item = latest(metric)
        if item is None:
            return None
        payload = item.get("value") or {}
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        return None

    def quality(metric: str) -> str:
        item = latest(metric)
        if item is None:
            return "missing_no_asof_observation"
        return f"{item.get('data_kind', 'unknown')}:{item.get('quality', 'unknown')}"

    funding = latest("funding_rate")
    funding_value = value("funding_rate", "funding_rate")
    mark_price = value("funding_rate", "mark_price") or value("mark_price", "value")
    if exchange == "binance":
        open_interest = value("open_interest", "sum_open_interest")
        open_interest_usd = value("open_interest", "sum_open_interest_value")
        open_interest_unit = "base_asset"
        global_ratio = value("global_long_short_ratio", "long_short_ratio")
        taker_ratio = value("taker_long_short_ratio", "buy_sell_ratio")
        top_account = value("top_long_short_account_ratio", "long_short_ratio")
        top_position = value("top_long_short_position_ratio", "long_short_ratio")
        taker_buy_sell = taker_ratio
        gate_taker = None
    else:
        open_interest = value("open_interest_contracts", "value")
        open_interest_usd = value("open_interest_usd", "value")
        open_interest_unit = "contracts"
        global_ratio = value("global_long_short_ratio", "value")
        gate_taker = value("taker_long_short_ratio", "value")
        taker_buy_sell = None
        top_account = value("top_trader_long_short_account_ratio", "value")
        top_position = value("top_trader_long_short_position_ratio", "value")

    oi_metric = "open_interest" if exchange == "binance" else "open_interest_contracts"
    oi_values = by_metric.get(oi_metric, [])
    oi_change_pct = None
    if len(oi_values) >= 2:
        old_payload = oi_values[-2].get("value") or {}
        new_payload = oi_values[-1].get("value") or {}
        oi_key = "sum_open_interest" if exchange == "binance" else "value"
        old_oi = _number(old_payload.get(oi_key))
        new_oi = _number(new_payload.get(oi_key))
        if old_oi not in (None, Decimal("0")) and new_oi is not None:
            oi_change_pct = float((new_oi - old_oi) / old_oi * Decimal("100"))

    long_liquidation_usd = None
    short_liquidation_usd = None
    long_liquidation_contracts = None
    short_liquidation_contracts = None
    stats_history: list[dict[str, Any]] = []
    if exchange == "gate":
        window_start = decision_time_ms - 3 * 60 * 60 * 1000

        def sum_recent(metric: str) -> str | None:
            values = [
                _number((item.get("value") or {}).get("value"))
                for item in by_metric.get(metric, [])
                if int(item.get("event_time_ms", 0)) >= window_start
            ]
            valid = [item for item in values if item is not None]
            return format(sum(valid, Decimal("0")), "f") if valid else None

        long_liquidation_usd = sum_recent("long_liquidation_usd")
        short_liquidation_usd = sum_recent("short_liquidation_usd")
        long_liquidation_contracts = sum_recent("long_liquidation_contracts")
        short_liquidation_contracts = sum_recent("short_liquidation_contracts")

        grouped: dict[int, dict[str, Any]] = defaultdict(dict)
        metric_fields = {
            "open_interest_contracts": "open_interest_contracts",
            "open_interest_usd": "open_interest_usd",
            "global_long_short_ratio": "long_short_account_ratio",
            "taker_long_short_ratio": "taker_long_short_ratio",
            "long_liquidation_usd": "long_liquidation_usd",
            "short_liquidation_usd": "short_liquidation_usd",
            "mark_price": "mark_price",
        }
        for metric, field in metric_fields.items():
            for item in by_metric.get(metric, []):
                event_time = int(item["event_time_ms"])
                if event_time < window_start:
                    continue
                grouped[event_time][field] = (item.get("value") or {}).get("value")
        stats_history = [
            {"timestamp": event_time, **fields}
            for event_time, fields in sorted(grouped.items())[-40:]
        ]

    funding_history = [
        {
            "timestamp": int(item["event_time_ms"]),
            "rate": (item.get("value") or {}).get("funding_rate"),
        }
        for item in by_metric.get("funding_rate", [])[-8:]
    ]
    data_quality = {
        "funding_rate": quality("funding_rate"),
        "open_interest": quality(oi_metric),
        "global_long_short_ratio": quality("global_long_short_ratio"),
        "taker_ratio": quality(
            "taker_long_short_ratio" if exchange == "gate" else "taker_long_short_ratio"
        ),
        "top_trader_account_ratio": quality(
            "top_trader_long_short_account_ratio"
            if exchange == "gate"
            else "top_long_short_account_ratio"
        ),
        "top_trader_position_ratio": quality(
            "top_trader_long_short_position_ratio"
            if exchange == "gate"
            else "top_long_short_position_ratio"
        ),
        "availability_filter": "event_time_and_available_at_le_decision_time",
    }
    if exchange == "gate":
        data_quality["liquidation_long"] = quality("long_liquidation_usd")
        data_quality["liquidation_short"] = quality("short_liquidation_usd")

    event_times = [int(item["event_time_ms"]) for item in eligible]
    context = CryptoMarketContext(
        market_type="usdm",
        exchange=exchange,
        symbol=symbol,
        contract=f"{symbol[:-4]}_USDT" if exchange == "gate" and symbol.endswith("USDT") else symbol,
        data_timestamp=max(event_times, default=decision_time_ms),
        mark_price=_text(mark_price),
        funding_rate=_text(funding_value),
        open_interest=_text(open_interest),
        open_interest_usd=_text(open_interest_usd),
        open_interest_unit=open_interest_unit,
        open_interest_change_pct=oi_change_pct,
        global_long_short_ratio=_text(global_ratio),
        taker_buy_sell_ratio=_text(taker_buy_sell),
        taker_long_short_ratio=_text(gate_taker),
        top_trader_long_short_account_ratio=_text(top_account),
        top_trader_long_short_position_ratio=_text(top_position),
        long_liquidation_usd_3h=_text(long_liquidation_usd),
        short_liquidation_usd_3h=_text(short_liquidation_usd),
        long_liquidation_contracts_3h=_text(long_liquidation_contracts),
        short_liquidation_contracts_3h=_text(short_liquidation_contracts),
        stats_interval=period,
        stats_window_minutes=180 if exchange == "gate" else None,
        stats_history=stats_history,
        funding_rate_history=funding_history,
        data_quality=data_quality,
    )
    provenance = {
        "decision_time_ms": decision_time_ms,
        "observation_count": len(eligible),
        "historical_count": sum(item.get("data_kind") == "historical" for item in eligible),
        "snapshot_count": sum(item.get("data_kind") == "snapshot" for item in eligible),
        "latest_event_time_ms": max(event_times, default=None),
        "latest_available_at_ms": max(
            (int(item["available_at_ms"]) for item in eligible),
            default=None,
        ),
        "metrics_used": sorted(by_metric),
        "missing_metrics": sorted(
            metric
            for metric, state in data_quality.items()
            if state.startswith("missing")
        ),
        "future_observations_excluded": True,
    }
    return context, provenance
