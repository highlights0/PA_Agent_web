from app.services.crypto_context import CryptoMarketContext, render_crypto_context


def test_context_marks_missing_values_as_unavailable_not_zero():
    context = CryptoMarketContext(
        market_type="spot",
        symbol="BTCUSDT",
        data_timestamp=123456,
        data_quality={"derivatives": "not_applicable_for_spot"},
    )
    rendered = render_crypto_context(context)
    assert "not_applicable_for_spot" in rendered
    assert '"funding_rate": null' in rendered
    assert '"open_interest": null' in rendered
