"""Tehran Stock Exchange: read-only market data from TSETMC. A stub venue: no trading (see client.py)."""

from ...config import Settings
from ..registry import register
from .client import TseClient

__all__ = ["TseClient"]


@register("tse")
def _make(settings: Settings) -> TseClient:
    return TseClient()
