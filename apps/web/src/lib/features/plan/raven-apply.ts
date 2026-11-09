import type { PlanBodiesV1, PlanFacilityV1 } from '$lib/api';
import {
  facilityFitsLocation,
  resolvePlanLocations,
  type PlannerLocation,
} from './locations';
import {
  isPlannerSite,
  MAX_SITES,
  type PlannerModel,
  type PlannerSite,
  type SlotCounts,
} from './model';
import {
  matchRavenBodies,
  normalizeRavenBodyName,
  type RavenImportResult,
} from './raven-import';

type Warning = { code: string; detail: string };
type BeltMatch = { location: PlannerLocation; cluster: bigint | null };
type BeltResolution = BeltMatch | { location: null; cluster: null };

function clusterNumber(name: string, beltName: string): bigint | null {
  const normalizedName = normalizeRavenBodyName(name);
  const prefix = `${normalizeRavenBodyName(beltName)} cluster `;
  if (!normalizedName.startsWith(prefix)) return null;
  const suffix = normalizedName.slice(prefix.length);
  return /^\d+$/.test(suffix) ? BigInt(suffix) : null;
}

/** Match the model's UTF-16 bounds without splitting a Unicode character. */
function boundedName(value: string, limit: number): string {
  let result = '';
  for (const character of value) {
    if (result.length + character.length > limit) break;
    result += character;
  }
  return result;
}

