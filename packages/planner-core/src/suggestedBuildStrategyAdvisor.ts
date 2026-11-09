import type {
  FacilityTemplate,
  OptimiserCandidate,
  OptimiserCandidatePlacement,
  SimulateBuildPlacement,
  SlotPredictionResponse,
  SystemBody,
  SystemDetail,
} from '@ed-finder/api-client/types';
import type { BodyPlannerLane } from './plannerTypes';
import {
  roleCompactLabel,
  roleLabel,
  type DeclaredColonyRole,
  type DeclaredColonyRoleId,
} from './colonyRoles';
import type { ObservedColonyRole } from './colonyRoleReview';
import { resolveExistingInfrastructure } from './existingInfrastructure';
import { compactEconomyLabel, normalisePlanningEconomy } from './planningEconomy';
import { buildBodyDataSlotEstimateMap, resolveSlotCapacity, systemBodyData } from './slotCapacity';
import { placementLaneForTemplate } from './structurePlanning';
import { bodyDisplayName, bodyTags } from './buildPlanLayout';
import { bodyIdKey } from './bodyId';

export interface SuggestedBuildStrategyAdvisor {
  cardLine: string;
  bodyChoice: string;
  existingInfrastructure: string;
  slotPressure: string;
  economyIntent: string;
  roleContext: string;
  roleGaps: string;
  roleSourceContext: string;
  uncertainty: string;
  projectionEffect: string;
  manualBoundary: string;
}

export function buildSuggestedBuildAdvisorHighlights(advisor: SuggestedBuildStrategyAdvisor): string[] {
  const highlights: string[] = [];

  if (/\bverify\/inferred\b|\bverify\b/i.test(advisor.existingInfrastructure)) {
    highlights.push('Verify existing');
  }
  if (/\bunresolved\b|\bunknown-lane\b/i.test(advisor.existingInfrastructure)) {
    highlights.push('Unresolved infra');
  }
  if (/exceed visible capacity|pressure|full after existing/i.test(advisor.slotPressure)) {
    highlights.push('Capacity pressure');
  }
  if (/no slot prediction|slots unknown|estimated/i.test(advisor.slotPressure)) {
    highlights.push('Slot estimate');
  }
  if (/manual review/i.test(advisor.roleGaps) && !/none from available role context/i.test(advisor.roleGaps)) {
    highlights.push('Role review');
  }
  if (/sparse, estimated, or incomplete data/i.test(advisor.uncertainty)) {
    highlights.push('Sparse evidence');
  }
  if (/Support bodies:/i.test(advisor.bodyChoice)) {
    highlights.push('Multi-body');
  }

  return highlights.slice(0, 4);
}

interface AdvisorInput {
  candidate: OptimiserCandidate;
  system?: SystemDetail | null;
  templates?: FacilityTemplate[];
  bodyLabelsById?: Record<string, string>;
  currentPreviewPlacements?: SimulateBuildPlacement[];
  declaredRoles?: DeclaredColonyRole[];
  observedRoles?: ObservedColonyRole[];
  observedRolesLoaded?: boolean;
  slotPredictions?: SlotPredictionResponse | null;
}

type PlacementLike = Pick<SimulateBuildPlacement | OptimiserCandidatePlacement, 'facility_template_id' | 'local_body_id' | 'is_primary_port'>;

interface LaneCounts {
  orbital: number;
  surface: number;
}

interface PlacementLaneSummary {
  byBodyId: Map<string, LaneCounts>;
  unassignedBody: number;
  unknownBody: number;
  unknownLane: number;
  missingTemplate: number;
}

interface RoleGuidanceSummary {
  summary: string;
  gaps: string;
  sources: string;
  hasReviewIssue: boolean;
}

