from fastapi import HTTPException

from app.adapters.binance.client import BinanceAPIError
from app.api.v1.market import _translate_error


def test_region_restriction_451_has_actionable_message():
    error = _translate_error(
        BinanceAPIError("restricted location", status_code=451)
    )
    assert isinstance(error, HTTPException)
    assert error.status_code == 502
    assert "HTTP 451" in error.detail
    assert "region or IP" in error.detail


def test_forbidden_403_is_distinguished_from_invalid_symbol():
    error = _translate_error(
        BinanceAPIError("Forbidden", status_code=403)
    )
    assert "HTTP 403" in error.detail


def test_bad_request_includes_binance_error_code_and_message():
    error = _translate_error(
        BinanceAPIError("Invalid symbol.", status_code=400, error_code=-1121)
    )
    assert "HTTP 400" in error.detail
    assert "code -1121" in error.detail
    assert "Invalid symbol" in error.detail


def test_rate_limit_preserves_retry_after():
    error = _translate_error(
        BinanceAPIError("too many requests", status_code=429, retry_after="10")
    )
    assert error.status_code == 503
    assert error.headers["Retry-After"] == "10"
