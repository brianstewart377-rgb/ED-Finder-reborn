// A system's page: the system, its bodies, rings and stations, put together
// from the catalogue's four answers.
// Offered to the app through ./client, like everything in this folder.
import { FINDER_F4_ENABLED } from '$lib/config';
import { ApiError } from '@ed-finder/api-client/core';
import { parseId64 } from '@ed-finder/api-client/id64';
import type { Id64 } from '@ed-finder/api-client/id64';
import type { BodyV1, RingV1, StationV1, SystemV1 } from './generated';
import {
  getV3CurrentGeneration,
  getV3System,
  listV3SystemBodies,
  listV3SystemRings,
  listV3SystemStations,
} from './generated/sdk.gen';
import { SOL_ID64 } from './sol';

/** One catalogued ring or belt of a body (V3 canonical generation). */
export type SystemDetailRing = Readonly<{
  /** Canonical V3 ring key (lossless decimal). */
  ringKey: string;
  name: string;
  kind: 'RING' | 'BELT';
  ringType: string | null;
  innerRadiusKm: number | null;
  outerRadiusKm: number | null;
  /** V3 reserve-level public code (`pristine` … `depleted`); null when unobserved. */
  reserveLevel: string | null;
  /**
   * Catalogued ring signals: mining hotspots by commodity public code
   * (`platinum`, `lowtemperaturediamond`, …), largest count first.
   */
  hotspots: readonly Readonly<{ signalType: string; count: number }>[];
  /** Whether the source recorded this ring's signal list as complete. */
  signalsComplete: boolean | null;
}>;

/** One catalogued body as Inspect renders it (V3 canonical generation). */
export type SystemDetailBody = Readonly<{
  /**
   * Frontier in-system BodyID, when uniquely available as a safe integer.
   * System Map selection requires this ID; parent links use the key below.
   */
  id: number | null;
  /** Canonical V3 body key (lossless decimal). */
  bodyKey: string;
  /** Canonical V3 parent key, which may refer to an unloaded body. */
  directParentBodyKey: string | null;
  name: string;
  /** V3 public body-type code: `star`, `planet`, `barycentre`, `belt_cluster`. */
  bodyType: string | null;
  /**
   * Raw catalogued class; display verbatim. It is the galaxy tables' own
   * where the body's row holds one, otherwise the published derived
   * generation's.
   */
  bodyClass: string | null;
  /** Named only when the class came from the derived generation. */
  bodyClassSource: 'ratings-v4-derived' | null;
  isMainStar: boolean | null;
  spectralClass: string | null;
  luminosityClass: string | null;
  isLandable: boolean | null;
  radiusKm: number | null;
  semiMajorAxisKm: number | null;
  distanceFromArrivalLs: number | null;
  rings: readonly SystemDetailRing[];
  signals: readonly Readonly<{ signalType: string; count: number }>[];
}>;

/** One catalogued economy of a station, as the V3 catalogue records it. */
export type SystemDetailStationEconomy = Readonly<{
  /** V3 economy public code (`extraction`, `high_tech`, …). */
  economy: string;
  /**
   * The catalogued share, 0–1, when a source recorded one. Always null from the
   * Spansh import, which keeps designations but not Spansh's uncalibrated scale.
   */
  weight: number | null;
  isPrimary: boolean;
  isSecondary: boolean;
}>;

export type SystemDetailStation = Readonly<{
  /** Canonical V3 station key (lossless decimal). */
  stationKey: string;
  name: string;
  stationType: string | null;
  distanceFromArrivalLs: number | null;
  /**
   * Canonical body key of the body the station is placed on or orbits, only
   * when the catalogue resolved that placement (`BODY_RESOLVED`).
   */
  bodyKey: string | null;
  /** Primary first, then secondary, then the rest by share. */
  economies: readonly SystemDetailStationEconomy[];
  /** Observation time, when supplied; the canonical station read has none. */
  lastObservedAt?: string | null;
}>;

/**
 * Inspect's system detail, read from the published V3 canonical generation
 * (`/api/v1/systems/{id}` + `/bodies`, `/rings`, `/stations`). Only ACTIVE
 * bodies, rings and stations are current catalogue objects; rows the
 * generation retains as MISSING_CANDIDATE or RETIRED are omitted. Population,
 * economy, security, allegiance, government and development fit are not part
 * of the V3 canonical catalogue, so they are deliberately absent here.
 */
export type SystemDetail = Readonly<{
  id64: Id64;
  name: string;
  /** The system's own catalogue lifecycle: ACTIVE, MISSING_CANDIDATE, RETIRED. */
  lifecycleState: string;
  /** F4 omits an unknown legacy origin; only Sol is known to occupy (0, 0, 0). */
  x?: number;
  y?: number;
  z?: number;
  sourceBodyCount: number | null;
  loadedBodyCount: number;
  sourceUpdatedAt: string | null;
  /** Identity already read by the canonical publication fence. */
  canonicalGenerationId?: string;
  canonicalGenerationLabel?: string;
  bodies: readonly SystemDetailBody[];
  stations: readonly SystemDetailStation[];
}>;

