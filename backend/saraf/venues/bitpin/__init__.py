"""Bitpin (bitpin.ir): Iranian crypto exchange with IRT (toman) and USDT spot markets. Market data, account and
live spot orders."""

from ...config import Settings
from ..registry import register
from .client import BitpinClient

__all__ = ["BitpinClient"]


@register("bitpin")
def _make(settings: Settings) -> BitpinClient:
    return BitpinClient(settings.bitpin_api_key, settings.bitpin_secret_key)
