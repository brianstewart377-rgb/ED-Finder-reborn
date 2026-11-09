import type { OptimiserCandidate } from '@ed-finder/api-client/types';
import { strategyLabel } from './optimiserUtils';

export interface SuggestedBuildPresentation {
  category: string;
  scale: SuggestedBuildScale;
  scaleLabel: string;
  scaleReason: string;
  placementCount: number;
  bodyCount: number;
  mainBodyId: string | null;
  supportBodyIds: string[];
  purpose: string;
  reason: string;
  tradeoff: string;
  nextAction: string;
  tags: string[];
}

export type SuggestedBuildScale = 'bootstrap' | 'starter' | 'expansion' | 'full';

const TAG_LABELS: Record<string, string> = {
  balanced: 'Balanced spread',
  body_diversity: 'Uses multiple bodies',
  primary_port: 'Includes a port anchor',
  main_station: 'Main station candidate',
  industrial: 'Industrial pressure',
  refinery: 'Refinery pressure',
  extraction: 'Extraction support',
  tourism: 'Tourism pressure',
  agriculture: 'Agriculture pressure',
  military: 'Security pressure',
  security: 'Security pressure',
  support_body: 'Support body coverage',
};

export function filterUsefulSuggestedBuilds(candidates: OptimiserCandidate[]): OptimiserCandidate[] {
  const seen = new Set<string>();
  const useful: OptimiserCandidate[] = [];

  for (const candidate of candidates) {
    if (isTrivialSuggestedBuild(candidate)) continue;
    const signature = candidateSignature(candidate);
    if (seen.has(signature)) continue;
    seen.add(signature);
    useful.push(candidate);
  }

  return useful;
}

export function isTrivialSuggestedBuild(candidate: OptimiserCandidate): boolean {
  if (isColonyShipOnly(candidate)) return true;
  if (isColonyShipAndGenericStation(candidate)) return true;
  if (candidate.placements.length >= 5) return false;
  if (candidate.placements.length <= 1 && candidate.placements.every((placement) => isLowPurposePortTemplate(placement.facility_template_id))) return true;
  if (candidate.placements.length <= 1 && !hasClearSuggestedBuildPurpose(candidate)) return true;
  if (candidate.placements.length <= 2 && !hasClearSuggestedBuildPurpose(candidate)) return true;
  return false;
}

export function suggestedBuildPresentation(candidate: OptimiserCandidate): SuggestedBuildPresentation {
  const text = candidateText(candidate);
  const scale = suggestedBuildScale(candidate);
  const category = suggestedBuildCategory(candidate, scale);
  const usedBodyIds = candidateBodyIds(candidate);
  const mainBodyId = candidate.placements.find((placement) => placement.is_primary_port)?.local_body_id
    ?? usedBodyIds[0]
    ?? null;
  const supportBodyIds = usedBodyIds.filter((bodyId) => bodyId !== mainBodyId);
  const reason = candidate.rationale[0]
    ?? `This ${strategyLabel(candidate.strategy).toLowerCase()} plan matches the current target better than a blank plan.`;
  const warningCount = candidate.warnings.length + (candidate.preview_summary?.warnings_count ?? 0);
  const tradeoff = warningCount > 0
    ? `Review ${warningCount} warning${warningCount === 1 ? '' : 's'} before relying on this plan.`
    : scale === 'full' || scale === 'expansion'
      ? 'Broader coverage means more placements to validate before committing materials.'
      : 'Focused starter plan; it may need support bodies after more system data is available.';

  return {
    category,
    scale,
    scaleLabel: scaleToLabel(scale),
    scaleReason: scaleReason(scale, candidate, usedBodyIds.length),
    placementCount: candidate.placements.length,
    bodyCount: usedBodyIds.length,
    mainBodyId,
    supportBodyIds,
    purpose: purposeForCategory(category, scale),
    reason,
    tradeoff,
    nextAction: 'Review in Workspace, then load deliberately if it fits the current Build Plan.',
    tags: translateSuggestedBuildTags(candidate.tags, text),
  };
}

