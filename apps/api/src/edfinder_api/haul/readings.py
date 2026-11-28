"""Construction-site readings from ED-Finder's EDMC plugin ("Haul — design",
step 2b).

The first rule of Haul: a site's list is the game's own. Each time a player
docks at a construction site the game writes ``ColonisationConstructionDepot``
to the journal: for each good, how much is needed and how much has been
delivered so far, by everyone. A reading is kept only when it differs from the
member's reading of the same site just before it in time, because the game
writes the same list again every few seconds while the player stays docked.
Deliveries are never added up.

The depot event names no system or station, so the site's system and name are
taken from the ``Docked`` event with the same market number: the plugin's last
``Docked`` before the batch, or one inside it. A ``Docked`` at a site also
fills in the name of the member's earlier readings of that site that lack it.

Only the kinds in ``KINDS`` may be sent. The other kinds there (deliveries,
claims, carrier and cargo events) are accepted and left for later steps;
"send past journals" sends them again then.

All SQL is parameterised.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

DEPOT = "ColonisationConstructionDepot"
DOCKED = "Docked"
KINDS = frozenset({
    DEPOT, DOCKED,
    "ColonisationContribution", "ColonisationSystemClaim", "ColonisationSystemClaimRelease",
    "ColonisationBeaconDeployed",
    "Loadout", "Cargo", "CargoTransfer", "CarrierStats", "CarrierBuy", "MarketBuy", "MarketSell",
    "Commander", "LoadGame",
})

MAX_GOODS = 200
MAX_TONNES = 10_000_000
MAX_CREDITS = 1_000_000_000
BIGINT_MAX = 2 ** 63 - 1
_SYMBOL = re.compile(r"[a-z0-9_]{1,80}\Z")


class Refused(ValueError):
    """An event that is not what the game writes."""


def symbol_of(name: Any) -> str:
    """The good's symbol in lower case: '$aluminium_name;' and 'Aluminium'
    both give 'aluminium'."""
    if not isinstance(name, str):
        raise Refused("a good's name is not text")
    text = name.strip()
    if text.startswith("$") and text.endswith("_name;"):
        text = text[1:-len("_name;")]
    text = text.lower()
    if not _SYMBOL.fullmatch(text):
        raise Refused("a good's name is not one the game writes")
    return text


def _whole(value: Any, top: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
        raise Refused("%s is not a whole number in range" % what)
    return value


def _market_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= BIGINT_MAX:
        raise Refused("MarketID is not a market number")
    return value


def when(event: Mapping[str, Any]) -> datetime:
    stamp = event.get("timestamp")
    if not isinstance(stamp, str) or len(stamp) > 40:
        raise Refused("the event has no time")
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise Refused("the event's time is not a time") from None
    if moment.tzinfo is None:
        raise Refused("the event's time has no time zone")
    return moment


@dataclass(frozen=True)
class Site:
    market_id: int
    system_id64: int
    name: str
    kind: Optional[str]


def site_of(event: Optional[Mapping[str, Any]]) -> Optional[Site]:
    """The station a ``Docked`` event names, or None when it is not one."""
    if not isinstance(event, Mapping) or event.get("event") != DOCKED:
        return None
    try:
        market = _market_id(event.get("MarketID"))
        system = event.get("SystemAddress")
        if isinstance(system, bool) or not isinstance(system, int) or not 0 < system <= BIGINT_MAX:
            return None
    except Refused:
        return None
    name = event.get("StationName")
    kind = event.get("StationType")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return None
    if not isinstance(kind, str) or not kind.strip() or len(kind) > 60:
        kind = None
    return Site(market, system, name.strip(), kind.strip() if kind else None)


@dataclass(frozen=True)
class Reading:
    market_id: int
    read_at: datetime
    progress: float
    complete: bool
    failed: bool
    needs: list = field(hash=False)


def reading_of(event: Mapping[str, Any]) -> Reading:
    """The game's list from one ``ColonisationConstructionDepot`` event."""
    market = _market_id(event.get("MarketID"))
    progress = event.get("ConstructionProgress")
    if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not 0 <= progress <= 1:
        raise Refused("ConstructionProgress is not between 0 and 1")
    complete, failed = event.get("ConstructionComplete"), event.get("ConstructionFailed")
    if not isinstance(complete, bool) or not isinstance(failed, bool):
        raise Refused("ConstructionComplete or ConstructionFailed is not true or false")
    goods = event.get("ResourcesRequired")
    if not isinstance(goods, list) or len(goods) > MAX_GOODS:
        raise Refused("ResourcesRequired is not a list of goods")
    needs = {}
    for good in goods:
        if not isinstance(good, Mapping):
            raise Refused("a good is not a record")
        symbol = symbol_of(good.get("Name"))
        if symbol in needs:
            raise Refused("a good is listed twice")
        needs[symbol] = {
            "symbol": symbol,
            "required": _whole(good.get("RequiredAmount"), MAX_TONNES, "RequiredAmount"),
            "provided": _whole(good.get("ProvidedAmount"), MAX_TONNES, "ProvidedAmount"),
            "payment": _whole(good.get("Payment"), MAX_CREDITS, "Payment"),
        }
    return Reading(market, when(event), float(progress), complete, failed,
                   [needs[symbol] for symbol in sorted(needs)])