export function buildSuggestedBuildStrategyAdvisor({
  candidate,
  system,
  templates = [],
  bodyLabelsById = {},
  currentPreviewPlacements = [],
  declaredRoles = [],
  observedRoles = [],
  observedRolesLoaded = false,
  slotPredictions = null,
}: AdvisorInput): SuggestedBuildStrategyAdvisor {
  const templatesById = new Map(templates.map((template) => [template.id, template]));
  const bodies = system ? systemBodyData(system) : [];
  const bodiesById = new Map(
    bodies
      .filter((body) => body.id != null)
      .map((body) => [bodyIdKey(body.id), body]),
  );
  const usedBodyIds = candidateBodyIds(candidate);
  const mainBodyId = bodyIdKey(
    candidate.placements.find((placement) => placement.is_primary_port)?.local_body_id
      ?? usedBodyIds[0]
      ?? null,
  ) || null;
  const labelForBody = (bodyId: string) => (
    bodyLabelsById[bodyId] ?? bodyLabelsById[bodyIdKey(bodyId)] ?? (bodiesById.get(bodyId) ? bodyDisplayName(bodiesById.get(bodyId)!) : `Body ${bodyId}`)
  );

  const currentLaneSummary = summarizePlacementLanes(currentPreviewPlacements, templatesById, bodiesById);
  const projectedLaneSummary = summarizePlacementLanes(candidate.placements, templatesById, bodiesById);
  const existingResolution = system ? resolveExistingInfrastructure(system) : null;
  const candidateRoleSignals = candidateRoleSignalsByBody(candidate, templatesById);

  const bodyChoice = describeBodyChoice({
    mainBodyId,
    usedBodyIds,
    bodiesById,
    labelForBody,
    projectedLaneSummary,
  });
  const roleGuidance = describeRoleGuidance({
    usedBodyIds,
    declaredRoles,
    observedRoles,
    observedRolesLoaded,
    candidateRoleSignals,
    labelForBody,
  });
  const existingInfrastructure = describeExistingInfrastructure({
    usedBodyIds,
    existingResolution,
  });
  const slotPressure = describeSlotPressure({
    system,
    bodiesById,
    labelForBody,
    currentLaneSummary,
    projectedLaneSummary,
    slotPredictions,
    existingResolution,
  });
  const economyIntent = describeEconomyIntent(candidate, templatesById);
  const uncertainty = describeUncertainty({
    candidate,
    system,
    bodies,
    projectedLaneSummary,
    slotPredictions,
  });
  const projectionEffect = describeProjectionEffect(candidate, usedBodyIds, projectedLaneSummary);
  const manualBoundary = 'Manual boundary: select projects read-only ghosts, Load explicitly copies this candidate into the editable Build Plan, Run Preview remains explicit, and role guidance does not assign roles or alter ranking/scoring.';

  return {
    cardLine: cardLineFor({
      slotPressure,
      existingInfrastructure,
      uncertainty,
      roleGuidance,
    }),
    bodyChoice,
    existingInfrastructure,
    slotPressure,
    economyIntent,
    roleContext: roleGuidance.summary,
    roleGaps: roleGuidance.gaps,
    roleSourceContext: roleGuidance.sources,
    uncertainty,
    projectionEffect,
    manualBoundary,
  };
}

function describeBodyChoice({
  mainBodyId,
  usedBodyIds,
  bodiesById,
  labelForBody,
  projectedLaneSummary,
}: {
  mainBodyId: string | null;
  usedBodyIds: string[];
  bodiesById: Map<string, SystemBody>;
  labelForBody: (bodyId: string) => string;
  projectedLaneSummary: PlacementLaneSummary;
}) {
  if (!mainBodyId && usedBodyIds.length === 0) {
    return 'Body choice: system-level candidate with no explicit body assignments yet; body and lane selection need manual review.';
  }
  if (!mainBodyId) {
    return `Body choice: ${usedBodyIds.map(labelForBody).join(', ')}. No primary-port body is declared by this candidate.`;
  }

  const mainBody = bodiesById.get(mainBodyId);
  const tags = mainBody ? bodyTags(mainBody).slice(0, 3).join(', ') : null;
  const supportBodies = usedBodyIds.filter((bodyId) => bodyId !== mainBodyId).map(labelForBody);
  const unresolved = projectedLaneSummary.unknownBody > 0
    ? ` ${projectedLaneSummary.unknownBody} placement(s) reference bodies not present in current body data.`
    : '';
  const support = supportBodies.length > 0 ? ` Support bodies: ${supportBodies.join(', ')}.` : '';

  return `Body choice: main body is ${labelForBody(mainBodyId)}${tags ? ` (${tags})` : ''}.${support}${unresolved}`;
}

