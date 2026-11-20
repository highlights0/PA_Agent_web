"""Historical derivatives data collection with explicit source and time provenance.

Only records returned by an exchange's historical endpoint are written with
data_kind='historical'. Live FuturesContext values are stored separately as
snapshots and are never used to fill historical gaps.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable

_BINANCE_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
_GATE_PERIOD_SECONDS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "8h": 28800,
    "1d": 86400,
}
_BINANCE_WINDOW_LIMITED_METRICS = {
    "open_interest",
    "global_long_short_ratio",
    "taker_long_short_ratio",
    "top_long_short_account_ratio",
    "top_long_short_position_ratio",
}
_BINANCE_WINDOW_MS = 30 * 24 * 60 * 60 * 1000


def _event_time_ms(row: dict[str, Any], field: str) -> int:
    value = row.get(field)
    if value is None:
        raise ValueError(f"Historical derivatives row is missing {field}")
    timestamp = int(value)
    # Gate timestamps are seconds; Binance endpoints return milliseconds.
    return timestamp * 1000 if field == "time" or field == "t" else timestamp


def _normalize_binance(metric: str, row: dict[str, Any]) -> dict[str, Any]:
    if metric == "funding_rate":
        return {
            "funding_rate": row.get("fundingRate"),
            "mark_price": row.get("markPrice"),
            "rate_type": row.get("rateType"),
        }
    if metric == "open_interest":
        return {
            "sum_open_interest": row.get("sumOpenInterest"),
            "sum_open_interest_value": row.get("sumOpenInterestValue"),
            "circulating_supply": row.get("CMCCirculatingSupply"),
        }
    if metric in {"global_long_short_ratio", "top_long_short_account_ratio"}:
        return {
            "long_short_ratio": row.get("longShortRatio"),
            "long_account": row.get("longAccount"),
            "short_account": row.get("shortAccount"),
        }
    if metric == "taker_long_short_ratio":
        return {
            "buy_sell_ratio": row.get("buySellRatio"),
            "buy_volume": row.get("buyVol"),
            "sell_volume": row.get("sellVol"),
        }
    if metric == "top_long_short_position_ratio":
        return {
            "long_short_ratio": row.get("longShortRatio"),
            "long_account": row.get("longAccount"),
            "short_account": row.get("shortAccount"),
        }
    raise ValueError(f"Unsupported Binance derivatives metric: {metric}")


def _gate_stat_metrics(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mappings = {
        "open_interest_contracts": ("open_interest", "contracts"),
        "open_interest_usd": ("open_interest_usd", "quote_currency"),
        "global_long_short_ratio": ("lsr_account", "ratio"),
        "taker_long_short_ratio": ("lsr_taker", "ratio"),
        "top_trader_long_short_account_ratio": ("top_lsr_account", "ratio"),
        "top_trader_long_short_position_ratio": ("top_lsr_size", "ratio"),
        "long_liquidation_contracts": ("long_liq_size", "contracts"),
        "short_liquidation_contracts": ("short_liq_size", "contracts"),
        "long_liquidation_usd": ("long_liq_usd_new", "quote_currency"),
        "short_liquidation_usd": ("short_liq_usd_new", "quote_currency"),
        "mark_price": ("mark_price", "quote_currency"),
        "long_users": ("long_users", "accounts"),
        "short_users": ("short_users", "accounts"),
        "top_long_size": ("top_long_size", "contracts"),
        "top_short_size": ("top_short_size", "contracts"),
        "long_taker_size": ("long_taker_size", "contracts"),
        "short_taker_size": ("short_taker_size", "contracts"),
    }
    result: dict[str, dict[str, Any]] = {}
    for metric, (field, unit) in mappings.items():
        value = row.get(field)
        if value is None and metric.endswith("_liquidation_usd"):
            legacy = "long_liq_usd" if metric.startswith("long_") else "short_liq_usd"
            value = row.get(legacy)
        if value is not None:
            result[metric] = {"value": value, "unit": unit}
    return result


async def _collect_binance_metric(
    provider: Any,
    *,
    symbol: str,
    metric: str,
    period: str,
    start_ms: int,
    end_ms: int,
    fetched_at_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    endpoints: dict[str, tuple[str, Callable[..., Awaitable[list[Any]]], str, int]] = {
        "funding_rate": (
            "/fapi/v1/fundingRate",
            provider.funding_rate_history_range,
            "fundingTime",
            1000,
        ),
        "open_interest": (
            "/futures/data/openInterestHist",
            provider.open_interest_history_range,
            "timestamp",
            500,
        ),
        "global_long_short_ratio": (
            "/futures/data/globalLongShortAccountRatio",
            provider.global_long_short_ratio_range,
            "timestamp",
            500,
        ),
        "taker_long_short_ratio": (
            "/futures/data/takerlongshortRatio",
            provider.taker_long_short_ratio_range,
            "timestamp",
            500,
        ),
        "top_long_short_account_ratio": (
            "/futures/data/topLongShortAccountRatio",
            provider.top_long_short_account_ratio_range,
            "timestamp",
            500,
        ),
        "top_long_short_position_ratio": (
            "/futures/data/topLongShortPositionRatio",
            provider.top_long_short_position_ratio_range,
            "timestamp",
            500,
        ),
    }
    endpoint, fetch, time_field, page_size = endpoints[metric]
    effective_start = start_ms
    status = "ok"
    if metric in _BINANCE_WINDOW_LIMITED_METRICS:
        window_start = fetched_at_ms - _BINANCE_WINDOW_MS
        if effective_start < window_start:
            effective_start = window_start
            status = "window_limited"
    if effective_start > end_ms:
        return [], {
            "status": "window_limited",
            "records": 0,
            "source_endpoint": endpoint,
            "effective_start_ms": effective_start,
        }

    observations: list[dict[str, Any]] = []
    cursor = effective_start
    pages = 0
    while cursor <= end_ms and pages < 1000:
        if metric == "funding_rate":
            rows = await fetch(
                symbol, start_ms=cursor, end_ms=end_ms, limit=page_size
            )
        else:
            rows = await fetch(
                symbol,
                period,
                start_ms=cursor,
                end_ms=end_ms,
                limit=page_size,
            )
        pages += 1
        if not isinstance(rows, list):
            raise ValueError(f"{metric} response is not an array")
        if not rows:
            break
        parsed = sorted(
            (row for row in rows if isinstance(row, dict)),
            key=lambda row: int(row.get(time_field, 0)),
        )
        latest_event = cursor - 1
        for row in parsed:
            event_time = _event_time_ms(row, time_field)
            if event_time < effective_start or event_time > end_ms:
                continue
            latest_event = max(latest_event, event_time)
            normalized = _normalize_binance(metric, row)
            observations.append(
                {
                    "market": "usdm",
                    "exchange": "binance",
                    "symbol": symbol,
                    "metric": metric,
                    "period": "" if metric == "funding_rate" else period,
                    "event_time_ms": event_time,
                    "fetched_at_ms": fetched_at_ms,
                    "data_kind": "historical",
                    "value": normalized,
                    "raw": row,
                    "source_endpoint": endpoint,
                    "quality": "ok" if all(value is not None for value in normalized.values()) else "partial",
                }
            )
        if len(rows) < page_size or latest_event < cursor:
            break
        next_cursor = latest_event + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        await asyncio.sleep(0.05)

    if status == "ok" and not observations:
        status = "no_records"
    return observations, {
        "status": status,
        "records": len(observations),
        "pages": pages,
        "source_endpoint": endpoint,
        "effective_start_ms": effective_start,
    }


async def _collect_gate_history(
    provider: Any,
    *,
    symbol: str,
    period: str,
    start_ms: int,
    end_ms: int,
    fetched_at_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Fetch Gate funding and contract statistics once per page, then fan out metrics."""
    start_s = start_ms // 1000
    end_s = end_ms // 1000
    observations: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    funding_endpoint = "/futures/usdt/funding_rate"
    funding_cursor = start_s
    funding_pages = 0
    funding_count = 0
    while funding_cursor <= end_s and funding_pages < 1000:
        rows = await provider.funding_rate_history_range(
            symbol, from_s=funding_cursor, to_s=end_s, limit=100
        )
        funding_pages += 1
        if not isinstance(rows, list) or not rows:
            break
        parsed = sorted(
            (row for row in rows if isinstance(row, dict)),
            key=lambda row: int(row.get("t", 0)),
        )
        latest_event = funding_cursor - 1
        for row in parsed:
            event_s = int(row.get("t", 0))
            event_ms = event_s * 1000
            if event_ms < start_ms or event_ms > end_ms:
                continue
            latest_event = max(latest_event, event_s)
            observations.append(
                {
                    "market": "usdm",
                    "exchange": "gate",
                    "symbol": symbol,
                    "metric": "funding_rate",
                    "period": "",
                    "event_time_ms": event_ms,
                    "fetched_at_ms": fetched_at_ms,
                    "data_kind": "historical",
                    "value": {"funding_rate": row.get("r")},
                    "raw": row,
                    "source_endpoint": funding_endpoint,
                    "quality": "ok" if row.get("r") is not None else "partial",
                }
            )
            funding_count += 1
        if len(rows) < 100 or latest_event < funding_cursor:
            break
        funding_cursor = latest_event + 1
        await asyncio.sleep(0.05)
    summaries["funding_rate"] = {
        "status": "ok" if funding_count else "no_records",
        "records": funding_count,
        "pages": funding_pages,
        "source_endpoint": funding_endpoint,
    }

    stats_endpoint = "/futures/usdt/contract_stats"
    interval_seconds = _GATE_PERIOD_SECONDS[period]
    stats_cursor = start_s
    stats_pages = 0
    metric_counts: dict[str, int] = {}
    while stats_cursor <= end_s and stats_pages < 1000:
        rows = await provider.contract_stats_range(
            symbol, period, from_s=stats_cursor, limit=200
        )
        stats_pages += 1
        if not isinstance(rows, list) or not rows:
            break
        parsed = sorted(
            (row for row in rows if isinstance(row, dict)),
            key=lambda row: int(row.get("time", 0)),
        )
        latest_event = stats_cursor - 1
        for row in parsed:
            event_s = int(row.get("time", 0))
            event_ms = event_s * 1000
            if event_ms < start_ms or event_ms > end_ms:
                continue
            latest_event = max(latest_event, event_s)
            for metric, normalized in _gate_stat_metrics(row).items():
                observations.append(
                    {
                        "market": "usdm",
                        "exchange": "gate",
                        "symbol": symbol,
                        "metric": metric,
                        "period": period,
                        "event_time_ms": event_ms,
                        "fetched_at_ms": fetched_at_ms,
                        "data_kind": "historical",
                        "value": normalized,
                        "raw": row,
                        "source_endpoint": stats_endpoint,
                        "quality": "ok",
                    }
                )
                metric_counts[metric] = metric_counts.get(metric, 0) + 1
        if len(rows) < 200 or latest_event < stats_cursor:
            break
        stats_cursor = latest_event + interval_seconds
        await asyncio.sleep(0.05)

    for metric, count in metric_counts.items():
        summaries[metric] = {
            "status": "ok" if count else "no_records",
            "records": count,
            "pages": stats_pages,
            "source_endpoint": stats_endpoint,
        }
    for metric in (
        "open_interest_contracts",
        "open_interest_usd",
        "global_long_short_ratio",
        "taker_long_short_ratio",
        "top_trader_long_short_account_ratio",
        "top_trader_long_short_position_ratio",
        "long_liquidation_contracts",
        "short_liquidation_contracts",
        "long_liquidation_usd",
        "short_liquidation_usd",
        "mark_price",
        "long_users",
        "short_users",
        "top_long_size",
        "top_short_size",
        "long_taker_size",
        "short_taker_size",
    ):
        summaries.setdefault(
            metric,
            {
                "status": "no_records",
                "records": 0,
                "pages": stats_pages,
                "source_endpoint": stats_endpoint,
            },
        )
    return observations, summaries


