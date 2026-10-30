"""Ramzinex (ramzinex.com): Iranian crypto exchange with IRR (rial) and USDT spot markets. Market data, account
and live spot orders."""

from ...config import Settings
from ..registry import register
from .client import RamzinexClient

__all__ = ["RamzinexClient"]


@register("ramzinex", RamzinexClient)
def _make(settings: Settings) -> RamzinexClient:
    return RamzinexClient(settings.ramzinex_api_key, settings.ramzinex_secret, data_dir=settings.data_dir)
