from app.adapters.binance.normalizer import normalize_coinbase_klines


def test_coinbase_candle_interval_close_time():
    rows = [[1700000000, "99", "102", "100", "101", "10"]]
    result = normalize_coinbase_klines(rows, interval="15m", now_ms=1700001000000)
    assert result[0].close_time == 1700000899999
    assert result[0].open == "100"
    assert result[0].closed is True
