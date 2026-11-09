import type { SystemBody, SystemDetail, SystemStation } from '@ed-finder/api-client/types';
import { bodyIdKey, sameBodyId } from './bodyId';

export type ExistingStructureLane = 'orbital' | 'surface' | 'unknown';
export type ExistingStructureBodyMatchConfidence = 'exact' | 'inferred' | 'unresolved';
export type ExistingStructureAssociationStatus = 'confirmed' | 'inferred' | 'unresolved';
export type ExistingStructureAssociationConfidence = 'exact' | 'strong_inference' | 'weak_inference' | 'unresolved';

const TRANSIENT_STATION_TYPE_TOKENS = new Set(['fleetcarrier', 'carrier', 'megaship']);

export interface ExistingStructure {
  source: 'existing';
  id: string;
  market_id: number | null;
  name: string;
  station_type: string | null;
  body_id: string | null;
  body_name: string | null;
  body_match_confidence: ExistingStructureBodyMatchConfidence;
  body_match_reason: string;
  lane: ExistingStructureLane;
  association_status: ExistingStructureAssociationStatus;
  association_confidence: ExistingStructureAssociationConfidence;
  association_source: string;
  economy: string | null;
  secondary_economy: string | null;
  pad_size: string | null;
  distance_from_star: number | null;
  unresolved_reason: string | null;
  transient: boolean;
  transient_reason: string | null;
  raw: {
    station_id: number | string | null;
    body_name: string | null;
    station_type: string | null;
  };
}

export interface ExistingInfrastructureResolution {
  structures: ExistingStructure[];
  mapped: ExistingStructure[];
  unresolved: ExistingStructure[];
  transient: ExistingStructure[];
  byBodyId: Map<string, {
    orbital: ExistingStructure[];
    surface: ExistingStructure[];
    unknown: ExistingStructure[];
  }>;
}

type StationBodyMatch = {
  body: SystemBody | null;
  confidence: ExistingStructureBodyMatchConfidence;
  reason: string;
  source: string;
};

export function resolveExistingInfrastructure(system: SystemDetail): ExistingInfrastructureResolution {
  const bodies = (system.bodies ?? []).filter((body) => body?.id != null);
  const structures = (system.stations ?? []).map((station, index) => resolveStation(bodies, station, index));
  const mapped = structures.filter(isExistingSlotOccupant);
  const transient = structures.filter(isTransientExistingStructure);
  const unresolved = structures.filter(isUnresolvedPermanentInfrastructure);
  const byBodyId = new Map<string, { orbital: ExistingStructure[]; surface: ExistingStructure[]; unknown: ExistingStructure[] }>();

  structures.forEach((structure) => {
    if (!isExistingSlotOccupant(structure)) return;
    const current = byBodyId.get(structure.body_id) ?? { orbital: [], surface: [], unknown: [] };
    current[structure.lane].push(structure);
    byBodyId.set(structure.body_id, current);
  });

  return { structures, mapped, unresolved, transient, byBodyId };
}

export function isExistingSlotOccupant(structure: ExistingStructure): structure is ExistingStructure & { body_id: string; lane: 'orbital' | 'surface' } {
  return Boolean(
    !structure.transient
    && structure.body_id
    && (structure.association_status === 'confirmed' || structure.association_status === 'inferred')
    && (structure.lane === 'orbital' || structure.lane === 'surface'),
  );
}

export function isTransientExistingStructure(structure: ExistingStructure): boolean {
  return structure.transient;
}

export function isTransientStationForColonyPlanning(station: Partial<SystemStation> & Record<string, unknown>): boolean {
  return transientStationPlanningReason(station) != null;
}

export function transientStationPlanningReason(station: Partial<SystemStation> & Record<string, unknown>): string | null {
  const stationType = readString(station.station_type) ?? readString(station.type);
  if (isTransientStationType(stationType)) return 'Fleet Carrier / transient / ignored for colony planning';

  const name = readString(station.name);
  if (name && isFleetCarrierCallsign(name) && !hasConfirmedPermanentStationBodyLink(station)) {
    return 'Fleet Carrier callsign / transient / ignored for colony planning';
  }

  return null;
}

export function isTransientStationType(stationType?: string | null): boolean {
  return TRANSIENT_STATION_TYPE_TOKENS.has(normaliseToken(stationType));
}

export function isFleetCarrierCallsign(name?: string | null): boolean {
  return /^[A-Z0-9]{3}-[A-Z0-9]{3}$/i.test((name ?? '').trim());
}

export function classifyExistingStationLane(stationType?: string | null): ExistingStructureLane {
  const value = normaliseToken(stationType);
  if (!value) return 'unknown';

  if (value === 'planetaryport' || value === 'planetaryoutpost' || value.includes('settlement') || value.includes('surface')) {
    return 'surface';
  }
  if (value === 'coriolis' || value === 'orbis' || value === 'ocellus' || value === 'outpost' || value === 'asteroidbase') {
    return 'orbital';
  }

  // Fleet carriers and megaships are real infrastructure but are not safe
  // colonisation-slot occupants without a stronger source classification.
  return 'unknown';
}

