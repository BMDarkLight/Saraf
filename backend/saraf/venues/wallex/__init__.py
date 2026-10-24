"""Wallex (wallex.ir): Iranian crypto exchange with TMN and USDT spot markets. Full adapter: market data,
account, live spot orders; leverage markets for paper futures."""

from ...config import Settings
from ..registry import register
from .client import WallexClient, WallexError

__all__ = ["WallexClient", "WallexError"]


@register("wallex")
def _make(settings: Settings) -> WallexClient:
    return WallexClient(settings.wallex_base_url, settings.wallex_api_key,
                        sub_account=settings.wallex_sub_account_client_id)
