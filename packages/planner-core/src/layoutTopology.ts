import type { FacilityTemplate } from '@ed-finder/api-client/types';
import {
  architectPrimaryPortFlagLabel,
  architectSlotCountLabel,
  architectSurveyLabel,
  normalizeArchitectObservation,
  type ArchitectObservationInput,
} from './architectObservation';
import { bodyDisplayName, type BodyGroup } from './buildPlanLayout';
import { formatLocation } from './formatters';

export type TopologyTone = 'default' | 'good' | 'warn';

export interface TopologyChip {
  key: string;
  label: string;
  tone: TopologyTone;
}

export interface LayoutTopologyReadout {
  bodyLabel: string;
  bodyState: 'known' | 'unknown' | 'unassigned';
  orbitalPlanned: number;
  groundPlanned: number;
  unknownLocationPlanned: number;
  groundCapabilityLabel: string;
  groundCapabilityTone: TopologyTone;
  architectSurveyLabel: string;
  orbitalSlotLabel: string;
  groundSlotLabel: string;
  primaryPortContextLabel: string;
  primaryPortContextTone: TopologyTone;
  chips: TopologyChip[];
}

export function buildLayoutTopologyReadout(
  group: BodyGroup,
  architectInput?: ArchitectObservationInput | null,
): LayoutTopologyReadout {
  const status = normalizeArchitectObservation(architectInput);
  const counts = countPlacementLocations(group);
  const bodyState = getBodyState(group);
  const groundCapability = getGroundCapability(group);
  const primaryContext = getPrimaryPortContext(group, status.primaryPortFlag.state === 'observed'
    ? architectPrimaryPortFlagLabel(status)
    : null);

  const chips: TopologyChip[] = [
    {
      key: 'body-state',
      label: bodyState === 'known' ? 'Body: known' : bodyState === 'unknown' ? 'Body: unknown' : 'Body: unassigned',
      tone: bodyState === 'known' ? 'good' : 'warn',
    },
    { key: 'orbital-planned', label: `Orbital planned: ${counts.orbital}`, tone: counts.orbital > 0 ? 'good' : 'default' },
    { key: 'ground-planned', label: `Ground planned: ${counts.ground}`, tone: counts.ground > 0 ? groundCapability.tone : 'default' },
    { key: 'ground-capability', label: groundCapability.label, tone: groundCapability.tone },
    ...(counts.unknown > 0 ? [{ key: 'unknown-location', label: `Location unknown: ${counts.unknown}`, tone: 'warn' as const }] : []),
    { key: 'architect-survey', label: architectSurveyLabel(status), tone: status.surveyState === 'observed' ? 'good' : 'default' },
    { key: 'orbital-slots', label: architectSlotCountLabel('Orbital slots', status.orbitalSlotCount), tone: status.orbitalSlotCount == null ? 'default' : 'good' },
    { key: 'ground-slots', label: architectSlotCountLabel('Ground slots', status.groundSlotCount), tone: status.groundSlotCount == null ? 'default' : 'good' },
    { key: 'primary-context', label: primaryContext.label, tone: primaryContext.tone },
  ];

  return {
    bodyLabel: group.body ? bodyDisplayName(group.body) : bodyState === 'unknown' ? 'Unknown body reference' : 'Unassigned / needs body',
    bodyState,
    orbitalPlanned: counts.orbital,
    groundPlanned: counts.ground,
    unknownLocationPlanned: counts.unknown,
    groundCapabilityLabel: groundCapability.label,
    groundCapabilityTone: groundCapability.tone,
    architectSurveyLabel: architectSurveyLabel(status),
    orbitalSlotLabel: architectSlotCountLabel('Orbital slots', status.orbitalSlotCount),
    groundSlotLabel: architectSlotCountLabel('Ground slots', status.groundSlotCount),
    primaryPortContextLabel: primaryContext.label,
    primaryPortContextTone: primaryContext.tone,
    chips,
  };
}

function countPlacementLocations(group: BodyGroup): { orbital: number; ground: number; unknown: number } {
  return group.placements.reduce((counts, item) => {
    const location = normalizeTemplateLocation(item.template);
    if (location === 'orbital') counts.orbital += 1;
    else if (location === 'ground') counts.ground += 1;
    else counts.unknown += 1;
    return counts;
  }, { orbital: 0, ground: 0, unknown: 0 });
}

function normalizeTemplateLocation(template: FacilityTemplate | undefined): 'orbital' | 'ground' | 'unknown' {
  const value = template?.allowed_location?.toLowerCase() ?? '';
  if (value.includes('orbital')) return 'orbital';
  if (value.includes('surface') || value.includes('ground')) return 'ground';
  return 'unknown';
}

function getBodyState(group: BodyGroup): LayoutTopologyReadout['bodyState'] {
  if (group.body) return 'known';
  return group.placements.some((item) => item.hasUnknownBody) ? 'unknown' : 'unassigned';
}

function getGroundCapability(group: BodyGroup): { label: string; tone: TopologyTone } {
  if (!group.body) return { label: 'Surface capability: unknown', tone: 'warn' };
  if (group.body.is_water_world) return { label: 'Surface capability: review water world', tone: 'warn' };
  if (group.body.is_landable === false) return { label: 'Surface capability: not landable', tone: 'warn' };
  if (group.body.is_landable === true) return { label: 'Surface capability: landable', tone: 'good' };
  return { label: 'Surface capability: unknown', tone: 'default' };
}

function getPrimaryPortContext(group: BodyGroup, observedLabel: string | null): { label: string; tone: TopologyTone } {
  if (group.placements.some((item) => item.placement.is_primary_port)) {
    return { label: 'Primary-port plan: on this body', tone: 'good' };
  }
  if (observedLabel) return { label: observedLabel, tone: 'good' };
  return { label: 'Primary-port flag: unknown', tone: 'default' };
}

export function topologyPlacementLocationLabel(template: FacilityTemplate | undefined): string {
  if (!template) return 'Topology location: unknown';
  return `Topology location: ${formatLocation(template.allowed_location)}`;
}
