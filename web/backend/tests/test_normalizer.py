from __future__ import annotations

import pytest

from app.adapters.binance.normalizer import normalize_klines


def _row(open_time: int, close_time: int, close: str = "100.25"):
    return [
        open_time, "100.00", "101.00", "99.00", close, "12.5",
        close_time, "1250.0", 42, "6.0", "600.0", "0",
    ]


def test_normalize_klines_marks_closed_and_forming():
    result = normalize_klines(
        [_row(1_000, 1_999), _row(2_000, 2_999)],
        now_ms=2_500,
    )
    assert [item.closed for item in result] == [True, False]
    assert result[0].open == "100.00"
    assert result[0].trades == 42


def test_normalize_klines_rejects_duplicate_or_unsorted_rows():
    with pytest.raises(ValueError, match="oldest-first"):
        normalize_klines(
            [_row(2_000, 2_999), _row(1_000, 1_999)],
            now_ms=9_999,
        )


def test_normalize_klines_rejects_malformed_rows():
    with pytest.raises(ValueError, match="Malformed"):
        normalize_klines([[1, 2, 3]], now_ms=10_000)
