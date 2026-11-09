"""V3 journal event contract: the frozen 30-event allowlist and payload strips.

This server-side contract is authoritative. A matching client-side allowlist
(SUPPORTED_JOURNAL_EVENTS and EVENT_PAYLOAD_FIELDS) historically lived in the
retired React reference tree ``frontend/src/lib/journalParsing/journalParser.ts``
(historical evidence only per CLAUDE.md, not the live apps/web product) and is
also mirrored by ``apps/api/src/journal_import/api_models.py``.
The client parser strips events before they cross the network; the server
re-strips the same fields as defense-in-depth. ``GameVersion`` and
``GameBuild`` are additionally allowlisted on every event because the V3
client parser attaches them from the rolling Fileheader/LoadGame state.
"""

from __future__ import annotations

JOURNAL_EVENT_ALLOWLIST: frozenset[str] = frozenset({
    'ApproachBody', 'CarrierJump', 'CodexEntry', 'Commander', 'Died',
    'Disembark', 'Docked', 'Embark', 'Fileheader', 'FSDJump', 'FSDTarget',
    'FSSAllBodiesFound', 'FSSBodySignals', 'FSSDiscoveryScan', 'LeaveBody',
    'Liftoff', 'LoadGame', 'Location', 'MultiSellExplorationData',
    'NavRoute', 'NavRouteClear', 'Resurrect', 'SAAScanComplete',
    'SAASignalsFound', 'Scan', 'ScanOrganic', 'Screenshot',
    'SellExplorationData', 'SellOrganicData', 'Touchdown',
})