@dataclass
class Outcome:
    received: int = 0
    kept: int = 0
    repeated: int = 0
    left_for_later: int = 0
    refused: int = 0
    reasons: list = field(default_factory=list)


async def take(conn: Any, account_id: uuid.UUID, events: Sequence[Any],
               docked_before: Optional[Mapping[str, Any]] = None) -> Outcome:
    """Keep the readings a batch of journal events holds. Run inside a
    transaction. The events need not be in order; they are taken by time."""
    outcome = Outcome(received=len(events))
    timed = []
    for position, event in enumerate(events):
        if not isinstance(event, Mapping) or event.get("event") not in KINDS:
            outcome.refused += 1
            outcome.reasons.append("event %d is not one the plugin sends" % position)
            continue
        try:
            timed.append((when(event), position, event))
        except Refused as refused:
            outcome.refused += 1
            outcome.reasons.append("event %d: %s" % (position, refused))
    timed.sort(key=lambda item: (item[0], item[1]))

    docked = site_of(docked_before)
    for _, position, event in timed:
        kind = event["event"]
        if kind == DOCKED:
            docked = site_of(event)
            if docked is not None:
                await _name_earlier_readings(conn, account_id, docked)
            else:
                outcome.left_for_later += 1
            continue
        if kind != DEPOT:
            outcome.left_for_later += 1
            continue
        try:
            reading = reading_of(event)
        except Refused as refused:
            outcome.refused += 1
            outcome.reasons.append("event %d: %s" % (position, refused))
            continue
        site = docked if docked is not None and docked.market_id == reading.market_id else None
        if await _keep(conn, account_id, reading, site):
            outcome.kept += 1
        else:
            outcome.repeated += 1
    outcome.reasons = outcome.reasons[:20]
    return outcome


async def _keep(conn: Any, account_id: uuid.UUID, reading: Reading, site: Optional[Site]) -> bool:
    """Keep the reading unless the member's reading of the site just before
    it in time says the same. The site's name comes from ``site``, or else
    from the member's latest reading of the site that has one."""
    kept = await conn.fetchval(
        """
        INSERT INTO v3_private.haul_site_reading
               (owner_account_id, market_id, read_at, system_id64, site_name, site_type,
                progress, complete, failed, needs)
        SELECT $1, $2, $3,
               coalesce($4, known.system_id64), coalesce($5, known.site_name), coalesce($6, known.site_type),
               $7, $8, $9, $10
          FROM (SELECT 1) AS one
          LEFT JOIN LATERAL (
                SELECT system_id64, site_name, site_type
                  FROM v3_private.haul_site_reading
                 WHERE owner_account_id = $1 AND market_id = $2 AND site_name IS NOT NULL
                 ORDER BY read_at DESC LIMIT 1) AS known ON true
         WHERE NOT EXISTS (
                SELECT 1
                  FROM (SELECT progress, complete, failed, needs
                          FROM v3_private.haul_site_reading
                         WHERE owner_account_id = $1 AND market_id = $2 AND read_at < $3
                         ORDER BY read_at DESC LIMIT 1) AS before
                 WHERE before.progress = $7::real AND before.complete = $8
                   AND before.failed = $9 AND before.needs = $10::jsonb)
        ON CONFLICT (owner_account_id, market_id, read_at) DO NOTHING
        RETURNING true
        """,
        account_id, reading.market_id, reading.read_at,
        site.system_id64 if site else None, site.name if site else None, site.kind if site else None,
        reading.progress, reading.complete, reading.failed, reading.needs,
    )
    return bool(kept)


async def _name_earlier_readings(conn: Any, account_id: uuid.UUID, site: Site) -> None:
    await conn.execute(
        """UPDATE v3_private.haul_site_reading
              SET system_id64 = $3, site_name = $4, site_type = $5
            WHERE owner_account_id = $1 AND market_id = $2 AND site_name IS NULL""",
        account_id, site.market_id, site.system_id64, site.name, site.kind,
    )


async def ready(conn: Any) -> bool:
    """Whether migration 034 has made the readings table."""
    return bool(await conn.fetchval("SELECT to_regclass('v3_private.haul_site_reading') IS NOT NULL"))