/** Convert already-parsed commander data using only current planner locations. */
export function ravenToPlannerModel(
  result: RavenImportResult,
  data: PlanBodiesV1,
  facilities?: readonly PlanFacilityV1[],
): { model: PlannerModel; warnings: Warning[] } {
  if (!result.ok) throw new Error('The Raven layout could not be imported.');
  if (result.system?.id64 !== data.system_id64)
    throw new Error('The Raven layout belongs to a different system.');

  const warnings = result.warnings.map((warning) => ({ ...warning }));
  const warn = (code: string, detail: string) =>
    warnings.push({ code, detail });
  const locations = resolvePlanLocations(data);
  const byRef = new Map(
    locations.map((location) => [location.body_ref, location]),
  );
  const belts = locations.filter((location) => location.kind === 'belt');
  const byFacility = new Map(
    facilities?.map((facility) => [facility.id, facility]),
  );

  const matchBelt = (name: string | null): BeltResolution | null => {
    if (name === null) return null;
    const normalizedName = normalizeRavenBodyName(name);
    const candidates = new Map<string, BeltMatch>();
    for (const location of belts) {
      const cluster = clusterNumber(name, location.name);
      if (
        normalizedName !== normalizeRavenBodyName(location.name) &&
        cluster === null
      )
        continue;
      candidates.set(location.body_ref, { location, cluster });
    }
    if (candidates.size > 1) {
      warn(
        'ambiguous_body',
        `Body "${name}" matches multiple belt locations; its sites are Unplaced and its slot counts cannot be imported.`,
      );
      // A recognised but ambiguous belt must not fall through to body matching.
      return { location: null, cluster: null };
    }
    const match = candidates.values().next().value;
    if (!match) return null;
    const { location, cluster } = match;
    if (cluster !== null) {
      const representative = data.bodies.find(
        (body) => body.body_id === location.body_ref,
      );
      const first = representative
        ? clusterNumber(representative.name, location.name)
        : null;
      if (cluster !== first)
        warn(
          'belt_cluster_moved',
          `"${name}" was moved to belt "${location.name}". The game builds a belt through its first cluster.`,
        );
    }
    return match;
  };

  // Resolve belt names before normal matching, including clusters absent from
  // the body data. They must not acquire spurious unmatched/hidden warnings.
  const siteBelts = result.sites.map((site) => matchBelt(site.raven_body_name));
  const slotBelts = result.slots.map((slot) => matchBelt(slot.raven_body_name));
  const matched = matchRavenBodies(
    {
      ...result,
      sites: result.sites.filter((_, index) => !siteBelts[index]),
      slots: result.slots.filter((_, index) => !slotBelts[index]),
    },
    data.bodies,
  );
  warnings.push(...matched.warnings);
  const visibleRef = (
    ref: string | null,
    name: string | null,
  ): string | null => {
    if (ref === null || byRef.has(ref)) return ref;
    warn(
      'hidden_body',
      `"${name}" is not a visible planner location; its sites are Unplaced and its slot counts cannot be imported.`,
    );
    return null;
  };

  const ids = new Set<string>();
  const sites: PlannerSite[] = [];
  let siteMatchIndex = 0;
  for (const [index, source] of result.sites.entries()) {
    const belt = siteBelts[index];
    const bodyRef = belt
      ? (belt.location?.body_ref ?? null)
      : visibleRef(
          matched.siteBodyRefs[siteMatchIndex++],
          source.raven_body_name,
        );
    const originalId = `raven:${source.raven_site_id}`;
    const baseId =
      source.raven_site_id.trim() && originalId.length <= 64
        ? originalId
        : `raven:${boundedName(source.raven_site_id, 48) || 'site'}:${index + 1}`;
    let siteId = baseId;
    let duplicate = 1;
    while (ids.has(siteId)) {
      const suffix = `:${++duplicate}`;
      siteId = `${boundedName(baseId, 64 - suffix.length)}${suffix}`;
    }
    if (siteId !== originalId)
      warn(
        'site_id_changed',
        `Site "${source.name}" needed a new unique planner site ID.`,
      );

    const name = boundedName(source.name, 64);
    if (name !== source.name)
      warn(
        'name_truncated',
        `Site "${name}" was shortened to fit the planner's 64-character name limit.`,
      );
    const site: PlannerSite = {
      schema: 'cpe_site_v1',
      site_id: siteId,
      facility_id: source.facility_id,
      slot_kind: source.slot_kind,
      status: source.status,
      name,
      body_ref: bodyRef,
    };
    if (!isPlannerSite(site)) {
      warn(
        'invalid_site',
        `Site "${source.name}" with facility "${source.facility_id}" is not accepted by the planner; skipped.`,
      );
      continue;
    }
    ids.add(siteId);
    const facility = byFacility.get(site.facility_id);
    const location = bodyRef === null ? undefined : byRef.get(bodyRef);
    if (facility && location && !facilityFitsLocation(facility, location))
      warn(
        'facility_location_mismatch',
        `Site "${site.name}" (${facility.name}) does not fit "${location.name}"; it remains there for the plan check.`,
      );
    sites.push(site);
  }
  if (sites.length > MAX_SITES)
    warn(
      'too_many_sites',
      `Only the first ${MAX_SITES} sites were imported; ${sites.length - MAX_SITES} additional sites were skipped.`,
    );

  const slotRows = new Map<
    string,
    {
      source: RavenImportResult['slots'][number];
      cluster: bigint | null;
    }[]
  >();
  let slotMatchIndex = 0;
  for (const [index, source] of result.slots.entries()) {
    const belt = slotBelts[index];
    const ref = belt
      ? (belt.location?.body_ref ?? null)
      : visibleRef(
          matched.slotBodyRefs[slotMatchIndex++],
          source.raven_body_name,
        );
    if (ref === null) {
      warn(
        'slots_dropped',
        `Slot counts for "${source.raven_body_name}" were dropped because it has no visible planner location.`,
      );
      continue;
    }
    const rows = slotRows.get(ref) ?? [];
    const location = byRef.get(ref)!;
    rows.push({
      source,
      cluster:
        belt?.cluster ??
        (location.kind === 'belt'
          ? clusterNumber(source.raven_body_name, location.name)
          : null),
    });
    slotRows.set(ref, rows);
  }
  const slots = new Map<string, SlotCounts>();
  for (const [ref, rows] of slotRows) {
    const location = byRef.get(ref)!;
    if (location.kind === 'belt') {
      // An explicit cluster row wins over a belt-name row; equal cluster
      // numbers retain Raven's order. Compare integers without precision loss.
      rows.sort((a, b) => {
        const left = a.cluster;
        const right = b.cluster;
        return left === right
          ? 0
          : left === null
            ? 1
            : right === null
              ? -1
              : left < right
                ? -1
                : 1;
      });
      for (const row of rows.slice(1))
        warn(
          'belt_slots_merged',
          `Slot counts for "${row.source.raven_body_name}" were merged into belt "${location.name}"; counts from "${rows[0].source.raven_body_name}" were kept.`,
        );
    }
    const { source } = rows[0];
    const validCount = (count: number | null) =>
      count === null || (Number.isInteger(count) && count >= 0 && count <= 64);
    if (!validCount(source.orbital_slots) || !validCount(source.surface_slots))
      warn(
        'invalid_slots',
        `Invalid slot counts for "${source.raven_body_name}" were changed to Unknown.`,
      );
    slots.set(ref, {
      orbital_slots: validCount(source.orbital_slots)
        ? source.orbital_slots
        : null,
      surface_slots: validCount(source.surface_slots)
        ? source.surface_slots
        : null,
    });
  }

  return {
    model: {
      system_id64: data.system_id64,
      sites: sites.slice(0, MAX_SITES),
      slots: Object.fromEntries(slots),
    },
    warnings,
  };
}