# Per-event allowed payload fields — verbatim mirror of the client parser's
# EVENT_PAYLOAD_FIELDS (journalParser.ts), plus the V3-attached game
# identity fields. Field names are journal-verbatim (e.g. lowercase
# Fileheader 'gameversion'/'build' are parser fields, not the attached ones).
_PARSER_PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    'ApproachBody': ('StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName'),
    'CarrierJump': (
        'StarSystem', 'SystemAddress', 'StarPos', 'Body', 'BodyID', 'BodyType',
        'Docked',
    ),
    'CodexEntry': (
        'EntryID', 'Name', 'Name_Localised', 'SubCategory',
        'SubCategory_Localised', 'Category', 'Category_Localised', 'Region',
        'System', 'SystemName', 'StarSystem', 'SystemAddress', 'BodyID',
        'NearestDestination', 'NearestDestination_Localised', 'Latitude',
        'Longitude', 'Traits',
    ),
    'Commander': ('Name', 'FID'),
    'Died': ('KillerName', 'KillerShip', 'KillerRank', 'Killers'),
    'Disembark': (
        'SRV', 'Taxi', 'Multicrew', 'StarSystem', 'SystemAddress', 'Body',
        'BodyID', 'BodyName', 'OnStation', 'OnPlanet',
    ),
    'Docked': (
        'StarSystem', 'SystemAddress', 'StationName', 'StationType',
        'MarketID', 'DistFromStarLS', 'StationGovernment', 'StationAllegiance',
        'StationServices', 'StationEconomies', 'Taxi', 'Multicrew',
    ),
    'Embark': (
        'SRV', 'Taxi', 'Multicrew', 'StarSystem', 'SystemAddress', 'Body',
        'BodyID', 'BodyName', 'OnStation', 'OnPlanet',
    ),
    'Fileheader': ('part', 'language', 'Odyssey', 'gameversion', 'build'),
    'FSDJump': (
        'StarSystem', 'SystemAddress', 'StarPos', 'StarClass', 'JumpDist',
        'FuelUsed', 'FuelLevel',
    ),
    'FSDTarget': ('Name', 'SystemAddress', 'StarClass', 'RemainingJumpsInRoute'),
    'FSSAllBodiesFound': ('StarSystem', 'SystemAddress', 'Count'),
    'FSSBodySignals': ('StarSystem', 'SystemAddress', 'BodyName', 'BodyID', 'Signals'),
    'FSSDiscoveryScan': (
        'StarSystem', 'SystemAddress', 'Progress', 'BodyCount', 'NonBodyCount',
    ),
    'LeaveBody': ('StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName'),
    'Liftoff': (
        'StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName', 'Latitude',
        'Longitude', 'PlayerControlled', 'NearestDestination',
        'NearestDestination_Localised',
    ),
    'LoadGame': (
        'Commander', 'FID', 'Horizons', 'Odyssey', 'Ship', 'Ship_Localised',
        'ShipID', 'ShipName', 'ShipIdent', 'FuelLevel', 'FuelCapacity',
        'GameMode', 'Group', 'Credits', 'Loan',
    ),
    'Location': (
        'StarSystem', 'SystemAddress', 'StarPos', 'Body', 'BodyID', 'BodyType',
        'Docked', 'StationName', 'StationType', 'MarketID', 'Latitude',
        'Longitude',
    ),
    'MultiSellExplorationData': ('Discovered', 'BaseValue', 'Bonus', 'TotalEarnings'),
    'NavRoute': ('Route',),
    'NavRouteClear': (),
    'Resurrect': ('Option', 'Cost', 'Bankrupt'),
    'SAAScanComplete': (
        'StarSystem', 'SystemName', 'SystemAddress', 'BodyName', 'BodyID',
        'ProbesUsed', 'EfficiencyTarget',
    ),
    'SAASignalsFound': ('StarSystem', 'SystemAddress', 'BodyName', 'BodyID', 'Signals', 'Genuses'),
    'Scan': (
        'ScanType', 'StarSystem', 'SystemAddress', 'BodyName', 'BodyID',
        'DistanceFromArrivalLS', 'StarType', 'Subclass', 'StellarMass',
        'Radius', 'AbsoluteMagnitude', 'Age_MY', 'SurfaceTemperature',
        'Luminosity', 'SemiMajorAxis', 'Eccentricity', 'OrbitalInclination',
        'Periapsis', 'OrbitalPeriod', 'RotationPeriod', 'AxialTilt', 'Rings',
        'Parents', 'PlanetClass', 'Atmosphere', 'AtmosphereType',
        'AtmosphereComposition', 'Volcanism', 'MassEM', 'SurfaceGravity',
        'SurfacePressure', 'Landable', 'Materials', 'Composition',
        'ReserveLevel', 'TerraformState', 'WasDiscovered', 'WasMapped',
    ),
    'ScanOrganic': (
        'ScanType', 'Genus', 'Genus_Localised', 'Species', 'Species_Localised',
        'Variant', 'Variant_Localised', 'StarSystem', 'SystemName',
        'SystemAddress', 'Body', 'BodyID', 'BodyName',
    ),
    'Screenshot': (
        'Filename', 'Width', 'Height', 'System', 'SystemAddress', 'Body',
        'BodyID', 'Latitude', 'Longitude', 'Altitude', 'Heading',
    ),
    'SellExplorationData': ('Systems', 'Discovered', 'BaseValue', 'Bonus'),
    'SellOrganicData': ('MarketID', 'BioData'),
    'Touchdown': (
        'StarSystem', 'SystemAddress', 'Body', 'BodyID', 'BodyName', 'Latitude',
        'Longitude', 'PlayerControlled', 'NearestDestination',
        'NearestDestination_Localised',
    ),
}

_GAME_ATTACHED_FIELDS = ('GameVersion', 'GameBuild')

EVENT_PAYLOAD_ALLOWLIST: dict[str, frozenset[str]] = {
    event_type: frozenset((*fields, *_GAME_ATTACHED_FIELDS))
    for event_type, fields in _PARSER_PAYLOAD_FIELDS.items()
}


def strip_payload(event_type: str, payload: dict) -> tuple[dict, int]:
    """Strip non-allowlisted payload fields (fail-closed defense-in-depth).

    Returns ``(stripped, n_removed)``. Raises ``ValueError`` for event types
    outside the frozen allowlist.
    """
    if event_type not in JOURNAL_EVENT_ALLOWLIST:
        raise ValueError(
            f'Unknown journal event type: {event_type!r}; '
            f'allowlist = {sorted(JOURNAL_EVENT_ALLOWLIST)}',
        )
    allowed = EVENT_PAYLOAD_ALLOWLIST[event_type]
    stripped = {key: value for key, value in payload.items() if key in allowed}
    return stripped, len(payload) - len(stripped)