function describeRoleGuidance({
  usedBodyIds,
  declaredRoles,
  observedRoles,
  observedRolesLoaded,
  candidateRoleSignals,
  labelForBody,
}: {
  usedBodyIds: string[];
  declaredRoles: DeclaredColonyRole[];
  observedRoles: ObservedColonyRole[];
  observedRolesLoaded: boolean;
  candidateRoleSignals: Map<string, Set<DeclaredColonyRoleId>>;
  labelForBody: (bodyId: string) => string;
}): RoleGuidanceSummary {
  const used = new Set(usedBodyIds);
  const declaredOnCandidate = declaredRoles.filter((role) => used.has(bodyIdKey(role.body_id)));
  const observedOnCandidate = observedRolesLoaded
    ? observedRoles.filter((role) => used.has(bodyIdKey(role.body_id)))
    : [];
  const inferredParts = inferredRoleParts(candidateRoleSignals, labelForBody);
  const supportParts = declaredOnCandidate
    .filter((role) => roleSatisfiedByCandidate(role.role_id, candidateRoleSignals.get(bodyIdKey(role.body_id))))
    .map((role) => `${labelForBody(bodyIdKey(role.body_id))} ${roleCompactLabel(role.role_id)}`);
  const roleGapParts = declaredOnCandidate
    .filter((role) => !roleSatisfiedByCandidate(role.role_id, candidateRoleSignals.get(bodyIdKey(role.body_id))))
    .map((role) => gapTextForRole(role, labelForBody(bodyIdKey(role.body_id))));
  const conflictParts = roleConflictParts({
    declaredOnCandidate,
    observedOnCandidate,
    candidateRoleSignals,
    labelForBody,
  });

  const inferredLine = inferredParts.length > 0
    ? `Inferred: candidate appears to support ${inferredParts.slice(0, 4).join('; ')} from projected templates.`
    : 'Inferred: no role pattern from projected templates yet.';
  const declaredLine = supportParts.length > 0
    ? `Declared: supports ${supportParts.slice(0, 4).join('; ')}.`
    : declaredOnCandidate.length > 0
      ? 'Declared: roles are present on projected bodies, but this candidate does not directly satisfy them.'
      : declaredRoles.length > 0
        ? 'Declared: roles exist elsewhere; none are on projected candidate bodies.'
        : 'Declared: none.';
  const observedLine = observedLineFor({
    observedRolesLoaded,
    observedOnCandidate,
    labelForBody,
  });
  const roleReviewParts = [...conflictParts, ...roleGapParts].slice(0, 4);
  const gapLine = roleReviewParts.length > 0
    ? `Role gaps/conflicts: ${roleReviewParts.length} advisory item(s) need manual review. Advisory only; not blockers.`
    : 'Role gaps/conflicts: none from available role context. Advisory only; not blockers.';
  const reviewDetailLine = roleReviewParts.length > 0
    ? `Advisory gap/conflict detail: ${roleReviewParts.join(' ')}`
    : 'Advisory gap/conflict detail: no role gaps or conflicts from available source context.';
  const sourceLine = `${inferredLine} ${declaredLine} ${observedLine} ${reviewDetailLine} Role guidance is display-only and does not change candidate ranking, scoring, Preview, declared roles, or the Build Plan.`;

  return {
    summary: `Role guidance: ${inferredLine} ${declaredLine}`,
    gaps: gapLine,
    sources: `Role sources: ${sourceLine}`,
    hasReviewIssue: roleGapParts.length > 0 || conflictParts.length > 0,
  };
}

function inferredRoleParts(
  candidateRoleSignals: Map<string, Set<DeclaredColonyRoleId>>,
  labelForBody: (bodyId: string) => string,
) {
  return Array.from(candidateRoleSignals.entries())
    .flatMap(([bodyId, roleIds]) => Array.from(roleIds).map((roleId) => (
      `${labelForBody(bodyId)} ${roleCompactLabel(roleId)}`
    )))
    .slice(0, 6);
}