async def collect_derivatives_history(
    market_service: Any,
    *,
    exchange: str,
    symbol: str,
    period: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    """Collect historical futures metrics without substituting live snapshots."""
    if start_ms < 0 or end_ms < start_ms:
        raise ValueError("Invalid derivatives history time range")
    if exchange not in {"binance", "gate"}:
        raise ValueError(f"Historical derivatives data is not supported for {exchange}")
    if exchange == "binance" and period not in _BINANCE_PERIODS:
        raise ValueError(f"Unsupported Binance derivatives period: {period}")
    if exchange == "gate" and period not in _GATE_PERIOD_SECONDS:
        raise ValueError(f"Unsupported Gate derivatives period: {period}")

    provider = market_service._provider("usdm", exchange)
    symbol = symbol.strip().upper()
    fetched_at_ms = int(time.time() * 1000)
    metrics: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []

    if exchange == "binance":
        metric_names = [
            "funding_rate",
            "open_interest",
            "global_long_short_ratio",
            "taker_long_short_ratio",
            "top_long_short_account_ratio",
            "top_long_short_position_ratio",
        ]
        results = await asyncio.gather(
            *[
                _collect_binance_metric(
                    provider,
                    symbol=symbol,
                    metric=metric,
                    period=period,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    fetched_at_ms=fetched_at_ms,
                )
                for metric in metric_names
            ],
            return_exceptions=True,
        )
    else:
        try:
            gate_observations, gate_metrics = await _collect_gate_history(
                provider,
                symbol=symbol,
                period=period,
                start_ms=start_ms,
                end_ms=end_ms,
                fetched_at_ms=fetched_at_ms,
            )
            observations.extend(gate_observations)
            metrics.update(gate_metrics)
        except Exception as exc:  # noqa: BLE001
            metrics["gate_history"] = {
                "status": "unavailable",
                "records": 0,
                "error": str(exc)[:240],
            }

    if exchange == "binance":
        for metric, result in zip(metric_names, results, strict=True):
            if isinstance(result, BaseException):
                metrics[metric] = {
                    "status": "unavailable",
                    "records": 0,
                    "error": str(result)[:240],
                }
                continue
            metric_observations, summary = result
            observations.extend(metric_observations)
            metrics[metric] = summary

    has_records = any(item.get("records", 0) for item in metrics.values())
    has_unavailable = any(item.get("status") == "unavailable" for item in metrics.values())
    has_limited_window = any(item.get("status") == "window_limited" for item in metrics.values())
    if has_records and (has_unavailable or has_limited_window):
        run_status = "partial"
    elif has_records:
        run_status = "completed"
    elif has_unavailable:
        run_status = "unavailable"
    else:
        run_status = "no_data"

    return {
        "run_id": str(uuid.uuid4()),
        "market": "usdm",
        "exchange": exchange,
        "symbol": symbol,
        "period": period,
        "start_time_ms": start_ms,
        "end_time_ms": end_ms,
        "fetched_at_ms": fetched_at_ms,
        "status": run_status,
        "metrics": metrics,
        "observations": observations,
        "observation_count": len(observations),
        "note": (
            "历史记录仅来自交易所历史接口；Binance OI/多空比类统计仅提供最近约 30 天。"
            "实时 FuturesContext 快照不会被用于填补历史空白。"
        ),
    }
