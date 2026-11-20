from __future__ import annotations

from app.services.segmenter import (
    assign_temporal_splits,
    build_current_features,
    build_segment_samples,
    similarity_score,
)


def _bars(count=180, *, jump_at=None, future_jump=0.0):
    bars = []
    price = 100.0
    for index in range(count):
        if jump_at is not None and index >= jump_at:
            price *= 1.001
        if future_jump and index >= count - 12:
            price *= 1.02
        open_price = price * 0.999
        close = price
        bars.append(
            {
                "open_time": index * 60_000,
                "close_time": (index + 1) * 60_000 - 1,
                "open": str(open_price),
                "high": str(price * 1.002),
                "low": str(price * 0.998),
                "close": str(close),
                "volume": "100",
                "closed": True,
            }
        )
    return bars


def test_features_are_as_of_time_and_future_bars_only_change_labels():
    bars = _bars(180, jump_at=80)
    samples = build_segment_samples(
        bars,
        market="usdm",
        exchange="gate",
        symbol="BTCUSDT",
        timeframe="1m",
        lookback_bars=24,
        horizon_bars=12,
    )
    assert samples
    sample = samples[40]
    decision_index = next(
        index for index, bar in enumerate(bars)
        if bar["close_time"] == sample["decision_time_ms"]
    )
    current = build_current_features(bars[: decision_index + 1], lookback_bars=24)
    assert current is not None
    assert sample["features"] == current["features"]
    assert sample["decision_time_ms"] == current["decision_time_ms"]

    changed_future = [dict(item) for item in bars]
    for index in range(decision_index + 1, min(len(changed_future), decision_index + 13)):
        changed_future[index]["high"] = str(float(changed_future[index]["high"]) * 1.3)
        changed_future[index]["low"] = str(float(changed_future[index]["low"]) * 0.7)
        changed_future[index]["close"] = str(float(changed_future[index]["close"]) * 1.1)
    changed = build_segment_samples(
        changed_future,
        market="usdm",
        exchange="gate",
        symbol="BTCUSDT",
        timeframe="1m",
        lookback_bars=24,
        horizon_bars=12,
    )
    altered = next(item for item in changed if item["sample_id"] == sample["sample_id"])
    assert altered["features"] == sample["features"]
    assert altered["labels"] != sample["labels"]


def test_future_labels_are_separate_and_never_merged_into_features():
    samples = build_segment_samples(
        _bars(180),
        market="spot",
        exchange="coinbase",
        symbol="BTC-USD",
        timeframe="1m",
    )
    assert samples
    sample = samples[0]
    assert "forward_return_pct" in sample["labels"]
    assert "forward_return_pct" not in sample["features"]
    assert "future_start_ms" not in sample["features"]


def test_open_or_incomplete_bars_are_not_used_for_features_or_labels():
    bars = _bars(100)
    bars[-1]["closed"] = False
    samples = build_segment_samples(
        bars,
        market="usdm",
        exchange="gate",
        symbol="BTCUSDT",
        timeframe="1m",
        lookback_bars=24,
        horizon_bars=12,
    )
    assert samples
    assert all(item["decision_time_ms"] < bars[-1]["close_time"] for item in samples)


def test_temporal_splits_are_chronological_and_leave_embargo_gap():
    samples = [
        {
            "sample_id": str(index),
            "decision_time_ms": index * 60_000,
            "horizon_bars": 12,
        }
        for index in range(240)
    ]
    split = assign_temporal_splits(samples)
    train = [item for item in split if item["split"] == "train"]
    validation = [item for item in split if item["split"] == "validation"]
    test = [item for item in split if item["split"] == "test"]
    assert train and validation and test
    assert max(item["decision_time_ms"] for item in train) < min(item["decision_time_ms"] for item in validation)
    assert max(item["decision_time_ms"] for item in validation) < min(item["decision_time_ms"] for item in test)
    train_last = int(max(item["sample_id"] for item in train))
    validation_first = int(min(item["sample_id"] for item in validation))
    validation_last = int(max(item["sample_id"] for item in validation))
    test_first = int(min(item["sample_id"] for item in test))
    assert validation_first - train_last >= 12
    assert test_first - validation_last >= 12


def test_small_dataset_is_not_misrepresented_as_a_train_validation_test_split():
    split = assign_temporal_splits([
        {"sample_id": str(index), "decision_time_ms": index, "horizon_bars": 12}
        for index in range(40)
    ])
    assert {item["split"] for item in split} == {"unassigned"}


def test_similarity_score_is_bounded_and_prefers_matching_regime():
    features = {
        "regime": "bullish_trend",
        "features": {
            "net_return_pct": 3.0,
            "path_efficiency": 0.7,
            "range_pct": 4.0,
            "volatility_pct": 1.2,
            "bullish_bar_ratio": 0.7,
            "bearish_bar_ratio": 0.2,
            "volume_ratio_recent4_to_prior": 1.2,
            "max_drawdown_pct": 1.0,
            "upper_wick_ratio": 0.2,
            "lower_wick_ratio": 0.2,
            "prior_return_pct": 2.0,
            "recent_return_pct": 1.0,
        },
    }
    same = {"regime": "bullish_trend", "features": dict(features["features"])}
    different = {"regime": "bearish_trend", "features": {**features["features"], "net_return_pct": -4.0}}
    assert similarity_score(features, same) == 1.0
    assert 0 <= similarity_score(features, different) < 1.0
