"""Venue registry: name -> factory. Adding a venue (Nobitex, Bitpin, Ramzinex, a currency-rate source, ...):

    # saraf/venues/nobitex/__init__.py
    @register("nobitex")
    def _make(settings: Settings) -> Venue:
        return NobitexClient(settings.nobitex_api_key)

then import the package in saraf/venues/__init__.py. Nothing else in the app names a venue.
"""

from __future__ import annotations

from typing import Callable

from ..config import Settings
from .base import Venue, VenueError

Factory = Callable[[Settings], Venue]
_FACTORIES: dict[str, Factory] = {}


def register(name: str) -> Callable[[Factory], Factory]:
    def deco(factory: Factory) -> Factory:
        _FACTORIES[name] = factory
        return factory
    return deco


def available() -> list[str]:
    return list(_FACTORIES)


def create(name: str, settings: Settings) -> Venue:
    try:
        factory = _FACTORIES[name.lower()]
    except KeyError:
        raise VenueError(f"unknown venue {name!r}; available: {', '.join(available())}") from None
    return factory(settings)


class Venues:
    """The venues one process uses, created on first use and shared (each holds an HTTP client and caches)."""

    def __init__(self, settings: Settings, default: Venue | None = None):
        self.settings = settings
        self._open: dict[str, Venue] = {}
        if default is not None:
            self._open[default.name] = default
        self.default_name = default.name if default is not None else settings.default_venue

    @property
    def default(self) -> Venue:
        return self.get()

    def get(self, name: str | None = None) -> Venue:
        name = (name or self.default_name).lower()
        if name not in self._open:
            self._open[name] = create(name, self.settings)
        return self._open[name]

    def describe(self) -> list[dict]:
        out = []
        for name in available():
            v = self.get(name)
            out.append({**v.describe(), "default": name == self.default_name})
        return out

    async def aclose(self) -> None:
        for v in self._open.values():
            await v.aclose()
