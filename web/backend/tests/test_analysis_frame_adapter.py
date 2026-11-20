from __future__ import annotations

from app.adapters.binance.normalizer import normalize_klines, to_core_bars
from pa_agent.data.snapshot import build_analysis_frame


def test_binance_closed_candles_build_original_pa_frame():
    rows = []
    base_time = 1_700_000_000_000
    for index in range(80):
        open_time = base_time + index * 900_000
        close = 100 + index * 0.25
        rows.append([
            open_time,
            str(close - 0.1),
            str(close + 1),
            str(close - 1),
            str(close),
            "100",
            open_time + 899_999,
            str(close * 100),
            20,
            "55",
            str(close * 55),
            "0",
        ])

    now_ms = rows[-1][6] + 1
    normalized = normalize_klines(rows, now_ms=now_ms)
    core_bars = to_core_bars(normalized)
    frame = build_analysis_frame(core_bars, 20, "BTCUSDT", "15m", now_ms=now_ms)

    assert frame is not None
    assert frame.symbol == "BTCUSDT"
    assert frame.timeframe == "15m"
    assert len(frame.bars) == 20
    assert frame.bars[0].seq == 1
    assert frame.bars[0].ts_open == rows[-1][0]
    assert all(bar.closed for bar in frame.bars)