function observedLineFor({
  observedRolesLoaded,
  observedOnCandidate,
  labelForBody,
}: {
  observedRolesLoaded: boolean;
  observedOnCandidate: ObservedColonyRole[];
  labelForBody: (bodyId: string) => string;
}) {
  if (!observedRolesLoaded) {
    return 'Observed: not loaded in Suggested Builds; no observed role evidence is included in this advisor.';
  }
  if (observedOnCandidate.length === 0) {
    return 'Observed: loaded, with no observed role evidence on projected candidate bodies.';
  }
  const parts = observedOnCandidate.slice(0, 4).map((role) => (
    `${labelForBody(bodyIdKey(role.body_id))} ${role.label} (${role.evidenceLabel})`
  ));
  return `Observed: ${parts.join('; ')}.`;
}

function roleConflictParts({
  declaredOnCandidate,
  observedOnCandidate,
  candidateRoleSignals,
  labelForBody,
}: {
  declaredOnCandidate: DeclaredColonyRole[];
  observedOnCandidate: ObservedColonyRole[];
  candidateRoleSignals: Map<string, Set<DeclaredColonyRoleId>>;
  labelForBody: (bodyId: string) => string;
}) {
  const conflicts = new Set<string>();
  declaredOnCandidate.forEach((declared) => {
    const bodyId = bodyIdKey(declared.body_id);
    const body = labelForBody(bodyId);
    const signals = candidateRoleSignals.get(bodyId);
    if (declared.role_id === 'expansion_reserve') {
      conflicts.add(`Declared conflict: ${body} is Expansion Reserve but this candidate projects placement there.`);
    }
    if (isIndustrialRole(declared.role_id) && signals?.has('tourism_agriculture_body')) {
      conflicts.add(`Declared conflict: ${body} ${roleCompactLabel(declared.role_id)} may conflict with projected Tourism / Agri intent.`);
    }
    if (declared.role_id === 'tourism_agriculture_body' && hasIndustrialSignal(signals)) {
      conflicts.add(`Declared conflict: ${body} Tourism / Agri may conflict with projected industrial intent.`);
    }
  });
  observedOnCandidate.forEach((observed) => {
    const bodyId = bodyIdKey(observed.body_id);
    const body = labelForBody(bodyId);
    const declaredHere = declaredOnCandidate.filter((role) => bodyIdKey(role.body_id) === bodyId);
    if (observed.role_id === 'tourism_agriculture_body' && declaredHere.some((role) => isIndustrialRole(role.role_id))) {
      conflicts.add(`Observed conflict: ${body} observed Tourism Focus differs from declared Industrial Core.`);
    }
    if (isIndustrialRole(observed.role_id) && declaredHere.some((role) => role.role_id === 'tourism_agriculture_body')) {
      conflicts.add(`Observed conflict: ${body} observed ${observed.label} differs from declared Tourism / Agri.`);
    }
  });
  return Array.from(conflicts);
}

function gapTextForRole(role: DeclaredColonyRole, bodyLabel: string) {
  switch (role.role_id) {
    case 'main_station_body':
    case 'primary_port_body':
      return `Declared gap: ${bodyLabel} ${roleLabel(role.role_id)} has no station/port placement in this candidate.`;
    case 'industrial_core':
      return `Declared gap: ${bodyLabel} Industrial Core has no industrial/refinery/extraction placement in this candidate.`;
    case 'refinery_core':
      return `Declared gap: ${bodyLabel} Refinery Core has no refinery placement in this candidate.`;
    case 'extraction_support':
      return `Declared gap: ${bodyLabel} Extraction Support has no extraction placement in this candidate.`;
    case 'tourism_agriculture_body':
      return `Declared gap: ${bodyLabel} Tourism / Agriculture Body has no tourism/agriculture placement in this candidate.`;
    case 'security_military_body':
      return `Declared gap: ${bodyLabel} Security / Military Body has no security/military placement in this candidate.`;
    case 'support_body':
      return `Declared gap: ${bodyLabel} Support Body has no support placement in this candidate.`;
    case 'colony_anchor':
      return `Declared gap: ${bodyLabel} Colony Anchor has no primary station/port signal in this candidate.`;
    case 'expansion_reserve':
      return `Declared gap: ${bodyLabel} Expansion Reserve is used by this candidate and needs manual review.`;
    default:
      return `Declared gap: ${bodyLabel} ${roleLabel(role.role_id)} needs manual review.`;
  }
}

