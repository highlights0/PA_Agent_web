from __future__ import annotations

from app.adapters.binance.normalizer import normalize_klines, to_core_bars


def test_to_core_bars_uses_newest_first_and_only_closed_bars():
    rows = [
        [1_000, "1", "2", "0.5", "1.5", "10", 1_999, "15", 2, "5", "7.5", "0"],
        [2_000, "1.5", "2.5", "1", "2", "12", 2_999, "24", 3, "6", "12", "0"],
        [3_000, "2", "3", "1.5", "2.5", "14", 3_999, "35", 4, "7", "17.5", "0"],
    ]
    klines = normalize_klines(rows, now_ms=3_500)
    bars = to_core_bars(klines)
    assert len(bars) == 2
    assert bars[0].seq == 1
    assert bars[0].ts_open == 2_000
    assert bars[1].seq == 2
    assert all(bar.closed for bar in bars)
