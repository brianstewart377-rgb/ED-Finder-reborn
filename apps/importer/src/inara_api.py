"""
inara_api.py  v1.0
──────────────────
Inara API integration for ED Finder.
Enriches system and commander data with live Inara information.

API docs: https://inara.cz/inara-api-devguide/

SETUP:
  1. Register your application at https://inara.cz/inara-api-devguide/
  2. Set the INARA_API_KEY environment variable in your .env file:
       INARA_API_KEY=your_key_here
  3. Optionally set INARA_APP_NAME and INARA_APP_VERSION.

USAGE:
  from inara_api import InaraClient
  client = InaraClient()
  data = await client.get_system(system_name="Shinrarta Dezhra")
"""

import asyncio
import logging
import os
import time
from typing import Any, Optional

import aiohttp

log = logging.getLogger("inara_api")

# ── Config ────────────────────────────────────────────────────────────────────
INARA_API_KEY     = os.getenv("INARA_API_KEY", "PLACEHOLDER_SET_INARA_API_KEY_IN_ENV")
INARA_APP_NAME    = os.getenv("INARA_APP_NAME", "ED-Finder")
INARA_APP_VERSION = os.getenv("INARA_APP_VERSION", "3.0")
INARA_API_URL     = "https://inara.cz/inapi/v1/"
INARA_TIMEOUT_S   = 10

# Rate limit: Inara allows ~1 req/s per API key
_last_call_ts: float = 0.0
_RATE_LIMIT_S: float = 1.1


class InaraAPIError(Exception):
    """Raised when the Inara API returns an error response."""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Inara API error {code}: {message}")


class InaraClient:
    """Async Inara API client with rate limiting and error handling."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or INARA_API_KEY
        if self.api_key == "PLACEHOLDER_SET_INARA_API_KEY_IN_ENV":
            log.warning("Inara API key not set — all Inara calls will fail. "
                        "Set INARA_API_KEY in your .env file.")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=INARA_TIMEOUT_S),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _call(self, events: list[dict]) -> list[dict]:
        """Send one or more events to the Inara API and return results."""
        global _last_call_ts

        # Rate limiting
        elapsed = time.monotonic() - _last_call_ts
        if elapsed < _RATE_LIMIT_S:
            await asyncio.sleep(_RATE_LIMIT_S - elapsed)

        payload = {
            "header": {
                "appName":    INARA_APP_NAME,
                "appVersion": INARA_APP_VERSION,
                "isDeveloped": True,
                "APIkey":     self.api_key,
            },
            "events": events,
        }

        session = await self._get_session()
        _last_call_ts = time.monotonic()

        try:
            async with session.post(INARA_API_URL, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except aiohttp.ClientError as e:
            raise InaraAPIError(0, f"HTTP error: {e}") from e

        # Check header status
        header = data.get("header", {})
        if header.get("eventStatus", 200) >= 400:
            raise InaraAPIError(
                header.get("eventStatus", 0),
                header.get("eventStatusText", "Unknown error"),
            )

        results = data.get("events", [])
        return results

    # ── High-level helpers ────────────────────────────────────────────────────

    async def get_system(self, system_name: str) -> Optional[dict[str, Any]]:
        """
        Fetch system data from Inara by name.
        Returns a dict with keys: name, allegiance, government, economy,
        security, population, controlling_faction, stations, etc.
        Returns None if the system is not found.
        """
        events = [{
            "eventName":    "getSystem",
            "eventTimestamp": _iso_now(),
            "eventData":    {"systemName": system_name},
        }]
        try:
            results = await self._call(events)
        except InaraAPIError as e:
            log.warning("Inara getSystem failed for %r: %s", system_name, e)
            return None

        result = results[0] if results else {}
        if result.get("eventStatus", 200) == 204:
            return None  # Not found

        raw = result.get("eventData", {})
        if not raw:
            return None

        return {
            "name":                raw.get("systemName"),
            "allegiance":          raw.get("systemAllegiance"),
            "government":          raw.get("systemGovernment"),
            "economy":             raw.get("systemEconomy"),
            "second_economy":      raw.get("systemSecondEconomy"),
            "security":            raw.get("systemSecurity"),
            "population":          raw.get("systemPopulation"),
            "controlling_faction": raw.get("systemControllingFaction", {}).get("factionName"),
            "inara_url":           raw.get("inaraURL"),
            "stations":            [
                {
                    "name":         s.get("stationName"),
                    "type":         s.get("stationType"),
                    "distance_ls":  s.get("distanceToArrival"),
                    "services":     s.get("stationServices", []),
                    "economy":      s.get("stationEconomy"),
                    "inara_url":    s.get("inaraURL"),
                }
                for s in raw.get("relatedStationsInSystem", [])
            ],
        }

    async def get_commander(self, cmdr_name: str) -> Optional[dict[str, Any]]:
        """
        Fetch commander data from Inara by name.
        Returns a dict with keys: name, rank_combat, rank_trade, rank_explore,
        rank_cqc, allegiance, power, inara_url, etc.
        Returns None if the commander is not found or profile is private.
        """
        events = [{
            "eventName":    "getCommanderProfile",
            "eventTimestamp": _iso_now(),
            "eventData":    {"searchName": cmdr_name},
        }]
        try:
            results = await self._call(events)
        except InaraAPIError as e:
            log.warning("Inara getCommanderProfile failed for %r: %s", cmdr_name, e)
            return None

        result = results[0] if results else {}
        if result.get("eventStatus", 200) == 204:
            return None  # Not found

        raw = result.get("eventData", {})
        if not raw:
            return None

        ranks = {r["rankName"]: r["rankValue"] for r in raw.get("commanderRanksPilot", [])}
        return {
            "name":          raw.get("userName"),
            "rank_combat":   ranks.get("Combat"),
            "rank_trade":    ranks.get("Trade"),
            "rank_explore":  ranks.get("Exploration"),
            "rank_cqc":      ranks.get("CQC"),
            "allegiance":    raw.get("preferredAllegiance"),
            "power":         raw.get("preferredPower"),
            "inara_url":     raw.get("inaraURL"),
            "avatar_url":    raw.get("avatarImageURL"),
            "squadron":      raw.get("commanderSquadron", {}).get("squadronName"),
        }

    async def get_market(self, station_name: str, system_name: str) -> Optional[list[dict]]:
        """
        Fetch market commodity data for a station.
        Returns a list of commodity dicts or None if not found.
        """
        events = [{
            "eventName":    "getStationMarket",
            "eventTimestamp": _iso_now(),
            "eventData":    {
                "stationName": station_name,
                "systemName":  system_name,
            },
        }]
        try:
            results = await self._call(events)
        except InaraAPIError as e:
            log.warning("Inara getStationMarket failed for %r/%r: %s", station_name, system_name, e)
            return None

        result = results[0] if results else {}
        if result.get("eventStatus", 200) >= 400:
            return None

        raw = result.get("eventData", {})
        return [
            {
                "name":       c.get("commodityName"),
                "buy_price":  c.get("buyPrice"),
                "sell_price": c.get("sellPrice"),
                "demand":     c.get("demand"),
                "supply":     c.get("stock"),
            }
            for c in raw.get("commodities", [])
        ]


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Singleton for use in FastAPI ──────────────────────────────────────────────
_client: Optional[InaraClient] = None


def get_inara_client() -> InaraClient:
    """Return the module-level singleton InaraClient."""
    global _client
    if _client is None:
        _client = InaraClient()
    return _client


async def close_inara_client():
    """Call this on app shutdown to cleanly close the aiohttp session."""
    global _client
    if _client:
        await _client.close()
        _client = None