function describeExistingInfrastructure({
  usedBodyIds,
  existingResolution,
}: {
  usedBodyIds: string[];
  existingResolution: ReturnType<typeof resolveExistingInfrastructure> | null;
}) {
  if (!existingResolution) {
    return 'Existing infrastructure: system context is unavailable in this view.';
  }
  const permanent = existingResolution.structures.filter((structure) => !structure.transient);
  if (permanent.length === 0) {
    return 'Existing infrastructure: none visible in the current system data.';
  }

  const used = new Set(usedBodyIds);
  const onCandidateBodies = existingResolution.mapped.filter((structure) => used.has(bodyIdKey(structure.body_id)));
  const confirmed = onCandidateBodies.filter((structure) => structure.association_status === 'confirmed').length;
  const inferred = onCandidateBodies.filter((structure) => structure.association_status === 'inferred').length;
  const unresolved = existingResolution.unresolved.length;
  const unknownLane = existingResolution.unresolved.filter((structure) => structure.lane === 'unknown').length;
  const candidateLine = onCandidateBodies.length > 0
    ? `${onCandidateBodies.length} existing slot occupant(s) on projected candidate bodies (${confirmed} confirmed, ${inferred} verify/inferred).`
    : `${existingResolution.mapped.length} mapped existing slot occupant(s) are visible, but none are on projected candidate bodies.`;
  const unresolvedLine = unresolved > 0
    ? ` ${unresolved} unresolved${unknownLane > 0 ? ` / ${unknownLane} unknown-lane` : ''} infrastructure item(s) stay visible but are not forced into a lane.`
    : '';

  return `Existing infrastructure: ${candidateLine} Existing items remain Existing and are not Build Plan placements.${unresolvedLine}`;
}

