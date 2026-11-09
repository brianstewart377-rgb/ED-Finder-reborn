import type { PlanStepV1 } from '$lib/api';

export const resultLabel: Record<PlanStepV1['result'], string> = {
  OK: 'OK',
  BLOCKED: 'Blocked',
  NEEDS_INFO: 'Needs info',
  COUNTED: 'Counted',
  IGNORED: 'Ignored',
};

export const reasonLabels: Readonly<Record<string, string>> = {
  INSUFFICIENT_T2:
    'There are not enough Tier 2 construction points for this step.',
  INSUFFICIENT_T3:
    'There are not enough Tier 3 construction points for this step.',
  PREREQUISITE_MISSING:
    'A required facility must be completed earlier in the build order.',
  NOT_PLACED: 'Choose a body or belt for this build.',
  SLOTS_UNKNOWN: 'Enter the available slot count for this location.',
  NO_FREE_SLOT: 'No slot of the required kind is free at this location.',
  NOT_LANDABLE: 'This surface facility needs a landable body.',
  LANDABLE_UNKNOWN: 'It is not yet known whether this body is landable.',
  NOT_RINGED: 'This facility needs a location with rings or an asteroid belt.',
  RINGED_UNKNOWN: 'It is not yet known whether this location has rings.',
};

export function previewReasonLabel(reason: string): string {
  return Object.hasOwn(reasonLabels, reason) ? reasonLabels[reason] : reason;
}
