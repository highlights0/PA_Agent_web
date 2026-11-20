"""Leakage-safe PA market segment samples and chronological evaluation splits.

Features are computed only from closed bars at or before decision_time_ms.
Labels are calculated from subsequent bars and are stored separately; they are
never merged into the feature dictionary or passed to the PA prompt.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from typing import Any, Iterable

FEATURE_SCHEMA_VERSION = "pa-segment-features-v1"
LABEL_SCHEMA_VERSION = "forward-outcome-v1"
DEFAULT_LOOKBACK_BARS = 24
DEFAULT_HORIZON_BARS = 12
_INTERVAL_MS = {
    "1m": 60000, "3m": 180000, "5m": 300000,
    "15m": 900000, "30m": 1800000,
    "1h": 3600000, "2h": 7200000, "4h": 14400000,
    "6h": 21600000, "8h": 28800000, "12h": 43200000,
    "1d": 86400000, "3d": 259200000, "7d": 604800000,
}


def _continuous(bars: list[Any], expected_ms: int) -> bool:
    """Reject gaps to keep future labels aligned with the requested horizon."""
    times = [_time(bar, "open_time") or _time(bar, "ts_open") for bar in bars]
    return bool(times) and all(
        later - earlier == expected_ms for earlier, later in zip(times, times[1:])
    )



def _value(bar: Any, name: str, default: Any = None) -> Any:
    if isinstance(bar, dict):
        return bar.get(name, default)
    return getattr(bar, name, default)


def _number(bar: Any, name: str, default: float = 0.0) -> float:
    try:
        value = float(_value(bar, name, default))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _time(bar: Any, name: str) -> int:
    return int(_number(bar, name, 0))


def _returns(closes: list[float]) -> list[float]:
    return [
        (current / previous) - 1.0 if previous > 0 else 0.0
        for previous, current in zip(closes, closes[1:])
    ]


def _classify_regime(bars: list[Any], lookback_bars: int) -> tuple[str, dict[str, float]]:
    window = bars[-lookback_bars:]
    opens = [_number(bar, "open") for bar in window]
    highs = [_number(bar, "high") for bar in window]
    lows = [_number(bar, "low") for bar in window]
    closes = [_number(bar, "close") for bar in window]
    volumes = [_number(bar, "volume") for bar in window]
    first_close = closes[0] if closes else 0.0
    last_close = closes[-1] if closes else 0.0
    if first_close <= 0 or last_close <= 0:
        raise ValueError("Segment window contains invalid close prices")

    returns = _returns(closes)
    net_return = (last_close / first_close - 1.0) * 100.0
    path_return = sum(abs(value) for value in returns)
    efficiency = min(1.0, abs(last_close / first_close - 1.0) / path_return) if path_return else 0.0
    price_range = (max(highs) - min(lows)) / first_close * 100.0 if first_close else 0.0
    bullish_ratio = sum(1 for bar in window if _number(bar, "close") > _number(bar, "open")) / len(window)
    bearish_ratio = sum(1 for bar in window if _number(bar, "close") < _number(bar, "open")) / len(window)
    mean_volume = statistics.fmean(volumes) if volumes else 0.0
    baseline_volume = statistics.fmean(volumes[:-4]) if len(volumes) > 4 else mean_volume
    volume_ratio = statistics.fmean(volumes[-4:]) / baseline_volume if baseline_volume > 0 else 1.0

    peak = closes[0]
    max_drawdown = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - close) / peak * 100.0)

    volatility = (
        statistics.pstdev(returns) * math.sqrt(max(1, len(returns))) * 100.0
        if len(returns) > 1 else 0.0
    )
    upper_wicks: list[float] = []
    lower_wicks: list[float] = []
    for bar in window:
        high, low = _number(bar, "high"), _number(bar, "low")
        open_price, close = _number(bar, "open"), _number(bar, "close")
        span = max(0.0, high - low)
        upper_wicks.append(max(0.0, high - max(open_price, close)) / span if span else 0.0)
        lower_wicks.append(max(0.0, min(open_price, close) - low) / span if span else 0.0)

    previous = window[:-1]
    previous_high = max((_number(bar, "high") for bar in previous), default=max(highs[:-1] or highs))
    previous_low = min((_number(bar, "low") for bar in previous), default=min(lows[:-1] or lows))
    breakout_up = last_close > previous_high * 1.001
    breakout_down = last_close < previous_low * 0.999

    prior_closes = closes[:-4]
    recent_closes = closes[-4:]
    prior_return = (
        (prior_closes[-1] / prior_closes[0] - 1.0) * 100.0
        if len(prior_closes) > 1 and prior_closes[0] > 0 else 0.0
    )
    recent_return = (
        (recent_closes[-1] / recent_closes[0] - 1.0) * 100.0
        if len(recent_closes) > 1 and recent_closes[0] > 0 else 0.0
    )

    if breakout_up:
        regime = "upside_breakout"
    elif breakout_down:
        regime = "downside_breakout"
    elif prior_return <= -1.8 and recent_return >= 1.0:
        regime = "bullish_reversal"
    elif prior_return >= 1.8 and recent_return <= -1.0:
        regime = "bearish_reversal"
    elif abs(prior_return) >= 2.5 and efficiency >= 0.48 and (
        (prior_return > 0 and recent_return < -0.8)
        or (prior_return < 0 and recent_return > 0.8)
    ):
        regime = "trend_end_candidate"
    elif net_return >= 2.0 and efficiency >= 0.42:
        regime = "bullish_trend"
    elif net_return <= -2.0 and efficiency >= 0.42:
        regime = "bearish_trend"
    elif abs(net_return) <= 1.2 and efficiency <= 0.34:
        regime = "range"
    else:
        regime = "mixed"

    features = {
        "net_return_pct": round(net_return, 6),
        "path_efficiency": round(efficiency, 6),
        "range_pct": round(price_range, 6),
        "volatility_pct": round(volatility, 6),
        "bullish_bar_ratio": round(bullish_ratio, 6),
        "bearish_bar_ratio": round(bearish_ratio, 6),
        "volume_ratio_recent4_to_prior": round(volume_ratio, 6),
        "max_drawdown_pct": round(max_drawdown, 6),
        "upper_wick_ratio": round(statistics.fmean(upper_wicks), 6),
        "lower_wick_ratio": round(statistics.fmean(lower_wicks), 6),
        "breakout_up": float(breakout_up),
        "breakout_down": float(breakout_down),
        "prior_return_pct": round(prior_return, 6),
        "recent_return_pct": round(recent_return, 6),
    }
    return regime, features


def build_current_features(
    bars: Iterable[Any],
    *,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
) -> dict[str, Any] | None:
    closed = [bar for bar in bars if bool(_value(bar, "closed", True))]
    closed.sort(key=lambda bar: _time(bar, "open_time") or _time(bar, "ts_open"))
    if len(closed) < lookback_bars:
        return None
    regime, features = _classify_regime(closed, lookback_bars)
    last = closed[-1]
    return {
        "regime": regime,
        "features": features,
        "decision_time_ms": _time(last, "close_time") or _time(last, "ts_open"),
        "segment_start_ms": _time(closed[-lookback_bars], "open_time") or _time(closed[-lookback_bars], "ts_open"),
        "lookback_bars": lookback_bars,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def build_segment_samples(
    bars: Iterable[Any],
    *,
    market: str,
    exchange: str,
    symbol: str,
    timeframe: str,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
    label_threshold_pct: float = 0.75,
) -> list[dict[str, Any]]:
    """Create one as-of sample per eligible closed bar with future-only labels."""
    closed = [bar for bar in bars if bool(_value(bar, "closed", True))]
    closed.sort(key=lambda bar: _time(bar, "open_time") or _time(bar, "ts_open"))
    if len(closed) < lookback_bars + horizon_bars + 1:
        return []

    expected_ms = _INTERVAL_MS.get(timeframe)
    if expected_ms is None:
        return []
    samples: list[dict[str, Any]] = []
    current_start = lookback_bars - 1
    previous_regime: str | None = None
    for index in range(lookback_bars - 1, len(closed) - horizon_bars):
        past = closed[index - lookback_bars + 1 : index + 1]
        future = closed[index + 1 : index + 1 + horizon_bars]
        if not _continuous(past + future, expected_ms):
            previous_regime = None
            continue
        regime, features = _classify_regime(past, lookback_bars)
        if regime != previous_regime:
            current_start = index
        previous_regime = regime

        entry = _number(closed[index], "close")
        if entry <= 0 or len(future) != horizon_bars or not all(bool(_value(bar, "closed", True)) for bar in future):
            continue
        future_closes = [_number(bar, "close") for bar in future]
        future_highs = [_number(bar, "high") for bar in future]
        future_lows = [_number(bar, "low") for bar in future]
        if not future_closes or any(value <= 0 for value in future_closes):
            continue
        forward_return = (future_closes[-1] / entry - 1.0) * 100.0
        threshold = max(float(label_threshold_pct), float(features["volatility_pct"]) * 0.15)
        direction = "up" if forward_return >= threshold else "down" if forward_return <= -threshold else "flat"
        decision_time = _time(closed[index], "close_time") or _time(closed[index], "ts_open")
        future_start = _time(future[0], "open_time") or _time(future[0], "ts_open")
        future_end = _time(future[-1], "close_time") or _time(future[-1], "ts_open")
        segment_start = _time(closed[current_start], "open_time") or _time(closed[current_start], "ts_open")
        sample_key = "|".join(
            (
                market, exchange, symbol, timeframe, str(decision_time),
                FEATURE_SCHEMA_VERSION, LABEL_SCHEMA_VERSION, str(lookback_bars), str(horizon_bars),
            )
        )
        sample_id = hashlib.sha256(sample_key.encode("utf-8")).hexdigest()
        labels = {
            "forward_direction": direction,
            "forward_return_pct": round(forward_return, 6),
            "future_max_up_pct": round((max(future_highs) / entry - 1.0) * 100.0, 6),
            "future_max_down_pct": round((min(future_lows) / entry - 1.0) * 100.0, 6),
            "horizon_bars": horizon_bars,
            "threshold_pct": round(threshold, 6),
            "future_start_ms": future_start,
            "future_end_ms": future_end,
        }
        samples.append(
            {
                "sample_id": sample_id,
                "market": market,
                "exchange": exchange,
                "symbol": symbol,
                "timeframe": timeframe,
                "regime": regime,
                "decision_time_ms": decision_time,
                "segment_start_ms": segment_start,
                "lookback_bars": lookback_bars,
                "horizon_bars": horizon_bars,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "label_schema_version": LABEL_SCHEMA_VERSION,
                "features": features,
                "labels": labels,
            }
        )
    return samples


def assign_temporal_splits(
    samples: list[dict[str, Any]],
    *,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    embargo_bars: int | None = None,
) -> list[dict[str, Any]]:
    """Assign chronological 60/20/20 splits with label-horizon embargo gaps."""
    if not 0 < train_ratio < 1 or not 0 < validation_ratio < 1 or train_ratio + validation_ratio >= 1:
        raise ValueError("Invalid temporal split ratios")
    ordered = sorted(samples, key=lambda item: int(item["decision_time_ms"]))
    if not ordered:
        return []
    horizon = max(int(item.get("horizon_bars", DEFAULT_HORIZON_BARS)) for item in ordered)
    embargo = max(horizon, int(embargo_bars or horizon))
    count = len(ordered)
    train_cut = int(count * train_ratio)
    validation_cut = int(count * (train_ratio + validation_ratio))
    if count < (embargo * 2 + 30):
        return [{**item, "split": "unassigned", "embargo_bars": embargo} for item in ordered]

    assigned: list[dict[str, Any]] = []
    for index, item in enumerate(ordered):
        if index < max(0, train_cut - embargo):
            split = "train"
        elif index >= train_cut + embargo and index < max(train_cut + embargo, validation_cut - embargo):
            split = "validation"
        elif index >= validation_cut + embargo:
            split = "test"
        else:
            split = "embargo"
        assigned.append({**item, "split": split, "embargo_bars": embargo})
    return assigned


def similarity_score(left: dict[str, Any], right: dict[str, Any]) -> float:
    """A transparent weighted distance; no learned or hidden accuracy claim."""
    left_features = left.get("features", left)
    right_features = right.get("features", right)
    scales = {
        "net_return_pct": 4.0,
        "path_efficiency": 0.5,
        "range_pct": 5.0,
        "volatility_pct": 3.0,
        "bullish_bar_ratio": 0.5,
        "bearish_bar_ratio": 0.5,
        "volume_ratio_recent4_to_prior": 2.0,
        "max_drawdown_pct": 5.0,
        "upper_wick_ratio": 0.5,
        "lower_wick_ratio": 0.5,
        "prior_return_pct": 4.0,
        "recent_return_pct": 3.0,
    }
    distance = 0.0
    weight_total = 0.0
    for key, scale in scales.items():
        if key not in left_features or key not in right_features:
            continue
        distance += min(3.0, abs(float(left_features[key]) - float(right_features[key])) / scale)
        weight_total += 1.0
    if weight_total == 0:
        return 0.0
    normalized = distance / weight_total
    regime_penalty = 0.0 if left.get("regime") == right.get("regime") else 0.35
    return round(1.0 / (1.0 + normalized + regime_penalty), 6)