function describeSlotPressure({
  system,
  bodiesById,
  labelForBody,
  currentLaneSummary,
  projectedLaneSummary,
  slotPredictions,
  existingResolution,
}: {
  system?: SystemDetail | null;
  bodiesById: Map<string, SystemBody>;
  labelForBody: (bodyId: string) => string;
  currentLaneSummary: PlacementLaneSummary;
  projectedLaneSummary: PlacementLaneSummary;
  slotPredictions: SlotPredictionResponse | null;
  existingResolution: ReturnType<typeof resolveExistingInfrastructure> | null;
}) {
  const unresolvedProjection = projectedLaneSummary.unassignedBody
    + projectedLaneSummary.unknownBody
    + projectedLaneSummary.unknownLane
    + projectedLaneSummary.missingTemplate;
  const projectedLaneEntries = Array.from(projectedLaneSummary.byBodyId.entries())
    .flatMap(([bodyId, counts]) => ([
      { bodyId, lane: 'orbital' as BodyPlannerLane, projected: counts.orbital },
      { bodyId, lane: 'surface' as BodyPlannerLane, projected: counts.surface },
    ]))
    .filter((entry) => entry.projected > 0);

  if (!system || projectedLaneEntries.length === 0) {
    if (unresolvedProjection > 0) {
      return `Slot pressure: ${unresolvedProjection} projected placement(s) need body/lane/template resolution before lane capacity can be checked; they remain ghost-only.`;
    }
    return 'Slot pressure: no lane-specific projected occupancy is available for this candidate.';
  }

  const predictionByBodyId = new Map(
    (slotPredictions?.predictions ?? []).map((prediction) => [bodyIdKey(prediction.body_id), prediction]),
  );
  const estimateMap = buildBodyDataSlotEstimateMap(system, slotPredictions?.predictions);
  const entries = projectedLaneEntries.map((entry) => {
    const body = bodiesById.get(entry.bodyId);
    const current = currentLaneSummary.byBodyId.get(entry.bodyId)?.[entry.lane] ?? 0;
    const existing = existingResolution?.byBodyId.get(entry.bodyId)?.[entry.lane].length ?? 0;
    const capacity = body
      ? resolveSlotCapacity(body, predictionByBodyId.get(entry.bodyId) ?? null, entry.lane, estimateMap.get(entry.bodyId))
      : { value: null, estimated: false };
    const remainingBeforeProjection = capacity.value == null ? null : capacity.value - existing - current;
    const remainingAfterProjection = capacity.value == null ? null : capacity.value - existing - current - entry.projected;
    return {
      ...entry,
      body,
      current,
      existing,
      capacity,
      remainingBeforeProjection,
      remainingAfterProjection,
    };
  }).sort((left, right) => {
    const leftOver = (left.remainingAfterProjection ?? 0) < 0 ? 1 : 0;
    const rightOver = (right.remainingAfterProjection ?? 0) < 0 ? 1 : 0;
    if (leftOver !== rightOver) return rightOver - leftOver;
    return right.projected - left.projected;
  });

  const entry = entries[0];
  const laneLabel = entry.lane === 'orbital' ? 'Orbit' : 'Surface';
  const capacityLabel = entry.capacity.value == null
    ? 'capacity unknown'
    : `${entry.capacity.estimated ? 'estimated' : 'predicted'} capacity ${entry.capacity.value}`;
  const prefix = `${labelForBody(entry.bodyId)} ${laneLabel}: ${capacityLabel}, existing ${entry.existing}, planned ${entry.current}, projected ghost ${entry.projected}`;
  if (entry.remainingAfterProjection != null && entry.remainingAfterProjection < 0) {
    return `Slot pressure: ${prefix}; projection would exceed visible capacity by ${Math.abs(entry.remainingAfterProjection)}.`;
  }
  if (entry.remainingBeforeProjection != null && entry.remainingAfterProjection != null) {
    return `Slot pressure: ${prefix}; ${entry.remainingBeforeProjection} remaining before projection (${entry.remainingAfterProjection} after projection).`;
  }
  return `Slot pressure: ${prefix}; unknown capacity remains unknown until verified in the planner canvas.`;
}

function describeEconomyIntent(
  candidate: OptimiserCandidate,
  templatesById: Map<string, FacilityTemplate>,
) {
  const counts = new Map<ReturnType<typeof normalisePlanningEconomy>, number>();
  let contextualPorts = 0;
  let unknown = 0;
  candidate.placements.forEach((placement) => {
    const template = templatesById.get(placement.facility_template_id);
    const economy = normalisePlanningEconomy(template?.economy);
    if (economy) {
      counts.set(economy, (counts.get(economy) ?? 0) + 1);
    } else if (template?.is_port) {
      contextualPorts += 1;
    } else {
      unknown += 1;
    }
  });

  const economies = Array.from(counts.entries())
    .filter((entry): entry is [NonNullable<ReturnType<typeof normalisePlanningEconomy>>, number] => Boolean(entry[0]))
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3)
    .map(([economy, count]) => `${compactEconomyLabel(economy)} x${count}`);
  const economyLine = economies.length > 0
    ? `Economy intent: ${economies.join(', ')}.`
    : 'Economy intent: contextual or unknown from available templates.';
  const portLine = contextualPorts > 0
    ? ` ${contextualPorts} port placement(s) need Preview to validate inherited economy.`
    : '';
  const unknownLine = unknown > 0
    ? ` ${unknown} placement(s) have unknown economy metadata.`
    : '';
  return `${economyLine}${portLine}${unknownLine}`;
}