export function translateSuggestedBuildTags(tags: string[], text = ''): string[] {
  const translated = tags
    .map((tag) => TAG_LABELS[tag] ?? humanizeTag(tag))
    .filter((tag) => tag.length > 0);
  const unique = Array.from(new Set(translated));
  if (unique.length > 0) return unique;
  if (text.includes('agriculture')) return ['Agriculture pressure'];
  if (text.includes('tourism')) return ['Tourism pressure'];
  if (text.includes('industrial') || text.includes('refinery')) return ['Industrial pressure'];
  if (text.includes('military') || text.includes('security')) return ['Security pressure'];
  return ['Workspace candidate'];
}

function isColonyShipOnly(candidate: OptimiserCandidate) {
  const text = candidateText(candidate);
  return (candidate.placements.length > 0
    && candidate.placements.every((placement) => isColonyShipTemplate(placement.facility_template_id))
  ) || /\bcolony ship only\b/.test(text);
}

function isColonyShipAndGenericStation(candidate: OptimiserCandidate) {
  if (candidate.placements.length !== 2) return false;
  const ids = candidate.placements.map((placement) => placement.facility_template_id.toLowerCase());
  const hasShip = ids.some(isColonyShipTemplate);
  const hasLowPurposePort = ids.some(isLowPurposePortTemplate);
  return hasShip && hasLowPurposePort && !hasClearSuggestedBuildPurpose(candidate);
}

function hasClearSuggestedBuildPurpose(candidate: OptimiserCandidate) {
  const text = candidateText(candidate);
  const meaningfulRationale = candidate.rationale.some((item) => {
    const value = item.trim().toLowerCase();
    return value.length > 0
      && !['starter plan', 'baseline plan', 'generic plan', 'candidate plan'].includes(value);
  });
  if (meaningfulRationale) return true;
  return [
    'primary',
    'port',
    'main',
    'industrial',
    'refinery',
    'tourism',
    'agriculture',
    'military',
    'security',
    'balanced',
    'support',
    'body',
  ].some((term) => text.includes(term))
    && !/\b(colony ship only|generic station only|generic outpost only|baseline only)\b/.test(text);
}

function isColonyShipTemplate(templateId: string) {
  const id = templateId.toLowerCase();
  return id.includes('colony_ship') || id.includes('colony-ship');
}

function isLowPurposePortTemplate(templateId: string) {
  const id = templateId.toLowerCase();
  return id.includes('generic')
    || id.includes('basic_station')
    || id.includes('basic-station')
    || id.includes('generic_station')
    || id.includes('generic-station')
    || id.includes('generic_outpost')
    || id.includes('generic-outpost')
    || id.includes('placeholder_station')
    || id.includes('placeholder-station');
}

function suggestedBuildCategory(candidate: OptimiserCandidate, scale: SuggestedBuildScale) {
  const text = candidateText(candidate);
  const hasPrimaryPort = candidate.placements.some((placement) => placement.is_primary_port);
  const bodyCount = new Set(candidate.placements.map((placement) => placement.local_body_id ?? 'unassigned')).size;

  if (text.includes('primary') || (hasPrimaryPort && candidate.placements.length <= 3)) return `Primary-port ${scaleToLabel(scale).toLowerCase()}`;
  if (text.includes('main station') || (hasPrimaryPort && candidate.placements.length > 3)) return `Main station ${scaleToLabel(scale).toLowerCase()}`;
  if (text.includes('industrial') || text.includes('refinery') || text.includes('extraction')) return `Industrial/refinery ${scaleToLabel(scale).toLowerCase()}`;
  if (text.includes('tourism') || text.includes('agriculture')) return `Tourism/agriculture ${scaleToLabel(scale).toLowerCase()}`;
  if (text.includes('military') || text.includes('security')) return 'Military/security stabiliser';
  if (text.includes('support') || bodyCount > 1) return `Support-body ${scaleToLabel(scale).toLowerCase()}`;
  return `Balanced ${scaleToLabel(scale).toLowerCase()}`;
}

