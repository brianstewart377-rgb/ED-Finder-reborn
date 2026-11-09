"""
ED Finder — Ingest: Journal Normaliser
========================================
Converts raw EDDN journal events into body_scan_facts rows.

Design rules:
  • Pure transformation functions — no DB access, no asyncio.
  • Input: raw EDDN event dict (the 'message' payload).
  • Output: normalised dict ready for upsert into body_scan_facts.
  • Every function is independently testable.
  • Never raises on malformed input — returns None or partial data.

Supported event types:
  Journal/Scan             — primary body data source
  Journal/FSSBodySignals   — signal counts (bio/geo)
  Journal/SAASignalsFound  — DSS scan results (higher confidence)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from edfinder_api.ring_facts import normalise_ring_payload

log = logging.getLogger('ed_finder')

# Confidence levels — stored in body_scan_facts.confidence
CONFIDENCE_DSS      = 0.95   # Full DSS (detailed surface scan) probe data
CONFIDENCE_FSS      = 0.75   # FSS (full spectrum scan) body data
CONFIDENCE_SCAN     = 0.70   # Basic Scan event (auto-scan on jump)
CONFIDENCE_SIGNAL   = 0.60   # Signal count only, no body details
CONFIDENCE_ESTIMATE = 0.40   # Estimated from system-level data


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def normalise_scan_event(event: dict) -> Optional[dict]:
    """
    Convert a Journal/Scan event into a body_scan_facts dict.

    Returns None if the event lacks the minimum required fields
    (SystemAddress + BodyID).
    """
    system_address = _safe_int(event.get('SystemAddress'))
    body_id        = _safe_int(event.get('BodyID'))

    if system_address is None or body_id is None:
        log.debug('normalise_scan_event: missing SystemAddress or BodyID, skipping')
        return None

    body_name    = event.get('BodyName', '')
    planet_class = event.get('PlanetClass', '')
    star_type    = event.get('StarType', '')

    # Stars don't get surface/body slots — skip
    if star_type and not planet_class:
        return None

    terraform_state  = _normalise_terraform_state(event.get('TerraformState', ''))
    atmosphere       = event.get('Atmosphere') or event.get('AtmosphereType') or ''
    volcanism        = event.get('Volcanism', '')
    is_landable      = bool(event.get('Landable', False))
    is_terraformable = terraform_state == 'Terraformable'

    radius           = _safe_float(event.get('Radius'))            # metres
    mass_em          = _safe_float(event.get('MassEM'))            # Earth masses
    surface_temp     = _safe_float(event.get('SurfaceTemperature'))
    surface_pressure = _safe_float(event.get('SurfacePressure'))
    gravity          = _safe_float(event.get('SurfaceGravity'))    # m/s²

    # Orbital elements
    semi_major_axis  = _safe_float(event.get('SemiMajorAxis'))
    orbital_period   = _safe_float(event.get('OrbitalPeriod'))

    # Rings. Scan is a full body scan, so an explicitly empty Rings array is
    # trusted no-ring evidence; missing Rings remains unknown.
    ring_result = normalise_ring_payload(event, trusted_empty_means_no_rings=True)
    is_ringed = True if ring_result.rings else False if ring_result.explicit_no_rings else None

    # Parents
    parents = event.get('Parents')

    # Signals from Scan event (if DSS already done)
    signals = event.get('Signals', []) or []
    geo_count, bio_count = _parse_signals(signals)
    has_geo = geo_count > 0
    has_bio = bio_count > 0

    # Confidence: DSS if Signals present in Scan, else FSS-level
    confidence = CONFIDENCE_DSS if signals else CONFIDENCE_FSS

    return {
        'system_address':   system_address,
        'body_id':          body_id,
        'body_name':        body_name,
        'radius':           radius,
        'mass_em':          mass_em,
        'gravity':          gravity,
        'surface_temp':     surface_temp,
        'surface_pressure': surface_pressure,
        'planet_class':     planet_class or None,
        'terraform_state':  terraform_state or None,
        'atmosphere':       atmosphere or None,
        'volcanism':        volcanism or None,
        'semi_major_axis':  semi_major_axis,
        'orbital_period':   orbital_period,
        'parents':          parents,
        'has_geo':          has_geo,
        'has_bio':          has_bio,
        'geo_signal_count': geo_count,
        'bio_signal_count': bio_count,
        'is_landable':      is_landable,
        'is_terraformable': is_terraformable,
        'is_ringed':        is_ringed,
        'rings':            ring_result.rings,
        'data_sources':     ['eddn_scan'],
        'confidence':       confidence,
    }


def normalise_fss_body_signals(event: dict) -> Optional[dict]:
    """
    Convert a Journal/FSSBodySignals event into a partial body_scan_facts dict.
    Only updates signal counts — does not overwrite other fields.
    """
    system_address = _safe_int(event.get('SystemAddress'))
    body_id        = _safe_int(event.get('BodyID'))

    if system_address is None or body_id is None:
        return None

    signals   = event.get('Signals', []) or []
    geo_count, bio_count = _parse_signals(signals)

    return {
        'system_address':   system_address,
        'body_id':          body_id,
        'body_name':        event.get('BodyName', ''),
        'has_geo':          geo_count > 0,
        'has_bio':          bio_count > 0,
        'geo_signal_count': geo_count,
        'bio_signal_count': bio_count,
        'data_sources':     ['eddn_fssbodysignals'],
        'confidence':       CONFIDENCE_SIGNAL,
    }


def normalise_saa_signals(event: dict) -> Optional[dict]:
    """
    Convert a Journal/SAASignalsFound event into a partial body_scan_facts dict.
    SAASignalsFound = player has DSS-probed this body — highest confidence.
    """
    system_address = _safe_int(event.get('SystemAddress'))
    body_id        = _safe_int(event.get('BodyID'))

    if system_address is None or body_id is None:
        return None

    signals   = event.get('Signals', []) or []
    geo_count, bio_count = _parse_signals(signals)

    return {
        'system_address':   system_address,
        'body_id':          body_id,
        'body_name':        event.get('BodyName', ''),
        'has_geo':          geo_count > 0,
        'has_bio':          bio_count > 0,
        'geo_signal_count': geo_count,
        'bio_signal_count': bio_count,
        'data_sources':     ['eddn_saasignals'],
        # DSS probe = highest confidence for signal data
        'confidence':       CONFIDENCE_DSS,
    }


def event_type_to_normaliser(event_type: str):
    """
    Return the appropriate normaliser function for a given EDDN event type.
    Returns None if we don't handle this event type.
    """
    _MAP = {
        'Scan':            normalise_scan_event,
        'FSSBodySignals':  normalise_fss_body_signals,
        'SAASignalsFound': normalise_saa_signals,
    }
    return _MAP.get(event_type)


def build_journal_event_row(
    event: dict,
    event_type: str,
    source: str = 'eddn',
) -> dict:
    """
    Build a row dict for insertion into journal_events.
    Always succeeds — raw_event captures the full payload.
    """
    ts_str = event.get('timestamp') or event.get('Timestamp')
    try:
        event_timestamp = datetime.fromisoformat(
            ts_str.replace('Z', '+00:00')
        ) if ts_str else None
    except (ValueError, AttributeError):
        event_timestamp = None

    return {
        'system_address':  _safe_int(event.get('SystemAddress')),
        'system_name':     event.get('StarSystem') or event.get('SystemName'),
        'body_id':         _safe_int(event.get('BodyID')),
        'body_name':       event.get('BodyName'),
        'event_type':      event_type,
        'event_timestamp': event_timestamp,
        'source':          source,
        'raw_event':       event,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_signals(signals: list[dict]) -> tuple[int, int]:
    """Extract geo and bio signal counts from a Signals list."""
    geo_count = 0
    bio_count = 0
    for sig in signals:
        sig_type  = (sig.get('Type') or '').lower()
        sig_count = _safe_int(sig.get('Count')) or 0
        if 'geological' in sig_type or 'geo' in sig_type:
            geo_count += sig_count
        elif 'biological' in sig_type or 'bio' in sig_type:
            bio_count += sig_count
    return geo_count, bio_count


def _normalise_terraform_state(raw: str) -> str:
    """Normalise terraform state to a clean canonical form."""
    if not raw:
        return ''
    clean = raw.strip()
    mapping = {
        'Terraformable':  'Terraformable',
        'Terraformed':    'Terraformed',
        'Being Terraformed': 'Terraformed',
        'None':           '',
        '':               '',
    }
    return mapping.get(clean, clean)


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