function describeUncertainty({
  candidate,
  system,
  bodies,
  projectedLaneSummary,
  slotPredictions,
}: {
  candidate: OptimiserCandidate;
  system?: SystemDetail | null;
  bodies: SystemBody[];
  projectedLaneSummary: PlacementLaneSummary;
  slotPredictions: SlotPredictionResponse | null;
}) {
  const signals = new Set<string>();
  const warningText = [...candidate.warnings, ...candidate.assumptions].join(' ').toLowerCase();
  if (/\b(sparse|estimated|inferred|no body|no matching|limited|unavailable|preview failed)\b/.test(warningText)) {
    signals.add('candidate warnings or assumptions use sparse, estimated, or incomplete data');
  }
  if (!system) signals.add('system context is unavailable');
  if (system && bodies.length === 0) signals.add('body catalogue is unavailable');
  if (slotPredictions?.prediction_status === 'unknown' || slotPredictions?.data_source === 'none') {
    signals.add('slot prediction status is unknown');
  }
  if (projectedLaneSummary.unassignedBody > 0) signals.add('some projected placements have no body assignment');
  if (projectedLaneSummary.unknownBody > 0) signals.add('some projected bodies are not in current body data');
  if (projectedLaneSummary.unknownLane > 0) signals.add('some projected lanes cannot be inferred from template/body data');
  if (projectedLaneSummary.missingTemplate > 0) signals.add('some facility templates are missing from the loaded catalogue');
  if (!candidate.preview_summary) {
    signals.add('lightweight preview summary is unavailable');
  } else if (candidate.preview_summary.confidence != null && candidate.preview_summary.confidence < 0.5) {
    signals.add('lightweight preview confidence is low');
  }

  if (signals.size === 0) {
    return 'Uncertainty: no additional data-quality signal beyond the candidate warnings; verify before loading or previewing.';
  }
  return `Uncertainty: ${Array.from(signals).join('; ')}.`;
}

function describeProjectionEffect(
  candidate: OptimiserCandidate,
  usedBodyIds: string[],
  projectedLaneSummary: PlacementLaneSummary,
) {
  const unresolvedProjection = projectedLaneSummary.unassignedBody
    + projectedLaneSummary.unknownBody
    + projectedLaneSummary.unknownLane
    + projectedLaneSummary.missingTemplate;
  const bodyCount = usedBodyIds.length || 1;
  const unresolvedLine = unresolvedProjection > 0
    ? ` ${unresolvedProjection} placement(s) need manual body/lane/template review before capacity is reliable.`
    : '';
  return `Projection effect: selecting this candidate shows ${candidate.placements.length} ghost structure(s) across ${bodyCount} body/bodies on the planner canvas; ghosts do not count as planned placements.${unresolvedLine}`;
}

function cardLineFor({
  slotPressure,
  existingInfrastructure,
  uncertainty,
  roleGuidance,
}: {
  slotPressure: string;
  existingInfrastructure: string;
  uncertainty: string;
  roleGuidance: RoleGuidanceSummary;
}) {
  if (/exceed visible capacity/i.test(slotPressure)) return 'Slot pressure needs review before loading.';
  if (/unresolved|unknown-lane/i.test(existingInfrastructure)) return 'Existing or unresolved infrastructure is called out in details.';
  if (roleGuidance.hasReviewIssue) return 'Role gaps or conflicts need manual review before loading.';
  if (/sparse|estimated|unknown|unavailable|missing/i.test(uncertainty)) return 'Sparse or estimated data is called out in details.';
  return 'Projection stays ghost-only until explicit load.';
}

function candidateBodyIds(candidate: OptimiserCandidate): string[] {
  return Array.from(new Set(
    candidate.placements
      .map((placement) => bodyIdKey(placement.local_body_id))
      .filter((bodyId) => Boolean(bodyId)),
  ));
}

function candidateRoleSignalsByBody(
  candidate: OptimiserCandidate,
  templatesById: Map<string, FacilityTemplate>,
) {
  const signals = new Map<string, Set<DeclaredColonyRoleId>>();
  candidate.placements.forEach((placement) => {
    const bodyId = bodyIdKey(placement.local_body_id);
    if (!bodyId) return;
    const template = templatesById.get(placement.facility_template_id);
    const text = [
      placement.facility_template_id,
      template?.id,
      template?.name,
      template?.category,
      template?.economy,
      candidate.target_archetype,
      candidate.strategy,
      ...candidate.tags,
      ...candidate.rationale,
    ].filter(Boolean).join(' ').toLowerCase();
    const roleIds = roleSignalsForPlacement({ placement, template, text });
    if (roleIds.size === 0) return;
    const bodySignals = signals.get(bodyId) ?? new Set<DeclaredColonyRoleId>();
    roleIds.forEach((roleId) => bodySignals.add(roleId));
    signals.set(bodyId, bodySignals);
  });
  return signals;
}

