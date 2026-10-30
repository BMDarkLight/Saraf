"""Trading venues (exchanges, brokers, rate sources) behind one interface; see base.py and registry.py."""

from .base import Capabilities, Credential, Order, Venue, VenueError, VenueUnsupported
from .registry import Venues, available, create, register

from . import binance, bitpin, nobitex, ramzinex, rates, tabdeal, tse, wallex  # noqa: E402,F401  (registers the venues)

__all__ = ["Capabilities", "Credential", "Order", "Venue", "VenueError", "VenueUnsupported", "Venues", "available", "create", "register"]
