export type ArchitectSurveyState = 'not_observed' | 'observed';
export type ArchitectPrimaryPortFlagState = 'unknown' | 'observed';

export interface ArchitectPrimaryPortFlag {
  state: ArchitectPrimaryPortFlagState;
  bodyName?: string | null;
  slotLabel?: string | null;
}

export interface ArchitectObservationInput {
  surveyState?: ArchitectSurveyState | null;
  orbitalSlotCount?: number | null;
  groundSlotCount?: number | null;
  primaryPortFlag?: ArchitectPrimaryPortFlag | null;
}

export interface ArchitectObservationStatus {
  surveyState: ArchitectSurveyState;
  orbitalSlotCount: number | null;
  groundSlotCount: number | null;
  primaryPortFlag: ArchitectPrimaryPortFlag;
}

export function normalizeArchitectObservation(input?: ArchitectObservationInput | null): ArchitectObservationStatus {
  return {
    surveyState: input?.surveyState === 'observed' ? 'observed' : 'not_observed',
    orbitalSlotCount: normalizeSlotCount(input?.orbitalSlotCount),
    groundSlotCount: normalizeSlotCount(input?.groundSlotCount),
    primaryPortFlag: normalizePrimaryPortFlag(input?.primaryPortFlag),
  };
}

export function architectSurveyLabel(status: ArchitectObservationStatus): string {
  return status.surveyState === 'observed' ? 'Architect survey: observed' : 'Architect survey: not observed';
}

export function architectPrimaryPortFlagLabel(status: ArchitectObservationStatus): string {
  if (status.primaryPortFlag.state !== 'observed') return 'Primary-port flag: unknown';

  const location = [status.primaryPortFlag.bodyName, status.primaryPortFlag.slotLabel]
    .filter((part): part is string => Boolean(part && part.trim()))
    .join(' / ');

  return location ? `Primary-port flag: observed on ${location}` : 'Primary-port flag: observed';
}

export function architectSlotCountLabel(label: 'Orbital slots' | 'Ground slots', count: number | null): string {
  return `${label}: ${count == null ? 'unknown' : count}`;
}

export function architectObservationGuidance(
  status: ArchitectObservationStatus,
  options: { showPrimaryPortPlacementReminder?: boolean } = {},
): string[] {
  const guidance = [
    'Check System Map > Architect Mode before final major station placement.',
    'Primary-port location is placement guidance, not a Build Point source.',
  ];

  if (status.primaryPortFlag.state === 'observed' || options.showPrimaryPortPlacementReminder) {
    guidance.push('If the flagged primary-port slot is inconvenient, consider an outpost there and place the main station elsewhere.');
  } else {
    guidance.push('Primary-port flag is unknown until observed in Architect Mode.');
  }

  return guidance;
}

function normalizeSlotCount(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null;
}

function normalizePrimaryPortFlag(value: ArchitectPrimaryPortFlag | null | undefined): ArchitectPrimaryPortFlag {
  if (value?.state !== 'observed') return { state: 'unknown' };

  return {
    state: 'observed',
    bodyName: value.bodyName?.trim() || null,
    slotLabel: value.slotLabel?.trim() || null,
  };
}
