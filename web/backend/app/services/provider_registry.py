"""Exchange provider registry and explicit market capability checks."""
from __future__ import annotations

from typing import Any

from app.adapters.binance.futures_rest import BinanceUSDMREST
from app.adapters.binance.spot_rest import BinanceSpotREST
from app.adapters.binance_us.spot_rest import BinanceUSSpotREST
from app.adapters.coinbase.spot_rest import CoinbaseSpotREST


class UnsupportedMarketProvider(ValueError):
    pass


class ProviderRegistry:
    """Keeps market capabilities explicit; never silently substitutes another market."""

    def __init__(
        self,
        *,
        binance_spot: BinanceSpotREST,
        binance_futures: BinanceUSDMREST,
        binance_us_spot: BinanceUSSpotREST,
        coinbase_spot: CoinbaseSpotREST,
    ) -> None:
        self._providers: dict[tuple[str, str], Any] = {
            ("binance", "spot"): binance_spot,
            ("binance", "usdm"): binance_futures,
            ("binance_us", "spot"): binance_us_spot,
            ("coinbase", "spot"): coinbase_spot,
        }

    @property
    def supported(self) -> dict[str, list[str]]:
        return {
            "binance": ["spot", "usdm"],
            "binance_us": ["spot"],
            "coinbase": ["spot"],
        }

    def get(self, exchange: str, market: str) -> Any:
        provider = self._providers.get((exchange, market))
        if provider is None:
            raise UnsupportedMarketProvider(
                f"{exchange} does not currently provide {market} data in this application"
            )
        return provider
