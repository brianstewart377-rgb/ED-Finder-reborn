"""Canonical slot prediction for colony planning.

This module is the only runtime source for predicted slot counts.
It intentionally does not fall back to legacy radius/class heuristics.
When required inputs are missing, it returns unknown with reasons.

Ground slots follow Raven Colonial's rule, unchanged there since
19 November 2025 (``src/slot-prediction.ts`` in njthomson/RavenColonialWeb),
written here in our own words:

  * a body that cannot be landed on, is hotter than 700 K or pulls more than
    2.7 g has none;
  * otherwise a base from the radius: under 1,500 km 1, under 3,750 km 2,
    under 6,000 km 3, bigger 4;
  * plus 1 for a high metal content world, 1 for terraformable, 1 for
    volcanism or geological signals, and 2 for any atmosphere, thin or not;
  * never more than 7.

Life signals add nothing. Until 22 November 2026 this file had drifted from
that rule in four ways: a slot for life signals, 1 rather than 2 for a thin
atmosphere, at most 3 for all the bonuses together, and the step to 4 at
5,500 km. Checked that day against counts read in the game's System Architect
view: Raven's rule was right on all six bodies whose count was certain, and
on one system's whole total; the drifted rule on three of the six. The
orbital estimate is our own; the planner does not publish it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal, Optional

PREDICTION_VERSION = 'raven-slot-v1'
VALIDATION_NOTE = (
    "Raven Colonial's ground-slot rule. Checked on 22 November 2026 against "
    'in-game counts: right on all 6 bodies whose count was certain.'
)
INSUFFICIENT_DATA_REASON = 'insufficient data for validated prediction algorithm'

PredictionStatus = Literal['predicted', 'unknown', 'observed']


@dataclass
class SlotPrediction:
    system_address: int
    body_id: int
    body_name: Optional[str]
    predicted_orbital_slots: Optional[int]
    predicted_ground_slots: Optional[int]
    prediction_status: PredictionStatus
    confidence_label: str
    prediction_version: str
    reasons: list[dict[str, Any]] = field(default_factory=list)
    validation_note: str = VALIDATION_NOTE
    required_input_missing: list[str] = field(default_factory=list)

    # Backward-compatible aliases used by existing call-sites/tests.
    @property
    def orbital_slots(self) -> int:
        return int(self.predicted_orbital_slots or 0)

    @property
    def surface_slots(self) -> int:
        return int(self.predicted_ground_slots or 0)

    @property
    def confidence(self) -> float:
        return 0.96 if self.prediction_status == 'predicted' else 0.0

    @property
    def slot_source(self) -> str:
        if self.prediction_status == 'observed':
            return 'observed'
        if self.prediction_status == 'predicted':
            return 'validated_prediction'
        return 'unknown'

    def to_dict(self) -> dict[str, Any]:
        return {
            'system_address': self.system_address,
            'body_id': self.body_id,
            'body_name': self.body_name,
            'predicted_orbital_slots': self.predicted_orbital_slots,
            'predicted_ground_slots': self.predicted_ground_slots,
            'prediction_status': self.prediction_status,
            'confidence_label': self.confidence_label,
            'prediction_version': self.prediction_version,
            'reasons': self.reasons,
            'validation_note': self.validation_note,
            'required_input_missing': self.required_input_missing,
            'missing_inputs': self.required_input_missing,
            'source_label': self.slot_source,
            # Back-compat mirrors
            'estimated_orbital_slots': self.predicted_orbital_slots,
            'estimated_surface_slots': self.predicted_ground_slots,
            'slot_confidence': self.confidence,
            'slot_source': self.slot_source,
        }


def predict_body_slots(scan_fact: dict[str, Any]) -> SlotPrediction:
    """Predict orbital/ground slots for a single body using validated rules."""
    system_address = _coerce_identifier(scan_fact.get('system_address'))
    body_id = _coerce_identifier(scan_fact.get('body_id'))
    body_name = _clean_text(scan_fact.get('body_name'))

    observed_orbital = _coerce_int(scan_fact.get('observed_orbital_slots'))
    observed_ground = _coerce_int(scan_fact.get('observed_ground_slots'))
    if observed_orbital is not None or observed_ground is not None:
        return SlotPrediction(
            system_address=system_address,
            body_id=body_id,
            body_name=body_name,
            predicted_orbital_slots=observed_orbital,
            predicted_ground_slots=observed_ground,
            prediction_status='observed',
            confidence_label='architect_observed',
            prediction_version=PREDICTION_VERSION,
            reasons=[
                {
                    'factor': 'observed_slots',
                    'note': 'Using architect-observed slot values.',
                }
            ],
            required_input_missing=[],
        )

    reasons: list[dict[str, Any]] = []
    missing: list[str] = []

    is_landable = _coerce_bool(scan_fact.get('is_landable'))
    radius_m = _coerce_float(scan_fact.get('radius'))
    surface_temp = _coerce_float(scan_fact.get('surface_temp'))
    gravity = _coerce_float(scan_fact.get('gravity'))
    atmosphere = _clean_text(scan_fact.get('atmosphere'))

    # Required inputs for validated algorithm.
    if is_landable is None:
        missing.append('is_landable')
    if radius_m is None:
        missing.append('radius')
    if surface_temp is None and is_landable is True:
        missing.append('surface_temp')
    if gravity is None and is_landable is True:
        missing.append('gravity')
    if atmosphere is None and is_landable is True:
        missing.append('atmosphere')

    if missing:
        return SlotPrediction(
            system_address=system_address,
            body_id=body_id,
            body_name=body_name,
            predicted_orbital_slots=None,
            predicted_ground_slots=None,
            prediction_status='unknown',
            confidence_label='insufficient_prediction_data',
            prediction_version=PREDICTION_VERSION,
            reasons=[
                {
                    'factor': 'missing_input',
                    'note': INSUFFICIENT_DATA_REASON,
                },
                {
                    'factor': 'guidance',
                    'note': 'Verify in Architect Mode.',
                },
            ],
            required_input_missing=sorted(set(missing)),
        )

    orbital_slots = _predict_orbital_slots(scan_fact, radius_m, reasons)

    if is_landable is False:
        reasons.append({
            'factor': 'is_landable',
            'value': False,
            'note': 'Non-landable body: 0 predicted ground slots.',
        })
        return SlotPrediction(
            system_address=system_address,
            body_id=body_id,
            body_name=body_name,
            predicted_orbital_slots=orbital_slots,
            predicted_ground_slots=0,
            prediction_status='predicted' if orbital_slots is not None else 'unknown',
            confidence_label='validated_high_accuracy',
            prediction_version=PREDICTION_VERSION,
            reasons=reasons,
            required_input_missing=[],
        )

    # Landable and required inputs present.
    assert radius_m is not None
    assert surface_temp is not None
    assert gravity is not None
    assert atmosphere is not None

    if surface_temp > 700:
        reasons.append({
            'factor': 'surface_temp',
            'value': surface_temp,
            'note': 'Surface temp > 700K: 0 predicted ground slots.',
        })
        return SlotPrediction(
            system_address=system_address,
            body_id=body_id,
            body_name=body_name,
            predicted_orbital_slots=orbital_slots,
            predicted_ground_slots=0,
            prediction_status='predicted' if orbital_slots is not None else 'unknown',
            confidence_label='validated_high_accuracy',
            prediction_version=PREDICTION_VERSION,
            reasons=reasons,
            required_input_missing=[],
        )

    if gravity > 2.7:
        reasons.append({
            'factor': 'gravity',
            'value': gravity,
            'note': 'Gravity > 2.7g: 0 predicted ground slots.',
        })
        return SlotPrediction(
            system_address=system_address,
            body_id=body_id,
            body_name=body_name,
            predicted_orbital_slots=orbital_slots,
            predicted_ground_slots=0,
            prediction_status='predicted' if orbital_slots is not None else 'unknown',
            confidence_label='validated_high_accuracy',
            prediction_version=PREDICTION_VERSION,
            reasons=reasons,
            required_input_missing=[],
        )

    radius_km = radius_m / 1000.0
    base_ground = _ground_base_from_radius_km(radius_km)
    reasons.append({
        'factor': 'radius',
        'value': round(radius_km, 2),
        'note': f'Radius tier base ground slots = {base_ground}.',
    })

    bonus = 0
    planet_class = _clean_text(scan_fact.get('planet_class')) or _clean_text(scan_fact.get('subtype')) or ''
    if _is_hmc(planet_class):
        bonus += 1
        reasons.append({'factor': 'planet_class', 'value': planet_class, 'note': 'High metal content bonus +1.'})

    terraformable = _coerce_bool(scan_fact.get('is_terraformable'))
    terraform_state = _clean_text(scan_fact.get('terraform_state'))
    if terraformable is True or (terraform_state or '').lower() == 'terraformable':
        bonus += 1
        reasons.append({'factor': 'terraformable', 'value': True, 'note': 'Terraformable bonus +1.'})

    has_geo = _has_geo_or_volcanism(scan_fact)
    if has_geo:
        bonus += 1
        reasons.append({'factor': 'geo_or_volcanism', 'value': True, 'note': 'Geo/volcanism bonus +1.'})

    atmosphere_bonus = _atmosphere_bonus(atmosphere)
    if atmosphere_bonus:
        bonus += atmosphere_bonus
        reasons.append({
            'factor': 'atmosphere',
            'value': atmosphere,
            'note': f'Atmosphere bonus +{atmosphere_bonus}.',
        })

    ground_slots = min(base_ground + bonus, 7)

    return SlotPrediction(
        system_address=system_address,
        body_id=body_id,
        body_name=body_name,
        predicted_orbital_slots=orbital_slots,
        predicted_ground_slots=ground_slots,
        prediction_status='predicted' if orbital_slots is not None else 'unknown',
        confidence_label='validated_high_accuracy',
        prediction_version=PREDICTION_VERSION,
        reasons=reasons,
        required_input_missing=[] if orbital_slots is not None else ['radius'],
    )


def confidence_label(confidence: Optional[float]) -> str:
    """Backward-compatible helper for legacy call sites."""
    if confidence is None:
        return 'Unknown'
    if confidence >= 0.9:
        return 'High'
    if confidence >= 0.7:
        return 'Moderate'
    if confidence >= 0.5:
        return 'Low'
    return 'Estimated'


def _predict_orbital_slots(
    scan_fact: dict[str, Any],
    radius_m: Optional[float],
    reasons: list[dict[str, Any]],
) -> Optional[int]:
    if radius_m is None:
        return None
    radius_km = radius_m / 1000.0
    orbital = _orbital_base_from_radius_km(radius_km)

    is_ringed = _coerce_bool(scan_fact.get('is_ringed'))
    if is_ringed:
        orbital = min(4, orbital + 1)
        reasons.append({'factor': 'is_ringed', 'value': True, 'note': 'Ringed body orbital bonus +1.'})

    return min(max(orbital, 1), 4)


def _ground_base_from_radius_km(radius_km: float) -> int:
    if radius_km < 1500:
        return 1
    if radius_km < 3750:
        return 2
    if radius_km < 6000:
        return 3
    return 4


def _orbital_base_from_radius_km(radius_km: float) -> int:
    if radius_km < 1500:
        return 1
    if radius_km < 3750:
        return 2
    if radius_km < 5500:
        return 3
    return 4


def _atmosphere_bonus(atmosphere: str) -> int:
    """2 for any atmosphere, thin or not; 0 for none."""
    normalised = atmosphere.strip().lower()
    if not normalised or normalised == 'no atmosphere':
        return 0
    return 2


def _is_hmc(planet_class: str) -> bool:
    normalised = planet_class.strip().lower()
    return normalised in {
        'high metal content world',
        'high metal content body',
    }


def _has_geo_or_volcanism(scan_fact: dict[str, Any]) -> bool:
    has_geo = _coerce_bool(scan_fact.get('has_geo'))
    geo_count = _coerce_int(scan_fact.get('geo_signal_count'))
    volcanism = _clean_text(scan_fact.get('volcanism')) or ''
    volcanism_norm = volcanism.strip().lower()
    has_volcanism = bool(volcanism_norm and volcanism_norm not in {'no volcanism', 'none'})
    return bool(has_geo or (geo_count is not None and geo_count > 0) or has_volcanism)


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {'true', 't', '1', 'yes', 'y'}:
        return True
    if text in {'false', 'f', '0', 'no', 'n'}:
        return False
    return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_identifier(value: Any) -> int:
    parsed = _coerce_int(value)
    if parsed is not None:
        return parsed
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    # Some legacy rows/tests use ids like "body1"; extract the numeric suffix.
    match = re.search(r'(\d+)(?!.*\d)', text)
    if match:
        return int(match.group(1))
    return 0
