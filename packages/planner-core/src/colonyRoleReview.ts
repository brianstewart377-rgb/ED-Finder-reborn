import type { ObservedFact } from '@ed-finder/api-client/types';
import {
  declaredRoleConflicts,
  roleCompactLabel,
  roleLabel,
  type DeclaredColonyRole,
  type DeclaredColonyRoleId,
  type RoleConfidence,
} from './colonyRoles';

export type StrategicConsistency = 'aligned' | 'partial' | 'diverging' | 'insufficient';

export interface ObservedColonyRole {
  id: string;
  body_id: string;
  role_id: DeclaredColonyRoleId;
  source: 'observed';
  confidence: RoleConfidence;
  label: string;
  evidenceLabel: string;
}

export interface RoleReviewResult {
  consistency: StrategicConsistency;
  consistencyLabel: string;
  declaredRoles: DeclaredColonyRole[];
  observedRoles: ObservedColonyRole[];
  summaries: string[];
  conflicts: string[];
  coverage: {
    declaredCount: number;
    observedCount: number;
    matchedCount: number;
    mismatchCount: number;
  };
}

export function buildObservedRolesFromFacts(facts: ObservedFact[] = []): ObservedColonyRole[] {
  const roles = new Map<string, ObservedColonyRole>();
  for (const fact of facts) {
    const bodyId = fact.local_body_id ?? (fact.subject_type === 'body' ? fact.subject_id : null);
    if (!bodyId || !isObservedPresent(fact.status)) continue;
    const role = observedRoleIdForFact(fact);
    if (!role) continue;
    const key = `${bodyId}:${role}`;
    if (roles.has(key)) continue;
    roles.set(key, {
      id: `observed:${bodyId}:${role}:${fact.observation_id}`,
      body_id: bodyId,
      role_id: role,
      source: 'observed',
      confidence: observedConfidence(fact.confidence),
      label: observedRoleLabel(role),
      evidenceLabel: evidenceLabelForFact(fact),
    });
  }
  return Array.from(roles.values());
}

export function buildRoleReview({
  declaredRoles,
  observedRoles,
}: {
  declaredRoles: DeclaredColonyRole[];
  observedRoles: ObservedColonyRole[];
}): RoleReviewResult {
  const conflicts = declaredRoleConflicts(declaredRoles);
  const summaries: string[] = [];
  let matchedCount = 0;
  let mismatchCount = 0;

  if (observedRoles.length === 0) {
    summaries.push('No observed evidence recorded yet.');
  }

  for (const declared of declaredRoles) {
    const sameBodyObserved = observedRoles.filter((role) => role.body_id === declared.body_id);
    const exact = sameBodyObserved.find((role) => role.role_id === declared.role_id);
    if (exact) {
      matchedCount += 1;
      summaries.push(`Declared ${roleLabel(declared.role_id)} matches observed ${exact.label}.`);
      continue;
    }
    if (sameBodyObserved.length > 0) {
      mismatchCount += 1;
      summaries.push(`Declared ${roleLabel(declared.role_id)} but observed ${sameBodyObserved[0].label}.`);
    }
  }

  for (const observed of observedRoles) {
    const declaredHere = declaredRoles.some((role) => role.body_id === observed.body_id && role.role_id === observed.role_id);
    if (!declaredHere && declaredRoles.length > 0) {
      summaries.push(`Observed ${observed.label} is not declared strategy.`);
    }
  }

  if (declaredRoles.some((role) => role.role_id === 'main_station_body') && observedRoles.some((role) => role.role_id === 'primary_port_body')) {
    const declaredMain = declaredRoles.find((role) => role.role_id === 'main_station_body');
    const observedPrimary = observedRoles.find((role) => role.role_id === 'primary_port_body');
    if (declaredMain && observedPrimary && declaredMain.body_id !== observedPrimary.body_id) {
      summaries.push('Observed primary-port context differs from declared Main Station Body.');
    }
  }

  const consistency = strategicConsistency({
    declaredCount: declaredRoles.length,
    observedCount: observedRoles.length,
    matchedCount,
    mismatchCount,
  });

  if (summaries.length === 0 && declaredRoles.length === 0) {
    summaries.push('Declare strategy before comparing colony identity.');
  }

  return {
    consistency,
    consistencyLabel: consistencyLabel(consistency),
    declaredRoles,
    observedRoles,
    summaries: summaries.slice(0, 5),
    conflicts,
    coverage: {
      declaredCount: declaredRoles.length,
      observedCount: observedRoles.length,
      matchedCount,
      mismatchCount,
    },
  };
}