export function existingStructureDisplayType(structure: Pick<ExistingStructure, 'station_type' | 'lane'>): string {
  if (structure.station_type) return readableStationType(structure.station_type);
  if (structure.lane === 'orbital') return 'Orbital station';
  if (structure.lane === 'surface') return 'Surface port';
  return 'Existing infrastructure';
}

function resolveStation(
  bodies: SystemBody[],
  station: SystemStation,
  index: number,
): ExistingStructure {
  const record = station as SystemStation & Record<string, unknown>;
  const stationType = readString(record.station_type) ?? readString(record.type);
  const name = readString(record.name) ?? `Existing station ${index + 1}`;
  const backendStatus = readAssociationStatus(record.association_status);
  const transientReason = transientStationPlanningReason(record);
  if (backendStatus) {
    return structureFromBackendAssociation(record, stationType, name, index, backendStatus, transientReason);
  }
  const explicitBodyId = readBodyId(record.body_id) ?? readBodyId(record.local_body_id);
  const stationBodyName = readString(record.body_name);
  const distanceFromStar = readNumber(record.distance_from_star ?? record.distance_to_arrival);
  const bodyMatch = matchStationBody(bodies, explicitBodyId, stationBodyName, distanceFromStar);
  const lane = classifyExistingStationLane(stationType);
  const stationId = readNumber(record.id) ?? readString(record.id);
  const marketId = readNumber(record.market_id) ?? readNumber(record.id);
  const unresolvedReason = bodyMatch.body == null
    ? bodyMatch.reason
    : lane === 'unknown'
      ? 'Station type cannot be safely mapped to an orbital or surface slot.'
      : null;

  return {
    source: 'existing',
    id: `existing-${marketId ?? stationId ?? index}`,
    market_id: marketId,
    name,
    station_type: stationType,
    body_id: bodyMatch.body?.id != null ? bodyIdKey(bodyMatch.body.id) : null,
    body_name: bodyMatch.body?.name ?? stationBodyName,
    body_match_confidence: bodyMatch.confidence,
    body_match_reason: bodyMatch.reason,
    lane,
    association_status: bodyMatch.confidence === 'exact' ? 'confirmed' : bodyMatch.confidence === 'inferred' ? 'inferred' : 'unresolved',
    association_confidence: bodyMatch.confidence === 'exact' ? 'exact' : bodyMatch.confidence === 'inferred' ? 'strong_inference' : 'unresolved',
    association_source: bodyMatch.source,
    economy: readString(record.primary_economy) ?? readEconomyName(record.economies, 0),
    secondary_economy: readString(record.secondary_economy) ?? readEconomyName(record.economies, 1),
    pad_size: readString(record.landing_pad_size) ?? readString(record.pad_size),
    distance_from_star: distanceFromStar,
    unresolved_reason: transientReason ?? unresolvedReason,
    transient: transientReason != null,
    transient_reason: transientReason,
    raw: {
      station_id: stationId,
      body_name: stationBodyName,
      station_type: stationType,
    },
  };
}

function structureFromBackendAssociation(
  record: SystemStation & Record<string, unknown>,
  stationType: string | null,
  name: string,
  index: number,
  associationStatus: ExistingStructureAssociationStatus,
  transientReason: string | null,
): ExistingStructure {
  const lane = readLane(record.lane);
  const bodyId = readBodyId(record.body_id);
  const stationId = readNumber(record.id) ?? readString(record.id);
  const marketId = readNumber(record.market_id) ?? readNumber(record.id);
  const associationConfidence = readAssociationConfidence(record.association_confidence);
  const associationSource = readString(record.association_source) ?? 'unknown';
  const bodyName = readString(record.body_name);
  const stationBodyName = readString(record.station_body_name) ?? bodyName;
  const resolverNotes = readString(record.resolver_notes);
  const unresolvedReason = transientReason
    ? transientReason
    : associationStatus === 'unresolved'
    ? resolverNotes ?? 'Backend association status is unresolved.'
    : !bodyId
      ? resolverNotes ?? 'Backend association has no body id.'
      : lane === 'unknown'
        ? resolverNotes ?? 'Backend association has unknown lane.'
        : null;

  return {
    source: 'existing',
    id: `existing-${marketId ?? stationId ?? index}`,
    market_id: marketId,
    name,
    station_type: stationType,
    body_id: bodyId,
    body_name: bodyName ?? stationBodyName,
    body_match_confidence: associationConfidence === 'exact'
      ? 'exact'
      : associationConfidence === 'strong_inference' || associationConfidence === 'weak_inference'
        ? 'inferred'
        : 'unresolved',
    body_match_reason: resolverNotes ?? `Backend association source: ${associationSource}.`,
    lane,
    association_status: associationStatus,
    association_confidence: associationConfidence,
    association_source: associationSource,
    economy: readString(record.primary_economy) ?? readEconomyName(record.economies, 0),
    secondary_economy: readString(record.secondary_economy) ?? readEconomyName(record.economies, 1),
    pad_size: readString(record.landing_pad_size) ?? readString(record.pad_size),
    distance_from_star: readNumber(record.distance_from_star ?? record.distance_to_arrival),
    unresolved_reason: unresolvedReason,
    transient: transientReason != null,
    transient_reason: transientReason,
    raw: {
      station_id: stationId,
      body_name: stationBodyName,
      station_type: stationType,
    },
  };
}