const SAFE_DECIMAL = /^(0|[1-9][0-9]{0,15})$/;
const ACTIVE = 'ACTIVE';

/** Unique, safe Frontier BodyIDs keyed by canonical body key. */
function frontierBodyIds(bodies: readonly BodyV1[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const body of bodies) {
    const raw = body.frontier_body_id;
    if (raw != null) counts.set(raw, (counts.get(raw) ?? 0) + 1);
  }
  const ids = new Map<string, number>();
  for (const body of bodies) {
    const raw = body.frontier_body_id;
    if (raw == null || counts.get(raw) !== 1 || !SAFE_DECIMAL.test(raw))
      continue;
    const value = Number(raw);
    if (Number.isSafeInteger(value)) ids.set(body.body_id, value);
  }
  return ids;
}

/** Main star first, then by arrival distance (unknown last), then name. */
function bodyOrder(left: SystemDetailBody, right: SystemDetailBody): number {
  const main =
    Number(right.isMainStar === true) - Number(left.isMainStar === true);
  if (main !== 0) return main;
  const leftDistance = left.distanceFromArrivalLs ?? Number.POSITIVE_INFINITY;
  const rightDistance = right.distanceFromArrivalLs ?? Number.POSITIVE_INFINITY;
  if (leftDistance !== rightDistance)
    return leftDistance < rightDistance ? -1 : 1;
  return left.name.localeCompare(right.name);
}

/** Pure V3 canonical → Inspect mapping; exported for tests. */
export function systemDetailFromV3(
  system: SystemV1,
  bodies: readonly BodyV1[],
  rings: readonly RingV1[],
  stations: readonly StationV1[],
): SystemDetail {
  const current = bodies.filter((body) => body.lifecycle_state === ACTIVE);
  const ids = frontierBodyIds(current);
  const ringsByBody = new Map<string, SystemDetailRing[]>();
  for (const ring of rings) {
    if (ring.lifecycle_state !== ACTIVE) continue;
    const list = ringsByBody.get(ring.body_id) ?? [];
    list.push({
      ringKey: ring.ring_id,
      name: ring.name,
      kind: ring.kind,
      ringType: ring.ring_type,
      innerRadiusKm: ring.inner_radius_km,
      outerRadiusKm: ring.outer_radius_km,
      reserveLevel: ring.reserve_type,
      hotspots: ring.signals
        .filter((signal) => signal.count > 0)
        .map((signal) => ({
          signalType: signal.signal_type,
          count: signal.count,
        }))
        .sort(
          (left, right) =>
            right.count - left.count ||
            left.signalType.localeCompare(right.signalType),
        ),
      signalsComplete: ring.signals_complete,
    });
    ringsByBody.set(ring.body_id, list);
  }
  return {
    id64: parseId64(system.id64),
    name: system.name,
    lifecycleState: system.lifecycle_state,
    x: system.x_ly,
    y: system.y_ly,
    z: system.z_ly,
    sourceBodyCount: system.source_body_count,
    loadedBodyCount: system.loaded_body_count,
    sourceUpdatedAt: system.source_updated_at,
    bodies: current
      .map((body): SystemDetailBody => ({
        id: ids.get(body.body_id) ?? null,
        bodyKey: body.body_id,
        directParentBodyKey: body.direct_parent_body_id,
        name: body.name,
        bodyType: body.body_type,
        bodyClass: body.body_class ?? null,
        bodyClassSource:
          body.body_class != null ? (body.body_class_source ?? null) : null,
        isMainStar: body.is_main_star,
        spectralClass: body.spectral_class,
        luminosityClass: body.luminosity_class,
        isLandable: body.is_landable,
        radiusKm: body.radius_km,
        semiMajorAxisKm: body.semi_major_axis_km,
        distanceFromArrivalLs: body.distance_from_arrival_ls,
        rings: ringsByBody.get(body.body_id) ?? [],
        signals: body.signals.map((signal) => ({
          signalType: signal.signal_type,
          count: signal.count,
        })),
      }))
      .sort(bodyOrder),
    stations: stations
      .filter((station) => station.lifecycle_state === ACTIVE)
      .map(stationDetail)
      .sort(stationOrder),
  };
}

const BODY_RESOLVED = 'BODY_RESOLVED';

/** One V3 station as Inspect renders it; a body only when resolved. */
function stationDetail(station: StationV1): SystemDetailStation {
  return {
    stationKey: station.station_id,
    name: station.name,
    stationType: station.station_type,
    distanceFromArrivalLs: station.distance_from_arrival_ls,
    bodyKey:
      station.association_state === BODY_RESOLVED ? station.body_id : null,
    economies: station.economies.map(stationEconomy).sort(economyOrder),
    lastObservedAt: null,
  };
}

function stationEconomy(
  economy: StationV1['economies'][number],
): SystemDetailStationEconomy {
  return {
    economy: economy.economy,
    weight: economy.economy_weight,
    isPrimary: economy.is_primary,
    isSecondary: economy.is_secondary,
  };
}

