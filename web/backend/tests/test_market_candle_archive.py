from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.analysis_history_store import AnalysisHistoryStore


@dataclass
class Candle:
    open_time: int
    close_time: int
    open: str = "100"
    high: str = "102"
    low: str = "98"
    close: str = "101"
    volume: str = "12.5"
    quote_volume: str = "1262.5"
    trades: int | None = 10
    taker_buy_base_volume: str = "6.2"
    taker_buy_quote_volume: str = "626.2"
    closed: bool = True
    volume_unit: str = "base_asset"
    contract_volume: str | None = None


def test_market_candles_are_persisted_idempotently_and_scoped_by_source(tmp_path: Path):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    candles = [
        Candle(60_000, 119_999),
        Candle(120_000, 179_999),
        Candle(180_000, 239_999, closed=False),
    ]

    assert store.upsert_market_candles(
        market="spot",
        exchange="binance",
        symbol="btcusdt",
        timeframe="1m",
        candles=candles,
    ) == 2
    # Re-importing the same closed bars updates their source values but does not
    # create duplicate rows or change the instrument identity.
    assert store.upsert_market_candles(
        market="spot",
        exchange="binance",
        symbol="BTCUSDT",
        timeframe="1m",
        candles=candles,
    ) == 2

    coverage = store.market_candle_coverage(
        market="spot", exchange="binance", symbol="BTCUSDT", timeframe="1m"
    )
    assert coverage == {
        "candle_count": 2,
        "oldest_open_time_ms": 60_000,
        "newest_open_time_ms": 120_000,
    }

    rows = store.list_market_candles(
        market="spot", exchange="binance", symbol="BTCUSDT", timeframe="1m"
    )
    assert [row["open_time"] for row in rows] == [60_000, 120_000]
    assert rows[0]["volume"] == "12.5"
    assert rows[0]["quote_volume"] == "1262.5"
    assert rows[0]["closed"] is True

    # Identical symbols from another venue are a different market-data series.
    store.upsert_market_candles(
        market="spot",
        exchange="binance_us",
        symbol="BTCUSDT",
        timeframe="1m",
        candles=[Candle(60_000, 119_999, close="99")],
    )
    stats = store.market_candle_stats(symbol="BTCUSDT", timeframe="1m")
    assert len(stats) == 2
    assert {item["exchange"] for item in stats} == {"binance", "binance_us"}


def test_market_candle_archive_can_resume_from_oldest_saved_timestamp(tmp_path: Path):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    store.upsert_market_candles(
        market="usdm",
        exchange="gate",
        symbol="NEARUSDT",
        timeframe="15m",
        candles=[
            Candle(900_000, 1_799_999, contract_volume="100"),
            Candle(1_800_000, 2_699_999, contract_volume="120"),
        ],
    )

    coverage = store.market_candle_coverage(
        market="usdm", exchange="gate", symbol="NEARUSDT", timeframe="15m"
    )
    assert coverage["candle_count"] == 2
    assert coverage["oldest_open_time_ms"] == 900_000
    assert coverage["newest_open_time_ms"] == 1_800_000



def test_similar_segments_excludes_samples_whose_future_label_overlaps_now(tmp_path: Path):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    base = {
        "market": "usdm",
        "exchange": "gate",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "regime": "range",
        "segment_start_ms": 100,
        "lookback_bars": 24,
        "horizon_bars": 12,
        "feature_schema_version": "pa-segment-features-v1",
        "label_schema_version": "forward-outcome-v1",
        "features": {"net_return_pct": 0.2},
    }
    store.upsert_segments([
        {
            **base,
            "sample_id": "fully-observed",
            "decision_time_ms": 200,
            "labels": {"forward_direction": "up", "future_end_ms": 800},
        },
        {
            **base,
            "sample_id": "future-overlap",
            "decision_time_ms": 400,
            "labels": {"forward_direction": "down", "future_end_ms": 1200},
        },
    ])

    result = store.similar_segments(
        market="usdm",
        exchange="gate",
        symbol="BTCUSDT",
        timeframe="15m",
        features={"regime": "range", "features": {"net_return_pct": 0.2}},
        before_ms=1000,
        limit=5,
    )

    assert [item["sample_id"] for item in result] == ["fully-observed"]