function isUnresolvedPermanentInfrastructure(structure: ExistingStructure): boolean {
  return !isExistingSlotOccupant(structure) && !structure.transient;
}

function hasConfirmedPermanentStationBodyLink(station: Partial<SystemStation> & Record<string, unknown>): boolean {
  const stationType = readString(station.station_type) ?? readString(station.type);
  const status = readAssociationStatus(station.association_status);
  const lane = readLane(station.lane);
  const bodyId = readBodyId(station.body_id) ?? readBodyId(station.local_body_id);
  return Boolean(
    status === 'confirmed'
    && bodyId
    && (lane === 'orbital' || lane === 'surface')
    && !isTransientStationType(stationType),
  );
}

function matchStationBody(
  bodies: SystemBody[],
  explicitBodyId: string | null,
  stationBodyName: string | null,
  stationDistanceFromStar: number | null,
): StationBodyMatch {
  if (explicitBodyId) {
    const body = bodies.find((candidate) => sameBodyId(candidate.id, explicitBodyId));
    if (body) {
      return { body, confidence: 'exact', reason: 'Matched exact body id.', source: 'frontend_body_id_fallback' };
    }
    return { body: null, confidence: 'unresolved', reason: 'Station body id does not match a known body.', source: 'frontend_body_id_fallback' };
  }

  if (stationBodyName) {
    const bodyName = normaliseName(stationBodyName);
    const matches = bodies.filter((candidate) => normaliseName(candidate.name) === bodyName);
    if (matches.length === 1) {
      return {
        body: matches[0],
        confidence: 'inferred',
        reason: 'Matched station body name; verify against backend resolver metadata.',
        source: 'frontend_body_name_fallback',
      };
    }
    if (matches.length > 1) {
      return {
        body: null,
        confidence: 'unresolved',
        reason: 'Station body name matches multiple bodies.',
        source: 'frontend_body_name_fallback',
      };
    }
  }

  if (stationDistanceFromStar != null && stationDistanceFromStar > 0) {
    const matches = bodies.filter((candidate) => {
      const bodyDistance = readNumber(candidate.distance_from_star);
      return bodyDistance != null && Math.abs(bodyDistance - stationDistanceFromStar) <= 0.01;
    });
    if (matches.length === 1) {
      return {
        body: matches[0],
        confidence: 'inferred',
        reason: 'Inferred from unique distance-from-star match.',
        source: 'frontend_distance_fallback',
      };
    }
    if (matches.length > 1) {
      return {
        body: null,
        confidence: 'unresolved',
        reason: 'Distance-from-star match is ambiguous.',
        source: 'frontend_distance_fallback',
      };
    }
  }

  return {
    body: null,
    confidence: 'unresolved',
    reason: 'No reliable body association is available.',
    source: 'frontend_unresolved_fallback',
  };
}

function readEconomyName(value: unknown, index: number): string | null {
  if (!Array.isArray(value)) return null;
  const entry = value[index];
  if (!entry || typeof entry !== 'object') return null;
  return readString((entry as Record<string, unknown>).name);
}

function readString(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function readBodyId(value: unknown): string | null {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return readString(value);
}

function readLane(value: unknown): ExistingStructureLane {
  const lane = readString(value);
  return lane === 'orbital' || lane === 'surface' ? lane : 'unknown';
}

function readAssociationStatus(value: unknown): ExistingStructureAssociationStatus | null {
  const status = readString(value);
  if (status === 'confirmed' || status === 'inferred' || status === 'unresolved') return status;
  return null;
}

function readAssociationConfidence(value: unknown): ExistingStructureAssociationConfidence {
  const confidence = readString(value);
  if (
    confidence === 'exact'
    || confidence === 'strong_inference'
    || confidence === 'weak_inference'
    || confidence === 'unresolved'
  ) {
    return confidence;
  }
  return 'unresolved';
}

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function normaliseName(value?: string | null): string {
  return (value ?? '').trim().replace(/\s+/g, ' ').toLowerCase();
}

function normaliseToken(value?: string | null): string {
  return (value ?? '').trim().replace(/[\s_-]+/g, '').toLowerCase();
}

function readableStationType(value: string): string {
  return value
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim();
}
