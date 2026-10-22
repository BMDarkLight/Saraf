from __future__ import annotations

from dataclasses import dataclass

from ..alerts import AlertStore
from ..brokers import FuturesBroker, LiveSpotBroker, PaperFuturesBroker, PaperSpotBroker, SpotBroker, WallexFuturesBroker
from ..config import Settings
from ..wallex import WallexClient


@dataclass
class AppContext:
    settings: Settings
    client: WallexClient
    spot: SpotBroker
    futures: FuturesBroker
    alerts: AlertStore
    # set by the server: the API key is the main account's while a trade-credit sub-account is configured
    key_is_main: bool = False

    @classmethod
    def build(cls, settings: Settings) -> "AppContext":
        client = WallexClient(settings.wallex_base_url, settings.wallex_api_key,
                              sub_account=settings.wallex_sub_account_client_id)
        spot = LiveSpotBroker(client) if settings.spot_mode == "live" else PaperSpotBroker(client, settings)
        futures = (WallexFuturesBroker(client, settings.wallex_futures_base_url)
                   if settings.futures_mode == "live" else PaperFuturesBroker(client, settings))
        return cls(settings, client, spot, futures, AlertStore(client, settings.data_dir))
