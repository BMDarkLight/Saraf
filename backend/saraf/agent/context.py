from __future__ import annotations

from dataclasses import dataclass, field

from ..alerts import AlertStore
from ..brokers import FuturesBroker, PaperFuturesBroker, PaperSpotBroker, SpotBroker
from ..config import Settings
from ..venues import Venue, Venues


@dataclass
class AppContext:
    settings: Settings
    venue: Venue                  # the default venue: brokers, alerts and portfolio values use it
    spot: SpotBroker
    futures: FuturesBroker
    alerts: AlertStore
    # set by the server: the venue flagged its API key (e.g. a main-account key for a trade-credit account)
    key_is_main: bool = False
    venues: Venues | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.venues is None:
            self.venues = Venues(self.settings, self.venue)

    def market(self, name: str | None = None) -> Venue:
        """A venue for read-only market data: the default one, or another registered venue by name."""
        return self.venue if not name or name.lower() == self.venue.name else self.venues.get(name)

    @classmethod
    def build(cls, settings: Settings) -> "AppContext":
        venues = Venues(settings)
        venue = venues.default
        spot = venue.live_spot_broker() if settings.spot_mode == "live" else PaperSpotBroker(venue, settings)
        futures = (venue.live_futures_broker(settings) if settings.futures_mode == "live"
                   else PaperFuturesBroker(venue, settings))
        return cls(settings, venue, spot, futures, AlertStore(venue, settings.data_dir), venues=venues)
