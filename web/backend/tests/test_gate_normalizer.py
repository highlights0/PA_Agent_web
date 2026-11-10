from __future__ import annotations

import pytest

from app.adapters.binance.normalizer import normalize_gate_klines


def test_gate_candles_convert_contract_volume_to_base_asset_volume():
    rows = [
        {"t": 1_700_000_900, "v": "20", "o": "101", "h": "103", "l": "100", "c": "102", "sum": "2040"},
        {"t": 1_700_000_000, "v": "10", "o": "99", "h": "102", "l": "98", "c": "101", "sum": "1010"},
    ]
    result = normalize_gate_klines(
        rows,
        interval="15m",
        quanto_multiplier="0.001",
        now_ms=1_700_002_000_000,
    )

    assert [item.open_time for item in result] == [1_700_000_000_000, 1_700_000_900_000]
    assert result[0].close_time == 1_700_000_899_999
    assert result[0].volume == "0.010"
    assert result[0].contract_volume == "10"
    assert result[0].quote_volume == "1010"
    assert result[0].exchange == "gate"
    assert result[0].market_type == "usdm"
    assert result[0].closed is True


def test_gate_normalizer_rejects_missing_multiplier_and_bad_rows():
    with pytest.raises(ValueError, match="quanto_multiplier"):
        normalize_gate_klines(
            [{"t": 1, "v": "1", "o": "1", "h": "1", "l": "1", "c": "1", "sum": "1"}],
            interval="1m",
            quanto_multiplier="0",
        )
    with pytest.raises(ValueError, match="Malformed"):
        normalize_gate_klines(
            [["bad"]],
            interval="1m",
            quanto_multiplier="0.001",
        )


def test_gate_normalizer_rejects_unsupported_interval():
    with pytest.raises(ValueError, match="Unsupported Gate"):
        normalize_gate_klines(
            [{"t": 1, "v": "1", "o": "1", "h": "1", "l": "1", "c": "1", "sum": "1"}],
            interval="3m",
            quanto_multiplier="0.001",
        )
