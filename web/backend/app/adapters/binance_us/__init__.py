"""Binance.US Spot public REST adapter."""
from __future__ import annotations

from app.adapters.binance.spot_rest import BinanceSpotREST


class BinanceUSSpotREST(BinanceSpotREST):
    """Binance.US exposes a compatible public Spot REST path surface."""
