"""Nobitex (nobitex.ir): Iranian crypto exchange with IRT (rial) and USDT spot markets. Market data, account and
live spot orders."""

from ...config import Settings
from ..registry import register
from .client import NobitexClient, sign

__all__ = ["NobitexClient", "sign"]


@register("nobitex")
def _make(settings: Settings) -> NobitexClient:
    return NobitexClient(settings.nobitex_api_key, settings.nobitex_private_key, settings.nobitex_token)
