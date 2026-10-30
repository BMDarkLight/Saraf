"""Free-market currency, coin and gold rates (Tetherland, Navasan): read-only `fx` venues."""

from ...config import Settings
from ..registry import register
from .sources import NavasanClient, TetherlandClient

__all__ = ["NavasanClient", "TetherlandClient"]


@register("tetherland", TetherlandClient)
def _tetherland(settings: Settings) -> TetherlandClient:
    return TetherlandClient()


@register("navasan", NavasanClient)
def _navasan(settings: Settings) -> NavasanClient:
    return NavasanClient(settings.navasan_api_key)
