import type { FacilityTemplate, SystemBody } from '@ed-finder/api-client/types';
import type { BodyGroup, GroupedPlacement } from './buildPlanLayout';

export type ColonyRoleHintSource = 'inferred' | 'observed' | 'future-editable';
export type ColonyRoleHintTone = 'default' | 'good' | 'warn';
export type ColonyRoleConfidence = 'tentative' | 'likely' | 'strong';

export interface ColonyRoleHint {
  id: string;
  label: string;
  compactLabel: string;
  source: ColonyRoleHintSource;
  tone: ColonyRoleHintTone;
  detail: string;
}

export interface ColonyRoleSummary {
  hints: ColonyRoleHint[];
  confidence: ColonyRoleConfidence;
  confidenceReason: string;
  reasoning: string;
  conflicts: string[];
  warnings: string[];
  primaryPortContext: string;
}

export function buildColonyRoleHintsForGroup(group: BodyGroup, allGroups: BodyGroup[] = [group]): ColonyRoleHint[] {
  const hints = new Map<string, ColonyRoleHint>();
  const add = (hint: ColonyRoleHint) => {
    if (!hints.has(hint.id)) hints.set(hint.id, hint);
  };

  if (!group.body) {
    add({
      id: group.placements.some((item) => item.hasUnknownBody) ? 'unknown-body-context' : 'unassigned-body-context',
      label: 'Role hint pending',
      compactLabel: group.placements.some((item) => item.hasUnknownBody) ? 'Unknown Role' : 'Role Pending',
      source: 'inferred',
      tone: 'warn',
      detail: group.placements.some((item) => item.hasUnknownBody)
        ? 'Unknown body reference: confirm the body before treating this as colony-role intent.'
        : 'No body assignment yet: role hints stay conservative until placement topology is known.',
    });
    return Array.from(hints.values());
  }

  if (isSparseBody(group.body)) {
    add({
      id: 'sparse-body-context',
      label: 'Role hint limited',
      compactLabel: 'Sparse Metadata',
      source: 'inferred',
      tone: 'warn',
      detail: 'Sparse body metadata: keep this as advisory context until in-game body details are checked.',
    });
  }

  const hasMainStationCandidate = groupHasMainStationCandidate(group);
  const hasSupport = group.placements.some(isSupportPlacement);
  const hasOtherMainCandidate = allGroups.some((item) => item.key !== group.key && groupHasMainStationCandidate(item));
  const hasIndustrial = group.placements.some((item) => matchesTemplateText(item.template, ['industrial', 'refinery', 'manufacturing']));
  const hasRefinery = group.placements.some((item) => matchesTemplateText(item.template, ['refinery']));
  const hasExtraction = group.placements.some((item) => matchesTemplateText(item.template, ['extraction', 'mining']));
  const hasSecurity = group.placements.some((item) => matchesTemplateText(item.template, ['security', 'military', 'defence', 'defense']));
  const hasTourismAgriculture = group.placements.some((item) => matchesTemplateText(item.template, ['tourism', 'agriculture', 'terraforming']))
    || hasTourismAgricultureBodyContext(group.body);

  if (hasMainStationCandidate) {
    add({
      id: 'main-station-body',
      label: 'Main Station Body candidate',
      compactLabel: 'Main Station Candidate',
      source: 'inferred',
      tone: 'good',
      detail: 'Current Build Plan places a primary, port, or major-tier facility on this body.',
    });
  }

  if (hasMainStationCandidate && hasSupport) {
    add({
      id: 'colony-core-body',
      label: 'Colony Core candidate',
      compactLabel: 'Colony Anchor Candidate',
      source: 'inferred',
      tone: 'default',
      detail: 'Station and support placements share this body, so it may be the project core.',
    });
  }

  if (hasIndustrial) {
    add({
      id: 'industrial-core',
      label: 'Industrial Core candidate',
      compactLabel: 'Industrial Candidate',
      source: 'inferred',
      tone: 'default',
      detail: 'Facility economy or category suggests industrial planning intent.',
    });
  }

  if (hasRefinery) {
    add({
      id: 'refinery-body',
      label: 'Refinery candidate',
      compactLabel: 'Refinery Candidate',
      source: 'inferred',
      tone: 'default',
      detail: 'Facility economy or category suggests refinery-oriented planning intent.',
    });
  }

  if (hasExtraction) {
    add({
      id: 'extraction-body',
      label: 'Extraction Body candidate',
      compactLabel: 'Extraction Candidate',
      source: 'inferred',
      tone: 'default',
      detail: 'Facility economy or category suggests extraction support.',
    });
  }

  if (hasTourismAgriculture) {
    add({
      id: 'tourism-agriculture-body',
      label: 'Tourism/Agriculture candidate',
      compactLabel: hasTourismAgricultureBodyContext(group.body) ? 'Tourism Pressure' : 'Agri/Tourism Candidate',
      source: 'inferred',
      tone: 'default',
      detail: 'Body context or facility economy suggests tourism, agriculture, or terraforming review.',
    });
  }

  if (hasSecurity) {
    add({
      id: 'security-support',
      label: 'Security Support candidate',
      compactLabel: 'Security Support',
      source: 'inferred',
      tone: 'default',
      detail: 'Facility economy or category suggests security support intent.',
    });
  }

  if (hasSupport && !hasMainStationCandidate) {
    add({
      id: 'support-body',
      label: hasOtherMainCandidate ? 'Support Body candidate' : 'Support-focused candidate',
      compactLabel: hasOtherMainCandidate ? 'Support Body' : 'Support Candidate',
      source: 'inferred',
      tone: 'default',
      detail: hasOtherMainCandidate
        ? 'Current placements keep this body support-focused away from the main station candidate.'
        : 'Current placements are support-focused; no main station candidate is assigned here.',
    });
  }

  if (hints.size === 0) {
    add({
      id: 'no-clear-role',
      label: 'No clear role hint',
      compactLabel: 'Unknown Role',
      source: 'inferred',
      tone: 'warn',
      detail: 'Current topology does not provide enough placement context for a specific colony-role hint.',
    });
  }

  return Array.from(hints.values());
}