function purposeForCategory(category: string, scale: SuggestedBuildScale) {
  switch (true) {
    case category.startsWith('Primary-port'):
      return 'Establish a conservative port anchor before expanding the plan.';
    case category.startsWith('Main station'):
      return 'Evaluate whether this body can carry the main station role.';
    case category.startsWith('Industrial/refinery'):
      return 'Seed industrial, refinery, or extraction pressure with supporting placements.';
    case category.startsWith('Tourism/agriculture'):
      return 'Start a lighter civilian economy plan around tourism or agriculture pressure.';
    case category.startsWith('Military/security'):
      return 'Add security-oriented support without claiming a final colony role.';
    case category.startsWith('Support-body'):
      return 'Use additional bodies to support the main colony plan.';
    default:
      return scale === 'expansion' || scale === 'full'
        ? 'Build a multi-body strategic plan that can be tuned in the workspace.'
        : 'Build a balanced starter plan that can be adjusted in the workspace.';
  }
}

export function suggestedBuildScale(candidate: OptimiserCandidate): SuggestedBuildScale {
  const fromTag = candidate.tags.find((tag) => tag.startsWith('scale_'))?.replace('scale_', '');
  if (fromTag === 'bootstrap' || fromTag === 'starter' || fromTag === 'expansion' || fromTag === 'full') {
    return fromTag;
  }
  const placementCount = candidate.placements.length;
  if (placementCount <= 4) return 'bootstrap';
  if (placementCount <= 8) return 'starter';
  if (placementCount <= 14) return 'expansion';
  return 'full';
}

function scaleToLabel(scale: SuggestedBuildScale): string {
  if (scale === 'bootstrap') return 'Bootstrap';
  if (scale === 'starter') return 'Starter';
  if (scale === 'expansion') return 'Expansion';
  return 'Full';
}

function scaleReason(scale: SuggestedBuildScale, candidate: OptimiserCandidate, bodyCount: number): string {
  if (scale === 'bootstrap') {
    return candidate.strategy === 'primary_port_bootstrap'
      ? 'Deliberately minimal bootstrap plan for initial setup.'
      : 'Scale was limited by sparse catalogue/body data; expand data to generate larger plans.';
  }
  if (scale === 'starter') {
    return `Starter footprint with ${candidate.placements.length} placements across ${Math.max(1, bodyCount)} body/bodies.`;
  }
  if (scale === 'expansion') {
    return `Expansion footprint with ${candidate.placements.length} placements across ${Math.max(1, bodyCount)} body/bodies.`;
  }
  return `Ambitious full plan with ${candidate.placements.length} placements across ${Math.max(1, bodyCount)} body/bodies.`;
}

function candidateBodyIds(candidate: OptimiserCandidate): string[] {
  return Array.from(new Set(
    candidate.placements
      .map((placement) => placement.local_body_id ?? '')
      .filter((bodyId) => Boolean(bodyId)),
  ));
}

function candidateText(candidate: OptimiserCandidate) {
  return [
    candidate.label,
    candidate.target_archetype,
    candidate.strategy,
    ...candidate.tags,
    ...candidate.rationale,
    ...candidate.warnings,
    ...candidate.assumptions,
    ...candidate.placements.map((placement) => placement.facility_template_id),
  ].join(' ').toLowerCase();
}

function candidateSignature(candidate: OptimiserCandidate) {
  return candidate.placements
    .map((placement) => [
      placement.facility_template_id,
      placement.local_body_id ?? '',
      placement.is_primary_port ? 'primary' : '',
    ].join('@'))
    .sort()
    .join('|');
}

function humanizeTag(tag: string) {
  return tag
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}
