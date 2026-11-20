from __future__ import annotations

import asyncio

from app.services.baseline_evaluation import evaluate_rule_baseline
from app.services.historical_backfill import backfill_gate_candles
from app.services.segmenter import build_segment_samples


class FakeGate:
    def __init__(self, *, missing: set[int] | None = None):
        self.requests: list[dict] = []
        self.missing = missing or set()

    async def contract_info(self, symbol: str):
        assert symbol == "BTCUSDT"
        return {"quanto_multiplier": "0.001"}

    async def klines_range(self, symbol: str, interval: str, *, from_s: int, to_s: int):
        assert symbol == "BTCUSDT"
        assert interval == "1m"
        self.requests.append({"from": from_s, "to": to_s})
        assert (to_s - from_s) // 60 + 1 <= 2000
        return [
            {"t": ts, "v": "1", "o": "100", "h": "102", "l": "98", "c": "100", "sum": "100"}
            for ts in range(from_s, to_s + 1, 60)
            if ts not in self.missing
        ]


def test_gate_backfill_uses_time_windows_and_no_limit(monkeypatch):
    monkeypatch.setattr("app.services.historical_backfill.time.time", lambda: 6_000_035)
    gate = FakeGate()
    result = asyncio.run(backfill_gate_candles(
        gate, symbol="BTCUSDT", timeframe="1m", requested_bars=2200
    ))
    assert result["closed_bars"] == 2200
    assert result["pages"] >= 3
    assert result["gaps"] == 0
    assert all(len(request) == 2 for request in gate.requests)
    times = [bar.open_time for bar in result["bars"]]
    assert times == sorted(set(times))
    assert result["bars"][-1].closed
    assert result["bars"][0].volume == "0.001"


def test_backfill_gaps_are_not_filled_or_labeled_across(monkeypatch):
    monkeypatch.setattr("app.services.historical_backfill.time.time", lambda: 6_000_035)
    target = (6_000_035 // 60) * 60 - 60 - 45 * 60
    gate = FakeGate(missing={target})
    result = asyncio.run(backfill_gate_candles(
        gate, symbol="BTCUSDT", timeframe="1m", requested_bars=150
    ))
    assert result["gaps"] >= 1
    samples = build_segment_samples(
        result["bars"], market="usdm", exchange="gate",
        symbol="BTCUSDT", timeframe="1m", lookback_bars=24, horizon_bars=12,
    )
    assert all(
        not (
            sample["segment_start_ms"] <= target * 1000 <= sample["labels"]["future_end_ms"]
        )
        for sample in samples
    )


def _sample(split: str, index: int, regime: str, outcome: str) -> dict:
    return {
        "split": split,
        "decision_time_ms": index * 900000,
        "regime": regime,
        "labels": {"forward_direction": outcome},
    }


def test_baseline_reports_only_held_out_fixed_rule_metrics():
    samples = (
        [_sample("train", i, "bullish_trend", "up") for i in range(80)]
        + [_sample("validation", 200 + i, "bearish_trend", "down") for i in range(20)]
        + [_sample("test", 500 + i, "bearish_trend", "down") for i in range(30)]
    )
    report = evaluate_rule_baseline(samples)
    assert report["status"] == "evaluated"
    assert report["majority_class_from_train"] == "up"
    assert report["train_count"] == 80
    assert report["test_count"] == 30
    assert report["rule_baseline"]["accuracy"] == 1.0
    assert report["train_majority_baseline"]["accuracy"] == 0.0


def test_baseline_never_fabricates_accuracy_when_samples_insufficient():
    samples = [_sample("train", i, "range", "flat") for i in range(10)]
    report = evaluate_rule_baseline(samples)
    assert report["status"] == "insufficient_data"
    assert "rule_baseline" not in report



def test_gate_backfill_resume_stops_before_oldest_archived_open_time(monkeypatch):
    monkeypatch.setattr("app.services.historical_backfill.time.time", lambda: 6_000_035)
    gate = FakeGate()
    oldest_archived_open_ms = 120 * 60_000

    result = asyncio.run(
        backfill_gate_candles(
            gate,
            symbol="BTCUSDT",
            timeframe="1m",
            requested_bars=80,
            end_ms=oldest_archived_open_ms + 1,
        )
    )

    assert result["closed_bars"] == 80
    assert result["bars"][-1].open_time < oldest_archived_open_ms
    assert gate.requests[0]["to"] == 119 * 60
