"""Shared HTTP plumbing for venue adapters: one httpx client, a short TTL cache, the retry policy, number helpers."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, Awaitable, Callable

import httpx

from .base import Venue, VenueError


def fmt_dec(x: float | str | Decimal) -> str:
    """A number as a plain decimal string for an order body: no exponent, no trailing zeros."""
    d = Decimal(str(x)) if not isinstance(x, Decimal) else x
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def rial_to_toman(x: Any) -> float:
    try:
        return float(x) / 10
    except (TypeError, ValueError):
        return 0.0


def decimals_of(step: Any) -> int:
    """Decimals implied by a step like "0.0001" (4), "1" (0) or "10" (0)."""
    try:
        d = Decimal(str(step)).normalize()
    except ArithmeticError:
        return 0
    return max(-d.as_tuple().exponent, 0)


class HttpVenue(Venue):
    """Base for adapters that talk JSON over HTTPS. Subclasses parse the reply and raise VenueError themselves."""

    def __init__(self, base_url: str, timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport,
                                       headers={"User-Agent": "saraf/0.1", "Accept": "application/json"})
        self._cache: dict[str, tuple[float, Any]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _cached(self, key: str, ttl: float, fn: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        value = await fn()
        self._cache[key] = (time.monotonic() + ttl, value)
        return value

    async def _send(self, method: str, path: str, *, params: dict | None = None, json: Any = None,
                    content: bytes | None = None, headers: dict | None = None, retries: int = 2) -> httpx.Response:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        # An order POST may have reached the exchange even when the reply was lost, so only GETs are retried on
        # timeouts and gateway errors; anything else only when it surely never arrived or was refused unprocessed.
        idempotent = method.upper() == "GET"
        for attempt in range(retries + 1):
            try:
                r = await self._http.request(method, path, params=params, json=json, content=content, headers=headers)
            except httpx.TransportError as e:
                unsent = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))
                if attempt < retries and (idempotent or unsent):
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise VenueError(f"{self.title}: network error calling {path}: "
                                 f"{type(e).__name__}{f' ({e})' if str(e) else ''}") from e
            if r.status_code in ((429, 502, 503, 504) if idempotent else (429,)) and attempt < retries:
                await asyncio.sleep(0.7 * (attempt + 1))
                continue
            return r
        return r

    def _json(self, r: httpx.Response, path: str) -> Any:
        try:
            return r.json()
        except ValueError:
            raise VenueError(f"{self.title}: non-JSON response from {path} (HTTP {r.status_code})",
                             r.status_code, r.text[:300]) from None


def jwt_exp(token: str) -> float:
    """The `exp` claim of a JWT (unverified, only to know when to renew it); 0 when it can't be read."""
    import base64
    import json

    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return 0.0
