"""Pure PP2 journal parsing.

Frontier's Journal Manual v37 predates the Trailblazers additions.  These
contracts follow the March 2025 journal notes and deliberately retain source
values verbatim: PP2 progress and merit fields have had real-world anomalies,
so this module never clamps, rescales, or "repairs" them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

PARSER_VERSION = 'powerplay-journal-v1'
JOURNAL_CONFIDENCE = 0.85
CYCLE_WEEK = timedelta(days=7)
CYCLE_BOUNDARY_WEEKDAY = 3  # Thursday, Monday=0
CYCLE_BOUNDARY_HOUR_UTC = 7

# March-2025-era PP2 powers from the community schemas.  Parsing remains open
# to unknown future names; this list is a fixture/colour vocabulary, not an
# enum validator that would discard new journal truth.
POWERPLAY_POWERS = (
    'Aisling Duval',
    'A. Lavigny-Duval',
    'Archon Delaine',
    'Denton Patreus',
    'Edmund Mahon',
    'Felicia Winters',
    'Jerome Archer',
    'Li Yong-Rui',
    'Nakato Kaine',
    'Pranav Antal',
    'Yuri Grom',
    'Zemina Torval',
)

SYSTEM_EVENTS = {'Location', 'FSDJump'}
PERSONAL_EVENTS = {
    'Powerplay',
    'PowerplayCollect',
    'PowerplayDeliver',
    'PowerplayMerits',
    'PowerplayRank',
    'PowerplayJoin',
    'PowerplayLeave',
    'PowerplayDefect',
}
SUPPORTED_EVENTS = SYSTEM_EVENTS | PERSONAL_EVENTS

SYSTEM_FIELD_MAP = {
    'controlling_power': 'ControllingPower',
    'control_state': 'PowerplayState',
    'control_progress': 'PowerplayStateControlProgress',
    'reinforcement_points': 'PowerplayStateReinforcement',
    'undermining_points': 'PowerplayStateUndermining',
    'powers': 'Powers',
}
CONFLICT_PROGRESS_FIELD = 'PowerplayConflictProgress'


@dataclass(frozen=True)
class ParsedPowerplayEvent:
    kind: Literal['system', 'commander']
    event_type: str
    observed_at: datetime
    cycle_start: datetime
    source_payload: dict[str, Any]
    game_build: str | None
    confidence: float = JOURNAL_CONFIDENCE
    system_address: int | None = None
    system_name: str | None = None
    values: dict[str, Any] = field(default_factory=dict)
    value_provenance: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class CommanderStateProjection:
    pledge: Any = None
    rank: Any = None
    merits: Any = None
    last_updated: datetime | None = None
    value_provenance: dict[str, dict[str, Any]] = field(default_factory=dict)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def cycle_start_for(observed_at: datetime) -> datetime:
    """Return the Thursday 07:00 UTC boundary containing ``observed_at``."""
    observed = _as_utc(observed_at)
    boundary = observed.replace(hour=CYCLE_BOUNDARY_HOUR_UTC, minute=0, second=0, microsecond=0)
    days_since_thursday = (boundary.weekday() - CYCLE_BOUNDARY_WEEKDAY) % 7
    boundary -= timedelta(days=days_since_thursday)
    if observed < boundary:
        boundary -= CYCLE_WEEK
    return boundary


def _provenance(
    *,
    source: str,
    source_version: str,
    observed_at: datetime,
    confidence: float,
) -> dict[str, Any]:
    return {
        'source': source,
        'version': source_version,
        'confidence': confidence,
        'observed_at': _as_utc(observed_at).isoformat(),
    }


def parse_powerplay_event(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
    game_build: str | None = None,
    source: str = 'journal',
    source_version: str = PARSER_VERSION,
    confidence: float = JOURNAL_CONFIDENCE,
) -> ParsedPowerplayEvent | None:
    """Parse one allowlisted journal payload without changing its values."""
    event_type = payload.get('event')
    if not isinstance(event_type, str) or event_type not in SUPPORTED_EVENTS:
        return None

    observed = _as_utc(observed_at)
    common = {
        'event_type': event_type,
        'observed_at': observed,
        'cycle_start': cycle_start_for(observed),
        'source_payload': dict(payload),
        'game_build': game_build,
        'confidence': confidence,
    }

    if event_type in SYSTEM_EVENTS:
        # A Location/FSDJump row is a PP2 observation only when at least one PP2
        # field is actually present.  Missing and explicit null remain distinct.
        values = {
            destination: payload[source_name]
            for destination, source_name in SYSTEM_FIELD_MAP.items()
            if source_name in payload
        }
        source_fields = {
            destination: source_name
            for destination, source_name in SYSTEM_FIELD_MAP.items()
            if source_name in payload
        }
        # Unoccupied/acquisition systems report a per-power object instead of
        # the controlled-system scalar.  Both occupy the observation model's
        # raw control_progress slot; provenance records which journal field was
        # present so consumers never mistake one representation for the other.
        if 'control_progress' not in values and CONFLICT_PROGRESS_FIELD in payload:
            values['control_progress'] = payload[CONFLICT_PROGRESS_FIELD]
            source_fields['control_progress'] = CONFLICT_PROGRESS_FIELD
        if not values:
            return None
        raw_address = payload.get('SystemAddress')
        address = None
        if not isinstance(raw_address, bool) and isinstance(raw_address, int):
            address = raw_address
        elif isinstance(raw_address, str) and raw_address.isdecimal():
            address = int(raw_address)
        if address is None or address <= 0 or address > 9_223_372_036_854_775_807:
            return None
        provenance = _provenance(
            source=source,
            source_version=source_version,
            observed_at=observed,
            confidence=confidence,
        )
        value_provenance = {}
        for key in values:
            item = dict(provenance)
            item['journal_field'] = source_fields[key]
            value_provenance[key] = item
        return ParsedPowerplayEvent(
            kind='system',
            system_address=address,
            system_name=payload.get('StarSystem') if isinstance(payload.get('StarSystem'), str) else None,
            values=values,
            value_provenance=value_provenance,
            **common,
        )

    personal_fields = {
        key: payload[key]
        for key in (
            'Power', 'FromPower', 'ToPower', 'Rank', 'Merits', 'MeritsGained',
            'TotalMerits', 'Count', 'Type', 'TimePledged', 'Votes',
        )
        if key in payload
    }
    provenance = _provenance(
        source=source,
        source_version=source_version,
        observed_at=observed,
        confidence=confidence,
    )
    return ParsedPowerplayEvent(
        kind='commander',
        values=personal_fields,
        value_provenance={key: dict(provenance) for key in personal_fields},
        **common,
    )


def project_commander_state(events: list[ParsedPowerplayEvent]) -> CommanderStateProjection:
    """Rebuild current state from append-only personal events."""
    pledge: Any = None
    rank: Any = None
    merits: Any = None
    last_updated: datetime | None = None
    provenance: dict[str, dict[str, Any]] = {}

    for parsed in sorted(events, key=lambda item: item.observed_at):
        if parsed.kind != 'commander':
            continue
        event = parsed.event_type
        values = parsed.values
        event_provenance = next(iter(parsed.value_provenance.values()), {
            'source': 'unknown',
            'version': 'unknown',
            'confidence': parsed.confidence,
            'observed_at': parsed.observed_at.isoformat(),
        })

        if event == 'Powerplay':
            if 'Power' in values:
                pledge = values['Power']
                provenance['pledge'] = dict(event_provenance)
            if 'Rank' in values:
                rank = values['Rank']
                provenance['rank'] = dict(event_provenance)
            if 'Merits' in values:
                merits = values['Merits']
                provenance['merits'] = dict(event_provenance)
        elif event == 'PowerplayJoin':
            pledge = values.get('Power')
            rank = None
            merits = None
            provenance = {
                'pledge': dict(event_provenance),
                'rank': dict(event_provenance),
                'merits': dict(event_provenance),
            }
        elif event == 'PowerplayDefect':
            pledge = values.get('ToPower')
            rank = None
            merits = None
            provenance = {
                'pledge': dict(event_provenance),
                'rank': dict(event_provenance),
                'merits': dict(event_provenance),
            }
        elif event == 'PowerplayLeave':
            pledge = None
            rank = None
            merits = None
            provenance = {
                'pledge': dict(event_provenance),
                'rank': dict(event_provenance),
                'merits': dict(event_provenance),
            }
        elif event == 'PowerplayMerits':
            if 'Power' in values:
                pledge = values['Power']
                provenance['pledge'] = dict(event_provenance)
            if 'TotalMerits' in values:
                merits = values['TotalMerits']
                provenance['merits'] = dict(event_provenance)
        elif event == 'PowerplayRank':
            if 'Power' in values:
                pledge = values['Power']
                provenance['pledge'] = dict(event_provenance)
            if 'Rank' in values:
                rank = values['Rank']
                provenance['rank'] = dict(event_provenance)
        last_updated = parsed.observed_at

    return CommanderStateProjection(
        pledge=pledge,
        rank=rank,
        merits=merits,
        last_updated=last_updated,
        value_provenance=provenance,
    )