export function buildColonyRoleSummaryForGroup(group: BodyGroup, allGroups: BodyGroup[] = [group]): ColonyRoleSummary {
  const hints = buildColonyRoleHintsForGroup(group, allGroups);
  const warnings = buildRoleWarnings(group, hints);
  const conflicts = buildRoleConflicts(hints);
  const confidence = deriveRoleConfidence(group, hints, allGroups, warnings, conflicts);

  return {
    hints,
    confidence,
    confidenceReason: confidenceReason(confidence, warnings),
    reasoning: roleReasoning(group, hints),
    conflicts,
    warnings,
    primaryPortContext: primaryPortContext(group),
  };
}

export function roleConfidenceLabel(confidence: ColonyRoleConfidence): string {
  if (confidence === 'strong') return 'strong';
  if (confidence === 'likely') return 'likely';
  return 'tentative';
}

export function primaryRoleHint(hints: ColonyRoleHint[]): ColonyRoleHint | null {
  return hints.find((hint) => hint.tone !== 'warn') ?? hints[0] ?? null;
}

function deriveRoleConfidence(
  group: BodyGroup,
  hints: ColonyRoleHint[],
  allGroups: BodyGroup[],
  warnings: string[],
  conflicts: string[],
): ColonyRoleConfidence {
  let score = 0;
  if (group.placements.length >= 3) score += 2;
  else if (group.placements.length > 0) score += 1;
  if (group.placements.some((item) => item.placement.is_primary_port)) score += 2;
  if (group.placements.some((item) => item.template?.is_port)) score += 1;
  if (hints.some((hint) => hint.id === 'colony-core-body')) score += 1;
  if (hints.some((hint) => hint.id === 'industrial-core' || hint.id === 'refinery-body' || hint.id === 'extraction-body')) score += 1;
  if (allGroups.filter((item) => item.body && item.placements.length > 0).length > 1) score += 1;
  if (!group.body || isSparseBody(group.body)) score -= 2;
  if (warnings.length > 0) score -= 1;
  if (conflicts.length > 0) score -= 1;
  if (hints.every((hint) => hint.tone === 'warn')) score = Math.min(score, 1);

  if (score >= 4) return 'strong';
  if (score >= 2) return 'likely';
  return 'tentative';
}