/** Primary, then secondary, then by share (unknown last), then by code. */
function economyOrder(
  left: SystemDetailStationEconomy,
  right: SystemDetailStationEconomy,
): number {
  const rank = (economy: SystemDetailStationEconomy) =>
    economy.isPrimary ? 0 : economy.isSecondary ? 1 : 2;
  const byRank = rank(left) - rank(right);
  if (byRank !== 0) return byRank;
  const byWeight = (right.weight ?? -1) - (left.weight ?? -1);
  if (byWeight !== 0) return byWeight;
  return left.economy.localeCompare(right.economy);
}

/** By arrival distance (unknown last), then name. */
function stationOrder(
  left: SystemDetailStation,
  right: SystemDetailStation,
): number {
  const leftDistance = left.distanceFromArrivalLs ?? Number.POSITIVE_INFINITY;
  const rightDistance = right.distanceFromArrivalLs ?? Number.POSITIVE_INFINITY;
  if (leftDistance !== rightDistance)
    return leftDistance < rightDistance ? -1 : 1;
  return left.name.localeCompare(right.name);
}

/** Attempts before a read that keeps straddling publications fails closed. */
const INSPECT_GENERATION_ATTEMPTS = 2;

/**
 * Inspect's system read: the generation-pinned V3 canonical routes, fetched in
 * parallel. Each route resolves the canonical pointer on its own, so the four
 * reads are fenced by the published generation identity read before and after
 * them: an unchanged generation and publication sequence proves no publication
 * happened in between and every part came from one immutable generation. Any
 * part's error (a 404 included) is only reported once the fence holds. A read
 * that straddles a publication is retried once, then fails closed with a
 * retryable 503. An unknown system rejects with the system route's 404
 * ApiError; no published generation rejects with 503.
 */
/** What the system route alone says of a system. */
export type SystemSummary = Readonly<{
  /** `null` when the catalogue has no name for it. */
  name: string | null;
  /** How many bodies we hold for it, stars included. */
  loadedBodyCount: number | null;
  /** How many the game says it has (its scan count), where that is known. */
  sourceBodyCount: number | null;
}>;

/**
 * A system's name and body counts, with one small request: the system route
 * alone, without its bodies, rings, stations or the generation fence
 * getSystem keeps. For a label (the selected-system bar) and for saying how
 * much of a system is known, never for anything read with the bodies.
 */
export async function getSystemSummary(
  id64: Id64,
  signal?: AbortSignal,
): Promise<SystemSummary> {
  const { data } = await getV3System({
    path: { system_id: parseId64(id64) },
    throwOnError: true,
    signal,
  });
  const system = data as {
    name?: unknown;
    loaded_body_count?: unknown;
    source_body_count?: unknown;
  };
  const count = (value: unknown): number | null =>
    typeof value === 'number' && Number.isInteger(value) && value >= 0
      ? value
      : null;
  return {
    name:
      typeof system.name === 'string' && system.name.trim()
        ? system.name
        : null,
    loadedBodyCount: count(system.loaded_body_count),
    sourceBodyCount: count(system.source_body_count),
  };
}

export async function getSystem(
  id64: Id64,
  signal?: AbortSignal,
): Promise<SystemDetail> {
  const path = { system_id: parseId64(id64) };
  for (let attempt = 0; attempt < INSPECT_GENERATION_ATTEMPTS; attempt += 1) {
    const before = await getV3CurrentGeneration({ throwOnError: true, signal });
    // Settle every part (including a 404) before judging it: a system that
    // was absent from the old generation may exist in a newly published one.
    const [system, bodies, rings, stations] = await Promise.allSettled([
      getV3System({ path, throwOnError: true, signal }),
      listV3SystemBodies({ path, throwOnError: true, signal }),
      listV3SystemRings({ path, throwOnError: true, signal }),
      listV3SystemStations({ path, throwOnError: true, signal }),
    ]);
    const after = await getV3CurrentGeneration({ throwOnError: true, signal });
    if (
      before.data.generation_id !== after.data.generation_id ||
      before.data.publication_sequence !== after.data.publication_sequence
    )
      continue;
    for (const part of [system, bodies, rings, stations])
      if (part.status === 'rejected') throw part.reason;
    if (
      system.status === 'fulfilled' &&
      bodies.status === 'fulfilled' &&
      rings.status === 'fulfilled' &&
      stations.status === 'fulfilled'
    ) {
      const detail = {
        ...systemDetailFromV3(
          system.value.data,
          bodies.value.data,
          rings.value.data,
          stations.value.data,
        ),
        canonicalGenerationId: after.data.generation_id,
        canonicalGenerationLabel: after.data.generation_key,
      };
      if (
        FINDER_F4_ENABLED &&
        detail.id64 !== SOL_ID64 &&
        detail.x === 0 &&
        detail.y === 0 &&
        detail.z === 0
      ) {
        return { ...detail, x: undefined, y: undefined, z: undefined };
      }
      return detail;
    }
  }
  throw new ApiError(
    503,
    `/api/v1/systems/${path.system_id}`,
    null,
    'The catalogue was republished while this system was loading',
  );
}
