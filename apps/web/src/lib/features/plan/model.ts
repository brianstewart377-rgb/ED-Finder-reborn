import type {
  PlanBodiesV1,
  PlanFacilityV1,
  PlanRequestV1,
  PlanSiteV1,
  PlanSystemBodyV1,
} from '$lib/api';
import { parseId64 } from '$lib/domain/id64';
import type { ColonyProjectRecord } from '$lib/persistence/storage';
import {
  beltAliases,
  beltBodyRefs,
  facilityFitsLocation,
  isBeltCluster,
  nonCurrentLocation,
  resolvePlanLocations,
  type PlannerLocation,
} from './locations';

export const MAX_SITES = 300;
export const MAX_IMPORT_BYTES = 2 * 1024 * 1024;
export const PRIMARY_PORT_PROTECTION =
  "The Primary Port can't be removed, and once it is being built its type and body can't change (RB-0008).";

export interface PlannerSite extends PlanSiteV1 {
  schema: 'cpe_site_v1';
  body_ref: string | null;
  name: string;
}

export interface SlotCounts {
  orbital_slots: number | null;
  surface_slots: number | null;
}

export interface PlannerModel {
  system_id64: string;
  sites: PlannerSite[];
  slots: Record<string, SlotCounts>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function boundedText(
  value: unknown,
  max: number,
  empty = false,
): value is string {
  return (
    typeof value === 'string' &&
    value.length <= max &&
    (empty || value.length > 0)
  );
}

export function isPlannerSite(value: unknown): value is PlannerSite {
  return (
    isRecord(value) &&
    value.schema === 'cpe_site_v1' &&
    boundedText(value.site_id, 64) &&
    boundedText(value.facility_id, 64) &&
    (value.body_ref === null || boundedText(value.body_ref, 64)) &&
    (value.slot_kind === 'orbital' || value.slot_kind === 'surface') &&
    typeof value.status === 'string' &&
    ['planned', 'building', 'complete', 'demolished'].includes(value.status) &&
    boundedText(value.name, 64, true)
  );
}

function validSlot(value: unknown): value is number | null {
  return (
    value === null ||
    (typeof value === 'number' &&
      Number.isInteger(value) &&
      value >= 0 &&
      value <= 64)
  );
}

function readSlots(value: unknown, strict = false): Record<string, SlotCounts> {
  if (!isRecord(value)) {
    if (strict) throw new Error('Slot counts must be an object.');
    return {};
  }
  const entries: [string, SlotCounts][] = [];
  for (const [ref, counts] of Object.entries(value)) {
    if (
      !boundedText(ref, 64) ||
      !isRecord(counts) ||
      !validSlot(counts.orbital_slots) ||
      !validSlot(counts.surface_slots)
    ) {
      if (strict)
        throw new Error(
          'Each slot count must be Unknown (null) or an integer from 0 to 64.',
        );
      continue;
    }
    entries.push([
      ref,
      {
        orbital_slots: counts.orbital_slots,
        surface_slots: counts.surface_slots,
      },
    ]);
  }
  return Object.fromEntries(entries);
}

export function emptyPlan(systemId: string): PlannerModel {
  return { system_id64: parseId64(systemId), sites: [], slots: {} };
}

export function findPlannerProject(
  projects: Record<string, ColonyProjectRecord>,
  systemId: string,
): ColonyProjectRecord | undefined {
  const matches = Object.values(projects).filter(
    (project) => project.system_id64 === systemId && !project.archived_at,
  );
  return (
    matches.find((project) =>
      project.build_plan_placements.some(
        (placement) => placement.schema === 'cpe_site_v1',
      ),
    ) ??
    matches.find((project) =>
      Object.hasOwn(project.selected_body_assignments, 'cpe_slots_v1'),
    ) ??
    matches[0]
  );
}

export function recordToPlanner(record: ColonyProjectRecord): PlannerModel {
  return {
    system_id64: record.system_id64,
    sites: record.build_plan_placements.flatMap((site) =>
      isPlannerSite(site) ? [{ ...site }] : [],
    ),
    slots: readSlots(record.selected_body_assignments.cpe_slots_v1),
  };
}

export function plannerToRecord(
  model: PlannerModel,
  existing?: ColonyProjectRecord,
  options: { id?: string; now?: string; systemName?: string } = {},
): ColonyProjectRecord {
  if (existing && existing.system_id64 !== model.system_id64)
    throw new Error('Cannot save a plan into another system’s project.');
  const now = options.now ?? new Date().toISOString();
  const legacy =
    existing?.build_plan_placements.filter(
      (placement) => placement.schema !== 'cpe_site_v1',
    ) ?? [];
  return {
    id: options.id ?? `colony-${model.system_id64}-${crypto.randomUUID()}`,
    system_id64: parseId64(model.system_id64),
    system_name: options.systemName ?? `System ${model.system_id64}`,
    project_name: 'Colony plan',
    declared_roles: [],
    status: 'draft',
    archived_at: null,
    created_at: now,
    ...existing,
    updated_at: now,
    build_plan_placements: [
      ...legacy,
      ...model.sites.map((site) => ({ ...site })),
    ],
    selected_body_assignments: {
      ...existing?.selected_body_assignments,
      cpe_slots_v1: Object.fromEntries(
        Object.entries(model.slots).map(([ref, counts]) => [
          ref,
          { ...counts },
        ]),
      ),
    },
  };
}

export function toPlanRequest(
  model: PlannerModel,
  data: PlanBodiesV1,
): PlanRequestV1 {
  if (model.system_id64 !== data.system_id64)
    throw new Error('Bodies belong to a different system.');
  const hasEconomyFacts = data.economy_facts_generation_id != null;
  const referenced = new Set(model.sites.map((site) => site.body_ref));
  // A saved site may still use an alias the belt no longer selects, for
  // example a cluster that has since been retired. It is still the same belt:
  // every alias is evaluated as ONE belt location (the current reference for
  // an active belt, else the first alias a site uses), so all its sites share
  // one slot total and keep the belt's properties (ringed, no surface). Only
  // the request is normalised; saved site references are never rewritten.
  const evaluationRef = new Map<string, string>();
  const evaluatedBelts: { body_ref: string; parent_ref: string | null }[] = [];
  for (const belt of data.belts) {
    const aliases = beltAliases(data, belt);
    const canonical =
      belt.lifecycle_state === 'ACTIVE'
        ? beltBodyRefs(data, belt)[0]
        : aliases.find((ref) => referenced.has(ref));
    if (canonical === undefined || evaluationRef.has(canonical)) continue;
    for (const alias of aliases)
      if (!evaluationRef.has(alias)) evaluationRef.set(alias, canonical);
    evaluatedBelts.push({
      body_ref: canonical,
      parent_ref: belt.parent_body_id,
    });
  }
  const beltRefs = new Set(evaluationRef.keys());
  return {
    system_id64: model.system_id64,
    bodies: [
      ...data.bodies
        .filter((body) =>
          body.lifecycle_state === 'ACTIVE'
            ? !isBeltCluster(body)
            : referenced.has(body.body_id) && !beltRefs.has(body.body_id),
        )
        .map((body) => ({
          body_ref: body.body_id,
          orbital_slots: model.slots[body.body_id]?.orbital_slots ?? null,
          surface_slots:
            model.slots[body.body_id]?.surface_slots ??
            surfaceSlotPrediction(body),
          is_landable: body.is_landable,
          is_ringed: body.has_rings,
          ...(hasEconomyFacts
            ? {
                economy: {
                  kind: body.economy_kind ?? null,
                  parent_ref: body.economy_parent_id ?? null,
                  in_binary_pair: body.in_binary_pair ?? null,
                  tidally_locked: body.tidally_locked ?? null,
                  volcanism: body.volcanism_present ?? null,
                  terraformable: body.terraformable ?? null,
                  has_rings: body.has_rings,
                  geological_signals:
                    body.signals_complete !== true &&
                    body.geological_signals === 0
                      ? null
                      : body.geological_signals,
                  biological_signals:
                    body.signals_complete !== true &&
                    body.biological_signals === 0
                      ? null
                      : body.biological_signals,
                },
              }
            : {}),
        })),
      ...evaluatedBelts.map(({ body_ref, parent_ref }) => ({
        body_ref,
        orbital_slots:
          locationSlots(model, data, body_ref)?.orbital_slots ?? null,
        surface_slots: 0,
        is_landable: false,
        is_ringed: true,
        ...(hasEconomyFacts
          ? { economy: { kind: null, parent_ref, has_rings: true } }
          : {}),
      })),
    ],
    ...(hasEconomyFacts
      ? {
          system_economy: {
            reserve: data.reserve_level ?? null,
            has_black_hole: data.has_black_hole ?? false,
            has_white_dwarf: data.has_white_dwarf ?? false,
            has_neutron_star: data.has_neutron_star ?? false,
          },
        }
      : {}),
    sites: model.sites.map(
      ({ site_id, facility_id, body_ref, slot_kind, status, name }) => ({
        site_id,
        facility_id,
        body_ref:
          body_ref === null ? null : (evaluationRef.get(body_ref) ?? body_ref),
        slot_kind,
        status,
        name,
      }),
    ),
  };
}

/** Imported sites must use current locations, without hidden clusters or barycentres. */
export function rejectInvalidLocations(
  model: PlannerModel,
  data: PlanBodiesV1,
): void {
  if (model.system_id64 !== data.system_id64)
    throw new Error('Bodies belong to a different system.');
  const allowed = new Set(
    resolvePlanLocations(data).map((location) => location.body_ref),
  );
  const hidden = new Map(
    data.bodies
      .filter((body) => !allowed.has(body.body_id))
      .map((body) => [body.body_id, body]),
  );
  for (const site of model.sites) {
    const nonCurrent = nonCurrentLocation(data, site.body_ref);
    if (nonCurrent) {
      const reason =
        nonCurrent.lifecycle_state === 'RETIRED'
          ? 'retired'
          : 'missing from the latest data';
      throw new Error(
        `“${nonCurrent.name}” is no longer in the current galaxy data (${reason}). Move this site to a current body, or to Unplaced, before importing.`,
      );
    }
    const body = site.body_ref === null ? undefined : hidden.get(site.body_ref);
    if (body)
      throw new Error(
        `“${body.name}” is not a buildable location. Use the belt's first cluster through its belt row, or move this site to Unplaced before importing.`,
      );
  }
}

/** Only use after explicit import confirmation; never rewrite existing saved plans. */
export function unplaceMissingLocations(
  model: PlannerModel,
  data: PlanBodiesV1,
): PlannerModel {
  if (model.system_id64 !== data.system_id64)
    throw new Error('Bodies belong to a different system.');
  const allowed = new Set(
    resolvePlanLocations(data).map((location) => location.body_ref),
  );
  return {
    ...model,
    sites: model.sites.map((site) =>
      site.body_ref !== null && !allowed.has(site.body_ref)
        ? { ...site, body_ref: null }
        : site,
    ),
  };
}

/**
 * Every saved reference the plan check treats as this location, the location's
 * own reference first: all of a belt's aliases (its clusters and `belt:<id>`),
 * or just the reference itself for anything else. See `toPlanRequest`.
 */
export function locationAliases(
  data: PlanBodiesV1 | undefined,
  bodyRef: string,
): string[] {
  for (const belt of data?.belts ?? []) {
    const aliases = beltAliases(data!, belt);
    if (aliases.includes(bodyRef))
      return [bodyRef, ...aliases.filter((ref) => ref !== bodyRef)];
  }
  return [bodyRef];
}

/** Only the API's successful surface prediction supplies a default total. */
export function surfaceSlotPrediction(
  body: PlanSystemBodyV1 | null | undefined,
): number | null {
  return body?.is_landable === true && body.prediction_status === 'predicted'
    ? (body.predicted_surface_slots ?? null)
    : null;
}

/** Entered totals win; otherwise a body's surface prediction is used without saving it. */
export function locationSlots(
  model: PlannerModel,
  data: PlanBodiesV1 | undefined,
  bodyRef: string,
): SlotCounts | undefined {
  const entered = locationAliases(data, bodyRef)
    .map((ref) => model.slots[ref])
    .find(Boolean);
  if (entered?.surface_slots != null) return entered;
  const predicted = surfaceSlotPrediction(
    data?.bodies.find((body) => body.body_id === bodyRef),
  );
  return predicted === null
    ? entered
    : {
        orbital_slots: entered?.orbital_slots ?? null,
        surface_slots: predicted,
      };
}

/** Slots used at a location, counting sites saved under any of its aliases. */
export function countLocationSlots(
  model: PlannerModel,
  data: PlanBodiesV1 | undefined,
  bodyRef: string,
): { orbital_slots: number; surface_slots: number } {
  const aliases = new Set(locationAliases(data, bodyRef));
  const counts = { orbital_slots: 0, surface_slots: 0 };
  for (const site of model.sites) {
    if (
      site.body_ref !== null &&
      aliases.has(site.body_ref) &&
      site.status !== 'demolished'
    )
      counts[`${site.slot_kind}_slots`]++;
  }
  return counts;
}

export function countUsedSlots(
  sites: PlannerSite[],
  bodyRef: string | null,
): { orbital_slots: number; surface_slots: number } {
  const counts = { orbital_slots: 0, surface_slots: 0 };
  for (const site of sites) {
    if (site.body_ref === bodyRef && site.status !== 'demolished')
      counts[`${site.slot_kind}_slots`]++;
  }
  return counts;
}

function siteIndex(model: PlannerModel, siteId: string): number {
  const index = model.sites.findIndex((site) => site.site_id === siteId);
  if (index < 0) throw new Error('This build is no longer in the plan.');
  return index;
}

function checkNewSite(model: PlannerModel, site: PlannerSite): void {
  if (model.sites.length >= MAX_SITES)
    throw new Error(
      'A plan can contain at most 300 sites. Remove a site before adding another.',
    );
  if (!isPlannerSite(site))
    throw new Error(
      'Invalid site: use a name of at most 64 characters and a valid status.',
    );
  if (model.sites.some((existing) => existing.site_id === site.site_id))
    throw new Error('Each site must have a unique site ID.');
}

export function addSite(model: PlannerModel, site: PlannerSite): PlannerModel {
  checkNewSite(model, site);
  return { ...model, sites: [...model.sites, { ...site }] };
}

export function insertSiteBefore(
  model: PlannerModel,
  site: PlannerSite,
  beforeId: string,
): PlannerModel {
  checkNewSite(model, site);
  const index = siteIndex(model, beforeId);
  if (index === 0)
    throw new Error('A build cannot be inserted before the Primary Port.');
  return {
    ...model,
    sites: [
      ...model.sites.slice(0, index),
      { ...site, status: 'planned' },
      ...model.sites.slice(index),
    ],
  };
}

export function canMoveSite(
  model: PlannerModel,
  siteId: string,
  direction: 'up' | 'down',
): boolean {
  const index = model.sites.findIndex((site) => site.site_id === siteId);
  const target = index + (direction === 'up' ? -1 : 1);
  return (
    index > 0 &&
    target > 0 &&
    model.sites[index]?.status === 'planned' &&
    model.sites[target]?.status === 'planned'
  );
}

export function moveSite(
  model: PlannerModel,
  siteId: string,
  direction: 'up' | 'down',
): PlannerModel {
  if (!canMoveSite(model, siteId, direction))
    throw new Error(
      'Only adjacent planned builds can change order, and none can move above the Primary Port.',
    );
  const index = siteIndex(model, siteId);
  const target = index + (direction === 'up' ? -1 : 1);
  const sites = [...model.sites];
  [sites[index], sites[target]] = [sites[target], sites[index]];
  return { ...model, sites };
}

export function isProtectedPrimary(
  model: PlannerModel,
  siteId: string,
): boolean {
  return (
    model.sites[0]?.site_id === siteId &&
    model.sites[0]?.status === 'complete' &&
    model.sites[0]?.body_ref !== null
  );
}

export function primaryPortLocks(site: PlannerSite): {
  remove: true;
  facility: boolean;
  body: boolean;
  status: boolean;
  allowedStatuses: PlannerSite['status'][];
} {
  return {
    remove: true,
    facility: site.status !== 'planned',
    body: site.status !== 'planned' && site.body_ref !== null,
    status: site.status === 'complete' && site.body_ref !== null,
    allowedStatuses: ['planned', 'building', 'complete'],
  };
}

export function removeSite(model: PlannerModel, siteId: string): PlannerModel {
  const index = siteIndex(model, siteId);
  if (index === 0 && primaryPortLocks(model.sites[index]).remove)
    throw new Error(
      "The Primary Port can't be removed. In the game it can only be cancelled together with the whole claim; use Abandon plan to start this system again.",
    );
  return {
    ...model,
    sites: model.sites.filter((site) => site.site_id !== siteId),
  };
}

export function updateSite(
  model: PlannerModel,
  siteId: string,
  patch: Partial<
    Pick<
      PlannerSite,
      'name' | 'status' | 'body_ref' | 'facility_id' | 'slot_kind'
    >
  >,
): PlannerModel {
  const index = siteIndex(model, siteId);
  const current = model.sites[index];
  const updated = { ...current, ...patch };
  if (index === 0) {
    const locks = primaryPortLocks(current);
    if ('status' in patch && !locks.allowedStatuses.includes(updated.status))
      throw new Error("The Primary Port can't be demolished (RB-0008).");
    if (
      (locks.body && updated.body_ref !== current.body_ref) ||
      (locks.facility &&
        (updated.facility_id !== current.facility_id ||
          updated.slot_kind !== current.slot_kind)) ||
      (locks.status && updated.status !== current.status)
    )
      throw new Error(PRIMARY_PORT_PROTECTION);
  }
  if (!isPlannerSite(updated))
    throw new Error('Invalid site: names must be at most 64 characters.');
  return {
    ...model,
    sites: model.sites.map((site, at) => (at === index ? updated : site)),
  };
}

export function abandonPlan(model: PlannerModel): PlannerModel {
  const primary = model.sites[0];
  if (primary && isProtectedPrimary(model, primary.site_id))
    throw new Error(PRIMARY_PORT_PROTECTION);
  return { ...model, sites: [] };
}

export function replacePlan(
  model: PlannerModel,
  replacement: PlannerModel,
): PlannerModel {
  if (model.system_id64 !== replacement.system_id64)
    throw new Error('Cannot replace this plan with a different system.');
  const primary = model.sites[0];
  const next = replacement.sites[0];
  if (
    primary &&
    isProtectedPrimary(model, primary.site_id) &&
    (!next ||
      next.site_id !== primary.site_id ||
      next.facility_id !== primary.facility_id ||
      next.body_ref !== primary.body_ref ||
      next.slot_kind !== primary.slot_kind ||
      next.status !== 'complete')
  )
    throw new Error(PRIMARY_PORT_PROTECTION);
  return replacement;
}

export function setSlotCount(
  model: PlannerModel,
  bodyRef: string,
  kind: 'orbital' | 'surface',
  value: number | null,
): PlannerModel {
  if (!validSlot(value))
    throw new Error('Slot counts must be Unknown or an integer from 0 to 64.');
  return {
    ...model,
    slots: {
      ...model.slots,
      [bodyRef]: {
        ...(model.slots[bodyRef] ?? {
          orbital_slots: null,
          surface_slots: null,
        }),
        [`${kind}_slots`]: value,
      },
    },
  };
}

export function facilitySlotKind(
  facility: PlanFacilityV1,
): 'orbital' | 'surface' {
  return facility.allowed_location.toLowerCase().includes('surface')
    ? 'surface'
    : 'orbital';
}

export function findFreeLocation(
  model: PlannerModel,
  locations: PlannerLocation[],
  facility: PlanFacilityV1,
  data?: PlanBodiesV1,
): string | null {
  const kind = facilitySlotKind(facility);
  const key = `${kind}_slots` as const;
  return (
    locations.find((location) => {
      const available = locationSlots(model, data, location.body_ref)?.[key];
      return (
        facilityFitsLocation(facility, location) &&
        typeof available === 'number' &&
        countLocationSlots(model, data, location.body_ref)[key] < available
      );
    })?.body_ref ?? null
  );
}

export function exportPlan(model: PlannerModel): string {
  return JSON.stringify(
    {
      schema: 'cpe_plan_export_v1',
      system_id64: model.system_id64,
      sites: model.sites,
      slots: model.slots,
    },
    null,
    2,
  );
}

/** Cross-system imports require a separate, explicit confirmation by the caller. */
export function importPlan(
  text: string,
  currentSystemId: string,
  options: { confirmOtherSystem?: boolean } = {},
): PlannerModel {
  if (new TextEncoder().encode(text).byteLength > MAX_IMPORT_BYTES)
    throw new Error('Plan files must be no larger than 2 MB.');
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error('The plan file is not valid JSON.');
  }
  if (!isRecord(value) || value.schema !== 'cpe_plan_export_v1')
    throw new Error('Expected a cpe_plan_export_v1 plan file.');
  if (typeof value.system_id64 !== 'string')
    throw new Error('The system id64 must be a string.');
  const importedSystem = parseId64(value.system_id64);
  const currentSystem = parseId64(currentSystemId);
  if (importedSystem !== currentSystem && !options.confirmOtherSystem)
    throw new Error(
      'This plan belongs to another system. Confirm importing it into this system first.',
    );
  if (
    !Array.isArray(value.sites) ||
    value.sites.length > MAX_SITES ||
    !value.sites.every(isPlannerSite)
  )
    throw new Error(
      'The plan must contain at most 300 valid cpe_site_v1 sites with names up to 64 characters.',
    );
  if (
    new Set(value.sites.map((site) => site.site_id)).size !== value.sites.length
  )
    throw new Error('Each site must have a unique site ID.');
  return {
    system_id64: currentSystem,
    sites: value.sites.map((site) => ({ ...site })),
    slots: readSlots(value.slots, true),
  };
}