function buildRoleWarnings(group: BodyGroup, hints: ColonyRoleHint[]): string[] {
  const warnings: string[] = [];
  if (!group.body) {
    warnings.push(group.placements.some((item) => item.hasUnknownBody)
      ? 'Unknown body reference keeps this advisory.'
      : 'No body assignment yet.');
  } else if (isSparseBody(group.body)) {
    warnings.push('Sparse metadata lowers confidence.');
  }
  if (hints.some((hint) => hint.id === 'no-clear-role')) {
    warnings.push('Current topology does not show a clear role.');
  }
  return warnings;
}

function buildRoleConflicts(hints: ColonyRoleHint[]): string[] {
  const ids = new Set(hints.map((hint) => hint.id));
  const conflicts: string[] = [];
  if ((ids.has('industrial-core') || ids.has('refinery-body') || ids.has('extraction-body')) && ids.has('tourism-agriculture-body')) {
    conflicts.push('Industrial + tourism pressure overlap.');
  }
  if (ids.has('main-station-body') && ids.has('support-body')) {
    conflicts.push('Main-station and support-body signals conflict.');
  }
  return conflicts;
}

function confidenceReason(confidence: ColonyRoleConfidence, warnings: string[]): string {
  if (warnings.some((warning) => warning.includes('Sparse metadata'))) {
    return `${roleConfidenceLabel(confidence)}; sparse metadata limits certainty`;
  }
  if (confidence === 'strong') return 'strong; concentrated placements and port signals align';
  if (confidence === 'likely') return 'likely; placement mix gives usable strategic context';
  return 'tentative; current topology has limited role evidence';
}

function roleReasoning(group: BodyGroup, hints: ColonyRoleHint[]): string {
  const primary = primaryRoleHint(hints);
  if (!primary) return 'No advisory role signal available yet.';
  if (primary.id === 'main-station-body') {
    return 'Possible main-station body due to current orbital/port concentration.';
  }
  if (primary.id === 'colony-core-body') {
    return 'Possible colony anchor because station and support placements share this body.';
  }
  if (primary.id === 'tourism-agriculture-body') {
    return 'Tourism/agriculture pressure likely from body context or facility mix.';
  }
  if (primary.id === 'support-body') {
    return 'Support-body role inferred from placement spread.';
  }
  if (primary.id === 'industrial-core' || primary.id === 'refinery-body' || primary.id === 'extraction-body') {
    return 'Industrial or refinery intent inferred from current facility mix.';
  }
  if (!group.body) return 'Role hint waits for a known body assignment.';
  return primary.detail;
}

function primaryPortContext(group: BodyGroup): string {
  const hasPrimary = group.placements.some((item) => item.placement.is_primary_port);
  const hasPort = group.placements.some((item) => item.template?.is_port);
  if (hasPrimary) {
    return 'Primary-port placement is planned here; Architect observation is still advisory context.';
  }
  if (hasPort) {
    return 'Port context present, but Architect primary-port observation is not recorded.';
  }
  return 'Primary-port context not recorded for this body.';
}

function groupHasMainStationCandidate(group: BodyGroup): boolean {
  return group.placements.some((item) => (
    item.placement.is_primary_port
    || Boolean(item.template?.is_port)
    || (item.template?.tier ?? 0) >= 3
  ));
}

function isSupportPlacement(item: GroupedPlacement): boolean {
  const template = item.template;
  if (!template) return false;
  return Boolean(template.is_support_facility || !template.is_port);
}

function hasTourismAgricultureBodyContext(body: SystemBody): boolean {
  return Boolean(body.is_water_world || body.is_earth_like || body.is_terraformable);
}

function matchesTemplateText(template: FacilityTemplate | undefined, needles: string[]): boolean {
  if (!template) return false;
  const value = [
    template.id,
    template.name,
    template.category,
    template.economy ?? '',
    template.notes ?? '',
  ].join(' ').toLowerCase();
  return needles.some((needle) => value.includes(needle));
}

function isSparseBody(body: SystemBody): boolean {
  return !body.body_type && !body.subtype;
}
