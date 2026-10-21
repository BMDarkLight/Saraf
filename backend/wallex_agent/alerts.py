"""Price alerts: the agent creates them, a background loop checks them against live prices."""

from __future__ import annotations

import time
import uuid

from .brokers import MarketInfo
from .brokers.base import JsonState
from .wallex import WallexClient


class AlertStore:
    def __init__(self, client: WallexClient, data_dir):
        self.info = MarketInfo(client)
        self.state = JsonState(data_dir / "alerts.json", {"alerts": {}})

    @property
    def alerts(self) -> dict[str, dict]:
        return self.state.data["alerts"]

    def create(self, symbol: str, condition: str, price: float, note: str | None, reference: float) -> dict:
        aid = f"ALR-{uuid.uuid4().hex[:8]}"
        a = {"id": aid, "symbol": symbol.upper(), "condition": condition, "price": price, "note": note,
             "price_at_creation": reference, "created_at": time.time(), "status": "active",
             "triggered_at": None, "triggered_price": None, "seen": False}
        self.alerts[aid] = a
        self.state.save()
        return a

    def cancel(self, aid: str) -> dict | None:
        a = self.alerts.get(aid)
        if a and a["status"] == "active":
            a["status"] = "canceled"
            self.state.save()
        return a

    def list(self, status: str | None = None) -> list[dict]:
        return sorted((a for a in self.alerts.values() if not status or a["status"] == status),
                      key=lambda a: a["created_at"], reverse=True)

    async def check(self) -> list[dict]:
        fired = []
        for a in self.alerts.values():
            if a["status"] != "active":
                continue
            last = (await self.info.quote(a["symbol"]))["last"]
            if (a["condition"] == "above" and last >= a["price"]) or (a["condition"] == "below" and last <= a["price"]):
                a.update(status="triggered", triggered_at=time.time(), triggered_price=last)
                fired.append(a)
        if fired:
            self.state.save()
        return fired

    def ack(self, aid: str) -> None:
        if aid in self.alerts:
            self.alerts[aid]["seen"] = True
            self.state.save()