export function roleReviewBodySummary(result: RoleReviewResult, bodyId: string | null): string {
  if (!bodyId) return result.consistencyLabel;
  const declared = result.declaredRoles.filter((role) => role.body_id === bodyId);
  const observed = result.observedRoles.filter((role) => role.body_id === bodyId);
  if (declared.length === 0 && observed.length === 0) return 'No body role review yet';
  if (declared.length > 0 && observed.length === 0) return `${declared.map((role) => `Declared ${roleCompactLabel(role.role_id)}`).join(', ')}; no observed role evidence yet`;
  if (declared.length === 0 && observed.length > 0) return `${observed.map((role) => `Observed ${role.label}`).join(', ')}; not declared`;
  const matched = declared.filter((role) => observed.some((item) => item.role_id === role.role_id)).length;
  return matched > 0 ? 'Declared strategy has observed support' : 'Declared strategy differs from observed role evidence';
}

function observedRoleIdForFact(fact: ObservedFact): DeclaredColonyRoleId | null {
  const value = [
    fact.economy,
    fact.service_id,
    fact.facility_template_id,
    typeof fact.observed_value === 'string' ? fact.observed_value : '',
    typeof fact.expected_value === 'string' ? fact.expected_value : '',
    fact.notes ?? '',
    fact.tags.join(' '),
  ].join(' ').toLowerCase();

  if (value.includes('primary port') || value.includes('primary-port') || value.includes('architect')) return 'primary_port_body';
  if (value.includes('main station') || value.includes('starport') || value.includes('orbital port')) return 'main_station_body';
  if (value.includes('tourism') || value.includes('tourist') || value.includes('agriculture') || value.includes('terraform')) return 'tourism_agriculture_body';
  if (value.includes('security') || value.includes('military') || value.includes('defence') || value.includes('defense')) return 'security_military_body';
  if (value.includes('refinery')) return 'refinery_core';
  if (value.includes('industrial') || value.includes('manufacturing')) return 'industrial_core';
  if (value.includes('extraction') || value.includes('mining')) return 'extraction_support';
  if (value.includes('population') || value.includes('center') || value.includes('centre')) return 'colony_anchor';
  if (value.includes('support')) return 'support_body';
  return null;
}

function observedRoleLabel(roleId: DeclaredColonyRoleId): string {
  if (roleId === 'main_station_body') return 'Observed Main Station Body';
  if (roleId === 'industrial_core') return 'Observed Industrial Core';
  if (roleId === 'tourism_agriculture_body') return 'Observed Tourism Focus';
  if (roleId === 'security_military_body') return 'Observed Security Support';
  if (roleId === 'colony_anchor') return 'Observed Population Center';
  if (roleId === 'support_body') return 'Observed Support Body';
  if (roleId === 'primary_port_body') return 'Observed Primary Port';
  return `Observed ${roleLabel(roleId)}`;
}

function evidenceLabelForFact(fact: ObservedFact): string {
  if (fact.fact_type === 'economy_presence' && fact.economy) return `Observed ${fact.economy} economy`;
  if (fact.fact_type === 'service_presence' && fact.service_id) return `Observed ${fact.service_id} service`;
  if (fact.fact_type === 'facility_state' && fact.facility_template_id) return `Observed ${fact.facility_template_id}`;
  return fact.notes?.trim() || 'Observed role evidence';
}

function observedConfidence(confidence: ObservedFact['confidence']): RoleConfidence {
  if (confidence === 'high') return 'strong';
  if (confidence === 'medium') return 'likely';
  return 'tentative';
}

function isObservedPresent(status: ObservedFact['status']): boolean {
  return status === 'observed_present' || status === 'confirmed';
}

function strategicConsistency({
  declaredCount,
  observedCount,
  matchedCount,
  mismatchCount,
}: {
  declaredCount: number;
  observedCount: number;
  matchedCount: number;
  mismatchCount: number;
}): StrategicConsistency {
  if (observedCount === 0) return 'insufficient';
  if (declaredCount === 0) return 'partial';
  if (matchedCount > 0 && mismatchCount === 0) return matchedCount === declaredCount ? 'aligned' : 'partial';
  if (mismatchCount > 0) return matchedCount > 0 ? 'partial' : 'diverging';
  return 'partial';
}

function consistencyLabel(consistency: StrategicConsistency): string {
  if (consistency === 'aligned') return 'Strategy aligned';
  if (consistency === 'partial') return 'Partially aligned';
  if (consistency === 'diverging') return 'Strategy diverging';
  return 'Insufficient observed evidence';
}
