"""Tabdeal (tabdeal.org): Iranian crypto exchange, read-only market data for now."""

from ...config import Settings
from ..registry import register
from .client import TabdealClient

__all__ = ["TabdealClient"]


@register("tabdeal", TabdealClient)
def _make(settings: Settings) -> TabdealClient:
    return TabdealClient()
