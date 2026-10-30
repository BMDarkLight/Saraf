"""Binance public market data: a global reference price for USDT markets."""

from ...config import Settings
from ..registry import register
from .client import BinanceClient

__all__ = ["BinanceClient"]


@register("binance", BinanceClient)
def _make(settings: Settings) -> BinanceClient:
    return BinanceClient()