function roleSignalsForPlacement({
  placement,
  template,
  text,
}: {
  placement: PlacementLike;
  template?: FacilityTemplate;
  text: string;
}) {
  const roles = new Set<DeclaredColonyRoleId>();
  if (placement.is_primary_port || template?.is_port || /\b(port|starport|station|outpost)\b/.test(text)) {
    roles.add('main_station_body');
    roles.add('primary_port_body');
    roles.add('colony_anchor');
  }
  if (/\b(refinery|refining)\b/.test(text)) {
    roles.add('refinery_core');
    roles.add('industrial_core');
  }
  if (/\b(industrial|industry|manufactur|factory|fabricat|production)\b/.test(text)) {
    roles.add('industrial_core');
  }
  if (/\b(extraction|extractive|mining|miner|resource)\b/.test(text)) {
    roles.add('extraction_support');
  }
  if (/\b(tourism|tourist|agri|agriculture|terraform|food|civilian)\b/.test(text)) {
    roles.add('tourism_agriculture_body');
  }
  if (/\b(security|military|defence|defense|navy|barracks|command)\b/.test(text)) {
    roles.add('security_military_body');
  }
  if (template?.is_support_facility || /\b(support|logistics|supply)\b/.test(text)) {
    roles.add('support_body');
  }
  return roles;
}

function roleSatisfiedByCandidate(
  roleId: DeclaredColonyRoleId,
  signals: Set<DeclaredColonyRoleId> | undefined,
) {
  if (!signals) return false;
  switch (roleId) {
    case 'colony_anchor':
      return signals.has('colony_anchor') || signals.has('main_station_body') || signals.has('primary_port_body');
    case 'main_station_body':
      return signals.has('main_station_body') || signals.has('primary_port_body');
    case 'primary_port_body':
      return signals.has('primary_port_body') || signals.has('main_station_body');
    case 'industrial_core':
      return hasIndustrialSignal(signals);
    case 'refinery_core':
      return signals.has('refinery_core');
    case 'extraction_support':
      return signals.has('extraction_support');
    case 'tourism_agriculture_body':
      return signals.has('tourism_agriculture_body');
    case 'security_military_body':
      return signals.has('security_military_body');
    case 'support_body':
      return signals.has('support_body');
    case 'expansion_reserve':
      return false;
    default:
      return false;
  }
}

function hasIndustrialSignal(signals: Set<DeclaredColonyRoleId> | undefined) {
  return Boolean(signals?.has('industrial_core') || signals?.has('refinery_core') || signals?.has('extraction_support'));
}

function isIndustrialRole(roleId: DeclaredColonyRoleId) {
  return roleId === 'industrial_core' || roleId === 'refinery_core' || roleId === 'extraction_support';
}

function summarizePlacementLanes(
  placements: PlacementLike[],
  templatesById: Map<string, FacilityTemplate>,
  bodiesById: Map<string, SystemBody>,
): PlacementLaneSummary {
  const byBodyId = new Map<string, LaneCounts>();
  const summary: PlacementLaneSummary = {
    byBodyId,
    unassignedBody: 0,
    unknownBody: 0,
    unknownLane: 0,
    missingTemplate: 0,
  };

  placements.forEach((placement) => {
    const bodyId = bodyIdKey(placement.local_body_id);
    if (!bodyId) {
      summary.unassignedBody += 1;
      return;
    }
    const body = bodiesById.get(bodyId);
    if (!body) {
      summary.unknownBody += 1;
      return;
    }
    const template = templatesById.get(placement.facility_template_id);
    if (!template) {
      summary.missingTemplate += 1;
      return;
    }
    const lane = placementLaneForTemplate(template, body);
    if (lane !== 'orbital' && lane !== 'surface') {
      summary.unknownLane += 1;
      return;
    }
    const counts = byBodyId.get(bodyId) ?? { orbital: 0, surface: 0 };
    counts[lane] += 1;
    byBodyId.set(bodyId, counts);
  });

  return summary;
}
