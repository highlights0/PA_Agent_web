import pytest

from app.services.provider_registry import ProviderRegistry, UnsupportedMarketProvider


class Stub:
    pass


def test_registry_exposes_spot_and_futures_capabilities_separately():
    registry = ProviderRegistry(
        binance_spot=Stub(),
        binance_futures=Stub(),
        binance_us_spot=Stub(),
        coinbase_spot=Stub(),
    )
    assert registry.get("coinbase", "spot") is not None
    assert registry.get("binance_us", "spot") is not None
    with pytest.raises(UnsupportedMarketProvider):
        registry.get("coinbase", "usdm")
